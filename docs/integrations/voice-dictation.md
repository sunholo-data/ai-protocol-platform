# Dictation and automatic spoken replies

The platform supports turn-based voice: record a short message, stop, review
the transcript in the composer, send it through the normal skill, then optionally
hear the completed reply. It uses ordinary Gemini transcription requests and the
existing text-to-speech providers. No Gemini Live connection is required.

## Enable dictation

Dictation is disabled by default. Configure the deployment:

```dotenv
VOICE_STT_PROVIDER=gemini
VOICE_STT_MODEL=lite
NEXT_PUBLIC_ENABLE_READ_ALOUD=true
```

`VOICE_STT_MODEL` is a Gemini model registry ID or tier. Calls use the platform's
residency-aware retry/fallback layer, so existing model residency settings apply.
The read-aloud flag controls output; dictation can be enabled independently.
Deployment read-aloud defaults can be set with `VOICE_TTS_PROVIDER`,
`VOICE_TTS_VOICE` and `VOICE_TTS_LANGUAGE`, for example `gcp_chirp3hd`,
`en-GB-Chirp3-HD-Aoede` and `en-GB`. Set the voice and language together.
Per-skill persona settings take precedence; deployments that omit these
variables keep the existing platform fallback.
Microphone capture requires HTTPS or localhost and browser permission. Tailnet
deployments must serve the AudioWorklet file `/pcm-capture-worklet.js` too.

Override dictation per skill in its existing persona voice block:

```yaml
persona:
  voice:
    sttProvider: gemini
    sttModel: lite
    voiceInputEnabled: true
    language: en
```

`voiceInputEnabled: false` disables input even when enabled deployment-wide.
`sttProvider: disabled` also opts a skill out. Output keeps its existing
`enabled`, `ttsProvider`, `ttsVoice`, `voicePrompt` and `rate` settings. Dictation
preserves the language actually spoken; the language setting is only a hint.

## Behaviour and API

- **Talk to type** starts the microphone and shows an input level meter. **Stop
  dictation** transcribes the clip into the existing draft without sending it.
  The recorder stops automatically after 60 seconds.
- **Cancel dictation** discards the recording/request. Leaving the thread releases
  the microphone, aborts transcription, and ignores late results.
- **Auto-read** is off by default and persists as a browser preference. It reads
  only new replies confirmed complete by `RUN_FINISHED`, never loaded history,
  stopped replies or failed turns. Starting a new message or dictation stops
  playback. Browsers that block automatic audio still allow manual read-aloud.
- Existing chat tools, document context, A2UI surfaces and handoffs use the same
  text-message path as typed input.

`GET /api/voice/config?skill_id=<id-or-slug>` returns independent `tts` and `stt`
configuration. Clients should hide input when `stt.enabled` is false.

`POST /api/voice/stt/transcribe` accepts authenticated multipart form fields:
`audio` (mono 16-bit PCM WAV), `skillId` (optional ID or slug), and `lang`
(optional language hint, default `auto`). It returns `{"text":"..."}`. Explicit
skill references must be accessible to the caller. The backend validates actual
WAV duration (60 seconds, with one second of capture tolerance) and limits uploads
to 18 MiB. Disabled transcription returns 503; invalid clips return 400;
oversized clips return 413; inaccessible skill references return 404.

The dictation path processes audio inline and does not save clips or transcripts
to platform storage. The transcript becomes normal conversation history only
when the user sends it. Existing model-provider data handling policies still
apply. Trace spans record provider, duration, language and transcript length,
without recording audio or transcript contents.

## Port provenance

Ported from AIPLA's `cphu-aipla-app` voice implementation at `16f66286`:
`VoiceComposerControls`, `audioCapture`, `pcm-capture-worklet`,
`RecordingLevelMeter`, the transcription provider/route, and auto-read controls.
The generic port adds pending-permission cleanup, shared preference updates,
server-confirmed reply completion, bounded uploads and the platform resilience
layer. Classroom access rules, retained lesson recordings, research transcripts,
class/persona management and teaching prompts remain downstream in AIPLA.

Repo routing: shared code belongs in `platform-source`, is eligible for the
public template export, and reaches downstream deployments through upstream
merges. Deployment-specific provider settings remain in each fork.
