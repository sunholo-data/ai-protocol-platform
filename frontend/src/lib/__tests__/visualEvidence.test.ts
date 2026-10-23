import { describe, it, expect, vi } from 'vitest';
import { captureSvg, sourceDigest } from '../visualEvidence';
const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 200"><text>Test</text></svg>';
describe('visual evidence boundary', () => {
  it('binds to the exact trimmed source rather than its intended content', async () => {
    expect(await sourceDigest('  '+svg+'\n')).toBe(await sourceDigest(svg));
    expect(await sourceDigest(svg.replace('Test','Other'))).not.toBe(await sourceDigest(svg));
  });
  it('refuses stale source before any image is decoded', async () => {
    await expect(captureSvg(svg,svg,()=>false)).rejects.toThrow(/changed/);
  });
  it('refuses an aborted request', async () => {
    const controller = new AbortController(); controller.abort();
    await expect(captureSvg(svg,svg,()=>true,controller.signal)).rejects.toThrow(/cancel/i);
  });
  it.each([
    '<svg><script>alert(1)</script></svg>',
    '<svg><image href="https://example.org/a.png"/></svg>',
    '<svg><style>@import "https://example.org/a.css";</style></svg>',
    '<svg><rect style="fill:url(https://example.org/a.svg)"/></svg>',
    '<svg><foreignObject>bad</foreignObject></svg>',
    '<svg onload="alert(1)"></svg>',
  ])('refuses active or externally dependent capture content', async (unsafe) => {
    await expect(captureSvg(svg,unsafe,()=>true)).rejects.toThrow(/self-contained/);
  });
  it('refuses invalid XML instead of creating apparently healthy evidence', async () => {
    await expect(captureSvg(svg,'<svg',()=>true)).rejects.toThrow(/valid SVG/);
  });
  it('checks freshness after asynchronous hashing', async () => {
    const current=vi.fn().mockReturnValueOnce(true).mockReturnValue(false);
    await expect(captureSvg(svg,svg,current)).rejects.toThrow(/changed/);
  });
});
