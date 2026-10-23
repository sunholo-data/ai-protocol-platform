import {describe,it,expect,vi,beforeEach} from 'vitest';
import {render,screen,fireEvent,waitFor} from '@testing-library/react';
import {VisualEvidenceControl,VisualEvidenceContext} from '../VisualEvidenceControl';
import {fetchWithAuth} from '@/lib/apiClient';
import {captureSvg,sourceDigest} from '@/lib/visualEvidence';
vi.mock('@/lib/apiClient',()=>({fetchWithAuth:vi.fn()}));
vi.mock('@/lib/visualEvidence',()=>({captureSvg:vi.fn(),sourceDigest:vi.fn()}));
const source='<svg>one</svg>';
const revision='a'.repeat(64);
const result={status:'inspected',source_revision:revision,findings:{observations:['The label is clipped.'],limitations:['Isolated render only.']}};
function control(value=source,onChatMessage=vi.fn()) {
 return <VisualEvidenceContext.Provider value={{sessionId:'session',onChatMessage}}><VisualEvidenceControl source={value} cleanSvg={value}/></VisualEvidenceContext.Provider>;
}
beforeEach(()=>{vi.clearAllMocks();vi.stubEnv('NEXT_PUBLIC_VISUAL_EVIDENCE_ENABLED','true');
 vi.mocked(captureSvg).mockResolvedValue({sourceRevision:revision,pngBase64:'bytes',width:10,height:10,captureMode:'client_supplied_isolated_render'});
 vi.mocked(sourceDigest).mockResolvedValue(revision);
});
describe('VisualEvidenceControl',()=>{
 it('does not expose the control by default',()=>{vi.stubEnv('NEXT_PUBLIC_VISUAL_EVIDENCE_ENABLED','false');render(control());expect(screen.queryByText('Inspect diagram')).toBeNull();});
 it('requires a session',()=>{render(<VisualEvidenceControl source={source} cleanSvg={source}/>);expect(screen.queryByText('Inspect diagram')).toBeNull();});
 it('shows findings but sends nothing to the copilot until explicitly requested',async()=>{
  vi.mocked(fetchWithAuth).mockResolvedValue({ok:true,json:async()=>result} as Response);
  const chat=vi.fn();render(control(source,chat));fireEvent.click(screen.getByText('Inspect diagram'));
  await screen.findByText('The label is clipped.');expect(chat).not.toHaveBeenCalled();
  expect(screen.getByRole('status').textContent).toContain('does not verify the live workspace');
  fireEvent.click(screen.getByText('Ask copilot to revise'));expect(chat).toHaveBeenCalledOnce();
  expect(chat.mock.calls[0][0]).toContain('Do not save or publish');
 });
 it('refuses a mismatched assessment revision',async()=>{
  vi.mocked(fetchWithAuth).mockResolvedValue({ok:true,json:async()=>({...result,source_revision:'b'.repeat(64)})} as Response);
  render(control());fireEvent.click(screen.getByText('Inspect diagram'));await screen.findByText('The service returned no matching visual assessment.');
  expect(screen.queryByText('Ask copilot to revise')).toBeNull();
 });
 it('does not display an old response after a source update',async()=>{
  let resolve!:(value:Response)=>void;
  vi.mocked(fetchWithAuth).mockReturnValue(new Promise(r=>{resolve=r;}));
  const view=render(control());fireEvent.click(screen.getByText('Inspect diagram'));
  await waitFor(()=>expect(fetchWithAuth).toHaveBeenCalledOnce());
  view.rerender(control('<svg>new</svg>'));
  resolve({ok:true,json:async()=>result} as Response);
  await waitFor(()=>expect(screen.queryByText('The label is clipped.')).toBeNull());
 });
 it('renders access denial without echoing server internals',async()=>{
  vi.mocked(fetchWithAuth).mockResolvedValue({ok:false,status:403,json:async()=>({detail:'secret'})} as Response);
  render(control());fireEvent.click(screen.getByText('Inspect diagram'));
  await screen.findByText('Visual inspection is not enabled for this account or diagram.');expect(screen.queryByText('secret')).toBeNull();
 });
});
