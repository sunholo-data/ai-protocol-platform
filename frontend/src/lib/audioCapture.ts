/** Browser microphone capture ported from AIPLA. AudioContext performs resampling;
 * the worklet emits PCM, packaged as a mono WAV for one-shot dictation. */

export interface RecordingResult {
  /** WAV (PCM 16-bit, 16 kHz, mono). */
  blob: Blob;
  mimeType: string;
  durationMs: number;
}

/** Preferred AudioContext rate; the browser resamples the microphone input.
 * The WAV header always records the actual context rate, including fallback. */
export const CAPTURE_RATE = 16000;

const WORKLET_PATH = "/pcm-capture-worklet.js";

export function isAudioCaptureSupported(): boolean {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) return false;
  const Ctx =
    typeof window !== "undefined"
      ? window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      : undefined;
  return typeof Ctx !== "undefined";
}

/**
 * Build a WAV (RIFF) blob from accumulated little-endian Int16 PCM chunks.
 * Mono, 16-bit. WAV is self-describing (the header carries the sample rate),
 * so the backend can read the rate without us threading it separately.
 */
export function encodeWav(chunks: ArrayBuffer[], sampleRate: number): Blob {
  let dataLen = 0;
  for (const c of chunks) dataLen += c.byteLength;
  const out = new Uint8Array(44 + dataLen);
  const view = new DataView(out.buffer);
  const writeStr = (off: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + dataLen, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true); // PCM fmt chunk size
  view.setUint16(20, 1, true); // format = PCM
  view.setUint16(22, 1, true); // channels = mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate = rate * channels * bytesPerSample
  view.setUint16(32, 2, true); // block align = channels * bytesPerSample
  view.setUint16(34, 16, true); // bits per sample
  writeStr(36, "data");
  view.setUint32(40, dataLen, true);
  let off = 44;
  for (const c of chunks) {
    out.set(new Uint8Array(c), off);
    off += c.byteLength;
  }
  return new Blob([out], { type: "audio/wav" });
}

function defaultAudioContext(): AudioContext {
  const Ctx =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  // Run the context at 16 kHz so the BROWSER's (high-quality) resampler does the
  // downsampling from the 44.1/48 kHz hardware, and the worklet just passes it
  // through. This gets clean 16 kHz two ways at once:
  //   - no aliasing (vs our old nearest-neighbour JS decimation that garbled
  //     speech), and
  //   - compact enough for an inline dictation upload.
  // The AudioContext sampleRate option IS honoured (unlike the getUserMedia
  // constraint); fall back to the native rate if a browser rejects it.
  try {
    return new Ctx({ sampleRate: 16000 });
  } catch {
    return new Ctx();
  }
}

export interface RecorderDeps {
  getUserMedia?: (c: MediaStreamConstraints) => Promise<MediaStream>;
  createAudioContext?: () => AudioContext;
  now?: () => number;
}

/**
 * One mic → PCM worklet session. Holds the AudioContext, mic stream, analyser
 * (for the level VFX) and the running buffer of Int16 PCM chunks. `take()`
 * drains the buffer before stopping the microphone without
 * tearing down the mic; `close()` releases everything.
 */
class PcmCaptureSession {
  private _ctx: AudioContext | null = null;
  private _stream: MediaStream | null = null;
  private _node: AudioWorkletNode | null = null;
  private _analyser: AnalyserNode | null = null;
  private _levelBuf: Uint8Array<ArrayBuffer> | null = null;
  private _chunks: ArrayBuffer[] = [];
  private _closed = false;
  // The AudioContext's native rate, captured at start() and written into the
  // WAV header so the backend (and STT) know the true rate.
  private _rate = CAPTURE_RATE;
  private readonly _getUserMedia: (c: MediaStreamConstraints) => Promise<MediaStream>;
  private readonly _createCtx: () => AudioContext;

  constructor(deps: RecorderDeps = {}) {
    this._getUserMedia = deps.getUserMedia ?? ((c) => navigator.mediaDevices.getUserMedia(c));
    this._createCtx = deps.createAudioContext ?? defaultAudioContext;
  }

  get active(): boolean {
    return this._node !== null;
  }

  async start(): Promise<void> {
    if (this._node) return;
    const stream = await this._getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    if (this._closed) {
      stream.getTracks().forEach((track) => track.stop());
      throw new Error("Recording cancelled");
    }
    this._stream = stream;
    const ctx = this._createCtx();
    this._ctx = ctx;
    this._rate = Math.round(ctx.sampleRate) || CAPTURE_RATE;
    if (ctx.state === "suspended") await ctx.resume();
    await ctx.audioWorklet.addModule(WORKLET_PATH);

    if (this._closed) throw new Error("Recording cancelled");
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    this._analyser = analyser;
    this._levelBuf = new Uint8Array(analyser.frequencyBinCount);
    source.connect(analyser);

    // No processorOptions — the worklet captures at the context's native rate.
    const node = new AudioWorkletNode(ctx, "pcm-capture");
    node.port.onmessage = (e: MessageEvent) => {
      if (e.data?.type === "pcm-chunk") this._chunks.push(e.data.pcmData as ArrayBuffer);
    };
    source.connect(node);
    this._node = node;
  }

  /** Drain the buffered PCM as a WAV blob (leaves the mic running). */
  take(): Blob {
    const chunks = this._chunks;
    this._chunks = [];
    return encodeWav(chunks, this._rate);
  }

  /** Live RMS input level 0..1 for a recording VFX. 0 when idle. */
  level(): number {
    if (!this._analyser || !this._levelBuf) return 0;
    this._analyser.getByteTimeDomainData(this._levelBuf);
    let sum = 0;
    for (let i = 0; i < this._levelBuf.length; i++) {
      const v = (this._levelBuf[i] - 128) / 128;
      sum += v * v;
    }
    return Math.min(1, Math.sqrt(sum / this._levelBuf.length) * 2.5);
  }

  close(): void {
    this._closed = true;
    try {
      this._node?.port.postMessage({ command: "stop" });
    } catch {
      /* port already closed */
    }
    try {
      this._node?.disconnect();
      this._analyser?.disconnect();
    } catch {
      /* nodes already detached */
    }
    this._stream?.getTracks().forEach((t) => t.stop());
    void this._ctx?.close().catch(() => {});
    this._node = null;
    this._analyser = null;
    this._levelBuf = null;
    this._stream = null;
    this._ctx = null;
    this._chunks = [];
  }
}

/**
 * One-shot recorder (dictation): `start()` then `stop()` -> a single WAV blob.
 * Releases the mic on stop/cancel.
 */
export class AudioRecorder {
  private _session: PcmCaptureSession | null = null;
  private _startedAt = 0;
  private readonly _deps: RecorderDeps;
  private readonly _now: () => number;

  constructor(deps: RecorderDeps = {}) {
    this._deps = deps;
    this._now = deps.now ?? (() => Date.now());
  }

  get recording(): boolean {
    return this._session !== null;
  }

  /** Live input level 0..1 for the VFX (0 when not recording). */
  getLevel(): number {
    return this._session?.level() ?? 0;
  }

  async start(): Promise<void> {
    if (this._session) return;
    const session = new PcmCaptureSession(this._deps);
    this._session = session;
    try {
      await session.start();
      this._startedAt = this._now();
    } catch (error) {
      session.close();
      if (this._session === session) this._session = null;
      throw error;
    }
  }

  async stop(): Promise<RecordingResult> {
    const session = this._session;
    if (!session) throw new Error("not recording");
    const durationMs = Math.max(0, this._now() - this._startedAt);
    const blob = session.take();
    session.close();
    this._session = null;
    return { blob, mimeType: "audio/wav", durationMs };
  }

  cancel(): void {
    this._session?.close();
    this._session = null;
  }
}
