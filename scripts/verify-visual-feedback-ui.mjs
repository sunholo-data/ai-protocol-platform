#!/usr/bin/env node
/** Real SVGBlock/control browser test with an explicitly synthetic HTTP assessment.
 * Firebase and model inference are excluded; never use this as deployed journey proof. */
import { createRequire } from 'node:module';
import { readFile, mkdir } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { parseArgs } from 'node:util';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const frontendRequire = createRequire(resolve(root, 'frontend/package.json'));
const report = {status:'failed',scope:'actual SVGBlock and VisualEvidenceControl in isolated Chrome',
  limitations:['Synthetic HTTP assessment and fetchWithAuth boundary; no Firebase authentication or model inference.', 'Fixture CSS is minimal; this verifies UI behaviour, not production layout.'],checks:{}};
let browser, server, heldResponse;
try {
  const {values} = parseArgs({options:{output:{type:'string'}}});
  let chromium, esbuild;
  try { ({chromium}=require('playwright')); esbuild=frontendRequire('esbuild'); }
  catch { throw new Error('Approved installed Playwright and frontend esbuild are required; no dependencies are downloaded.'); }
  const clipped=await readFile(resolve(root,'tests/fixtures/visual-feedback/clipped-label.svg'),'utf8');
  const corrected=await readFile(resolve(root,'tests/fixtures/visual-feedback/correct-label.svg'),'utf8');
  const bundle=await esbuild.build({stdin:{contents:`
    import React, {useState} from 'react';
    import {createRoot} from 'react-dom/client';
    import {SVGBlock} from './src/components/chat/media/SVGBlock';
    import {VisualEvidenceContext} from './src/components/chat/media/VisualEvidenceControl';
    function Fixture() {
      const [source,setSource]=useState(${JSON.stringify(clipped)});
      const [message,setMessage]=useState('');
      return <main><h1>Synthetic visual feedback UI verification</h1>
        <button onClick={()=>setSource(${JSON.stringify(corrected)})}>Use corrected source</button>
        <VisualEvidenceContext.Provider value={{sessionId:'synthetic-session',onChatMessage:setMessage}}>
          <SVGBlock svgString={source}/>
        </VisualEvidenceContext.Provider><pre id="copilot-message">{message}</pre></main>;
    }
    createRoot(document.getElementById('root')).render(<Fixture/>);
  `,loader:'tsx',resolveDir:resolve(root,'frontend')},bundle:true,write:false,platform:'browser',format:'iife',jsx:'automatic',
    define:{'process.env.NEXT_PUBLIC_VISUAL_EVIDENCE_ENABLED':'"true"','process.env.NODE_ENV':'"development"'},
    plugins:[{name:'synthetic-auth-boundary',setup(build){
      build.onResolve({filter:/^@\/lib\/apiClient$/},()=>({path:'fixture-auth',namespace:'synthetic'}));
      build.onLoad({filter:/.*/,namespace:'synthetic'},()=>({contents:'export const fetchWithAuth = (url, options) => fetch(url, options);',loader:'js'}));
    }}],alias:{'@':resolve(root,'frontend/src')}});
  let responseMode='success';
  const requests=[];
  server=createServer(async(req,res)=>{
    res.setHeader('Cache-Control','no-store');
    if(req.url==='/bundle.js'){res.setHeader('Content-Type','text/javascript');res.end(bundle.outputFiles[0].text);return;}
    if(req.url==='/api/proxy/api/visual-evidence/inspect'){
      let body=''; for await(const chunk of req) body+=chunk;
      const data=JSON.parse(body); requests.push(data);
      res.setHeader('Content-Type','application/json');
      const result={status:'inspected',source_revision:data.source_revision,findings:{observations:['Synthetic finding: output label is clipped.'],limitations:['Synthetic assessment; no model inference.']}};
      if(responseMode==='deny'){res.statusCode=403;res.end('{}');return;}
      if(responseMode==='hold'){heldResponse=()=>res.end(JSON.stringify({...result,findings:{observations:['STALE SYNTHETIC FINDING'],limitations:[]}}));return;}
      res.end(JSON.stringify(result));return;
    }
    res.setHeader('Content-Type','text/html');res.end('<!doctype html><title>Visual feedback UI fixture</title><style>body{font:16px sans-serif;margin:24px;max-width:800px}button{padding:8px;margin:4px}.svg-container{display:block;width:100%}.svg-container svg{width:100%;height:auto}pre{white-space:pre-wrap}</style><div id="root"></div><script src="/bundle.js"></script>');
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  const origin=`http://127.0.0.1:${server.address().port}`;
  browser=await chromium.launch({channel:'chrome',headless:true});report.browser=browser.version();
  const context=await browser.newContext({serviceWorkers:'block'});
  await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort('blockedbyclient'));
  const page=await context.newPage();page.setDefaultTimeout(15000);
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(origin);
  await page.getByRole('button',{name:'Inspect diagram',exact:true}).click();
  await page.getByText('Synthetic finding: output label is clipped.',{exact:true}).waitFor();
  report.checks.realPngRequest=requests[0]?.png_base64.startsWith('iVBOR') && requests[0]?.source_revision===createHash('sha256').update(clipped.trim()).digest('hex');
  report.checks.matchingAssessmentVisible=true;
  report.checks.noAutomaticCopilotRequest=await page.locator('#copilot-message').textContent()==='';
  await page.getByRole('button',{name:'Ask copilot to revise',exact:true}).click();
  const message=await page.locator('#copilot-message').textContent();
  report.checks.explicitCopilotCallback=message.includes(requests[0].source_revision)&&message.includes('Synthetic finding: output label is clipped.');
  if(values.output){await mkdir(values.output,{recursive:true});await page.screenshot({path:resolve(values.output,'visual-feedback-ui.png'),fullPage:true});}
  responseMode='deny';
  await page.getByRole('button',{name:'Inspect diagram',exact:true}).click();
  await page.getByRole('status').filter({hasText:'Visual inspection is not enabled for this account or diagram.'}).waitFor();
  report.checks.denialVisible=true;
  responseMode='hold';
  const requestStarted=page.waitForRequest(request=>request.url().endsWith('/api/visual-evidence/inspect')&&request.method()==='POST');
  await page.getByRole('button',{name:'Inspect diagram',exact:true}).click();await requestStarted;
  await page.getByRole('status').filter({hasText:'Inspecting the rendered pixels'}).waitFor();
  const aborted=page.waitForEvent('requestfailed',{predicate:request=>request.url().endsWith('/api/visual-evidence/inspect')});
  await page.getByRole('button',{name:'Use corrected source'}).click();
  await aborted;
  heldResponse?.();heldResponse=null;
  await page.getByRole('button',{name:'Inspect diagram',exact:true}).waitFor();
  report.checks.sourceUpdateAbortsRequest=true;
  report.checks.staleAssessmentHidden=await page.getByText('STALE SYNTHETIC FINDING',{exact:true}).count()===0 && await page.getByRole('button',{name:'Ask copilot to revise',exact:true}).count()===0;
  report.checks.noBrowserErrors=errors.length===0;
  if(errors.length) report.browserErrors=errors;
  report.status=Object.values(report.checks).every(value=>value===true)?'passed':'failed';
}catch(error){report.error=error.message;}
finally{heldResponse?.();await browser?.close();if(server)await new Promise(resolve=>server.close(resolve));}
console.log(JSON.stringify(report,null,2));process.exitCode=report.status==='passed'?0:1;
