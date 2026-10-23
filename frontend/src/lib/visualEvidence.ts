/** Bounded isolated SVG rasterisation. This does NOT capture the live workspace. */
export interface SvgCapture {
  sourceRevision: string;
  pngBase64: string;
  width: number;
  height: number;
  captureMode: 'client_supplied_isolated_render';
}

export async function sourceDigest(source: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(source.trim()));
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
}

/** Input must already be sanitised by SVGBlock. Additional restrictions prevent
 * isolated capture from silently omitting externally dependent content. */
export async function captureSvg(
  source: string,
  cleanSvg: string,
  isCurrent: () => boolean,
  signal?: AbortSignal,
): Promise<SvgCapture> {
  const check = () => {
    if (signal?.aborted) throw new Error('Visual inspection cancelled.');
    if (!isCurrent()) throw new Error('The diagram changed. Inspect the new revision.');
  };
  check();
  if (source.length > 250_000 || cleanSvg.length > 250_000) {
    throw new Error('This diagram is too large to inspect.');
  }
  const doc = new DOMParser().parseFromString(cleanSvg, 'image/svg+xml');
  const root = doc.documentElement;
  if (root.localName !== 'svg' || doc.querySelector('parsererror')) {
    throw new Error('Visual inspection requires valid SVG.');
  }
  const unsafe = Array.from(doc.querySelectorAll('*')).some(el =>
    ['script', 'foreignobject', 'image', 'use', 'animate', 'animatetransform', 'animatemotion', 'set'].includes(el.localName.toLowerCase()) ||
    Array.from(el.attributes).some(a => /^on/i.test(a.name) || /href$/i.test(a.name))
  );
  if (unsafe || /url\s*\(|@import|@font-face|@keyframes|animation\s*[:\-]|transition\s*[:\-]/i.test(cleanSvg)) {
    throw new Error('Inspection currently requires a static, self-contained SVG without external resources.');
  }
  const sourceRevision = await sourceDigest(source);
  check();
  const viewBox = (root.getAttribute('viewBox') ?? '').trim().split(/[\s,]+/).map(Number);
  const w = viewBox.length === 4 ? viewBox[2] : parseFloat(root.getAttribute('width') ?? '');
  const h = viewBox.length === 4 ? viewBox[3] : parseFloat(root.getAttribute('height') ?? '');
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0 || w/h > 20 || h/w > 20) {
    throw new Error('The diagram needs finite dimensions or a viewBox before inspection.');
  }
  const scale = Math.min(2, 1600/w, 1600/h);
  const width = Math.max(1, Math.round(w*scale));
  const height = Math.max(1, Math.round(h*scale));
  if (!root.getAttribute('viewBox')) root.setAttribute('viewBox', `0 0 ${w} ${h}`);
  root.setAttribute('width', String(width));
  root.setAttribute('height', String(height));
  root.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  const blob = new Blob([new XMLSerializer().serializeToString(root)], {type:'image/svg+xml'});
  const url = URL.createObjectURL(blob);
  const image = new Image();
  try {
    await new Promise<void>((resolve, reject) => {
      const finish = (error?: Error) => {
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
        image.onload = null; image.onerror = null;
        if (error) reject(error); else resolve();
      };
      const abort = () => finish(new Error('Visual inspection cancelled.'));
      const timer = setTimeout(() => finish(new Error('The diagram did not render within 15 seconds.')), 15_000);
      image.onload = () => finish();
      image.onerror = () => finish(new Error('The browser could not render this diagram.'));
      signal?.addEventListener('abort', abort, {once:true});
      if (signal?.aborted) abort(); else image.src = url;
    });
    check();
    const canvas = document.createElement('canvas');
    canvas.width = width; canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Image capture is unavailable in this browser.');
    // Explicit white background is part of the capture contract, not the live theme.
    ctx.fillStyle = '#ffffff'; ctx.fillRect(0,0,width,height);
    ctx.drawImage(image,0,0,width,height);
    const data = canvas.toDataURL('image/png');
    if (!data.startsWith('data:image/png;base64,')) throw new Error('The browser produced no PNG image.');
    const pngBase64 = data.substring(data.indexOf(',')+1);
    if (pngBase64.length > 2_600_000) throw new Error('The rendered image is too large to inspect.');
    check();
    return {sourceRevision,pngBase64,width,height,captureMode:'client_supplied_isolated_render'};
  } finally {
    image.src = '';
    URL.revokeObjectURL(url);
  }
}
