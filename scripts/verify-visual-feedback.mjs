#!/usr/bin/env node
/** Local synthetic feasibility probe. No stored profile, tokens, downloads, or polyfills. */
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
let browser;
let server;
const report = { status: 'failed', scope: 'isolated local browser; synthetic SVG only', checks: {}, limitations: ['Authenticated app journey, production tool registration, copilot image delivery and model visual understanding are not verified by this synthetic probe.'] };
try {
  const { values } = parseArgs({ options: {
    mode: { type: 'string', default: 'full' }, url: { type: 'string' }, output: { type: 'string' },
    'experimental-webmcp': { type: 'boolean', default: false },
  } });
  if (!['full', 'capture', 'inspect'].includes(values.mode)) throw new Error('mode must be full, capture, or inspect');
  let target;
  if (values.url) {
    target = new URL(values.url);
    if (target.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]'].includes(target.hostname)
      || !target.port || target.username || target.password || target.search || target.hash) {
      throw new Error('Only explicit local HTTP URLs with a port and no credentials/query are supported');
    }
  }
  let chromium;
  try { ({ chromium } = require('playwright')); }
  catch { throw new Error('Playwright unavailable. Use an approved installed playwright package (NODE_PATH is supported); this command does not download dependencies.'); }
  const frontendRequire = createRequire(resolve(root, 'frontend/package.json'));
  const fixture = name => readFile(resolve(root, `tests/fixtures/visual-feedback/${name}.svg`), 'utf8');
  if (values.mode !== 'inspect') {
    const ts = frontendRequire('typescript');
    const source = await readFile(resolve(root, 'frontend/src/lib/visualEvidence.ts'), 'utf8');
    const module = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
    const workspaceSource = await readFile(resolve(root, 'frontend/src/lib/webmcp/workspaceTools.ts'), 'utf8');
    const workspaceModule = ts.transpileModule(workspaceSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
    server = createServer((req, res) => {
      res.setHeader('Cache-Control', 'no-store');
      if (req.url === '/helper.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(module); }
      else if (req.url === '/workspace.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(workspaceModule); }
      else { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><title>Synthetic visual evidence probe</title><script type="module">import * as helper from "/helper.js"; window.visualHelper = helper; import * as workspace from "/workspace.js"; window.workspaceHelper = workspace;</script>'); }
    });
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  }
  const flags = values['experimental-webmcp'] ? ['--enable-features=WebMCPTesting,DevToolsWebMCPSupport'] : [];
  report.experimentalWebMCP = values['experimental-webmcp'];
  report.browserFlags = flags;
  browser = await chromium.launch({ channel: 'chrome', headless: true, args: flags });
  report.browser = browser.version();
  const context = await browser.newContext({ serviceWorkers: 'block' });
  // Even redirects/subresources from an explicitly local page cannot contact external origins.
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    const allowed = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
    return allowed ? route.continue() : route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const syntheticUrl = server && `http://127.0.0.1:${server.address().port}`;
  if (values.mode !== 'inspect') {
    await page.goto(syntheticUrl);
    await page.waitForFunction(() => !!window.visualHelper);
    const sources = { clipped: await fixture('clipped-label'), corrected: await fixture('correct-label') };
    const evidence = await page.evaluate(async sources => {
      const h = window.visualHelper;
      const clipped = await h.captureSvg(sources.clipped, sources.clipped, () => true);
      const corrected = await h.captureSvg(sources.corrected, sources.corrected, () => true);
      let staleRefused = false;
      try { await h.captureSvg(sources.clipped, sources.clipped, () => false); }
      catch { staleRefused = true; }
      const pixelsDiffer = clipped.pngBase64 !== corrected.pngBase64;
      const revisionsDiffer = clipped.sourceRevision !== corrected.sourceRevision;
      const pngValid = [clipped, corrected].every(item => item.pngBase64.startsWith('iVBOR') && item.width > 0 && item.height > 0);
      return {
        checks: { passed: staleRefused && pixelsDiffer && revisionsDiffer && pngValid,
          staleRefused, pixelsDiffer, revisionsDiffer, pngValid,
          captureMode: corrected.captureMode, width: corrected.width, height: corrected.height },
        revisions: {clipped: clipped.sourceRevision, corrected: corrected.sourceRevision},
        images: { clipped: clipped.pngBase64, corrected: corrected.pngBase64 },
      };
    }, sources);
    report.checks.capture = evidence.checks;
    report.syntheticCapturePairs = Object.entries(evidence.images).map(([name,data]) => evidence.revisions[name]+':'+createHash('sha256').update(Buffer.from(data,'base64')).digest('hex'));
    if (!evidence.checks.passed) throw new Error('Synthetic capture checks failed');
    if (values.output) {
      await mkdir(values.output, { recursive: true });
      for (const [name, data] of Object.entries(evidence.images)) {
        await writeFile(resolve(values.output, `${name}.png`), Buffer.from(data, 'base64'), { mode: 0o600 });
      }
      report.evidenceDirectory = resolve(values.output);
    }
  }
  if (values.mode !== 'capture') {
    if (target) await page.goto(target.href);
    else if (!syntheticUrl) throw new Error('--url is required in inspect mode');
    report.checks.native = await page.evaluate(async inspect => {
      const native = document.modelContext;
      const result = { secureContext: window.isSecureContext, available: !!native?.registerTool, registered: false, invoked: false };
      if (inspect || !result.available) return result;
      const parse = output => {
        let value = typeof output === 'string' ? JSON.parse(output) : output;
        if (value?.content?.[0]?.text) value = JSON.parse(value.content[0].text);
        return value;
      };
      let state = {identity:'synthetic-user:session',running:false,surfaces:[{id:'private-synthetic-surface',registered:true,mounted:true,visible:true,revision:1}]};
      let focused = false;
      const stop = window.workspaceHelper.registerWorkspaceTools(native,()=>state,()=>{focused=true;});
      try {
        const tools = await native.getTools();
        const call = async(name,args={}) => {
          const tool=tools.find(t=>t.name===name);
          if(!tool)throw new Error('Application tool not registered: '+name);
          return parse(await native.executeTool(tool,JSON.stringify(args)));
        };
        result.registered = ['get_workspace_state','inspect_surface','focus_asset'].every(name=>tools.some(t=>t.name===name));
        const workspace=await call('get_workspace_state');
        const input={contextRef:workspace.contextRef,surfaceRef:workspace.surfaces[0].surfaceRef};
        const surface=await call('inspect_surface',input);
        await call('focus_asset',input);
        state={...state,running:true};
        const blocked=await call('focus_asset',input);
        state={...state,identity:'different-session'};
        const stale=await call('inspect_surface',input);
        result.invoked=surface.visualInspection==='not_performed' && focused && blocked.error==='run_in_progress' && stale.error==='stale_context';
        result.noPrivateIds=!JSON.stringify(workspace).includes('private-synthetic-surface');
      } finally {stop();}
      result.revoked=!(await native.getTools()).some(t=>['get_workspace_state','inspect_surface','focus_asset'].includes(t.name));
      result.invoked=result.invoked && result.noPrivateIds && result.revoked;
      return result;
    }, values.mode === 'inspect');
    const native = report.checks.native;
    report.status = native.available && (values.mode === 'inspect' || native.invoked) ? 'passed' : 'partial';
    if (report.status === 'partial') report.limitation = 'Native WebMCP registration/invocation unavailable in this browser. No shim or direct handler call substitutes for native verification.';
  } else {
    report.status = 'passed';
    report.limitation = 'Capture-only result; native WebMCP, authenticated app journey, copilot image delivery and model visual understanding remain unverified.';
  }
} catch (error) {
  report.error = error.message;
} finally {
  await browser?.close();
  if (server) await new Promise(resolve => server.close(resolve));
}
console.log(JSON.stringify(report, null, 2));
process.exitCode = report.status === 'passed' ? 0 : report.status === 'partial' ? 2 : 1;
