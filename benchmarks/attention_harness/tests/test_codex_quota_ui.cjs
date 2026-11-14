const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const dialog=source.slice(source.indexOf('  function codexQuotaText('),source.indexOf('  // New run makes Agent runs only'));
const codexQuotaText=vm.runInNewContext(dialog+'\ncodexQuotaText',{Date:class extends Date {static now(){return new Date(2026,9,27,12).getTime();}}});

test('Codex quota dialog formats local reset date and warns at 70%',()=>{
  const resets_at=new Date(2026,9,28,12).getTime()/1000;
  assert.equal(codexQuotaText({used_percent:5,window_minutes:10080,resets_at}).text,
    'Codex quota: 5% of this week used · resets Wed 28 Oct');
  assert.equal(codexQuotaText({used_percent:69.9,window_minutes:10080,resets_at}).low,false);
  const low=codexQuotaText({used_percent:70,window_minutes:10080,resets_at});
  assert.equal(low.low,true);
  assert.match(low.text,/Running low this week/);
  assert.deepEqual({...codexQuotaText({available:false})},{text:'Quota unavailable',low:false});
  assert.match(source,/quotaLine\.hidden=form\.model_source\.value!=='codex'/);
  assert.match(source,/fetch\('\/api\/codex-quota'/);
  assert.match(source,/form\.model_source\.addEventListener\('change',showQuota\)/);
});


test('New run explains reset recovery and the specific last probe failure',()=>{
  const reset=codexQuotaText({used_percent:70,window_minutes:10080,resets_at:1});
  assert.match(reset.text,/weekly reset/);assert.match(reset.text,/one minimal Codex probe, charged to that run/);
  const failed=codexQuotaText({available:false,reason:'quota_unreadable_after_probe'});
  assert.match(failed.text,/still could not read the weekly quota/);
  assert.match(failed.text,/quota_unreadable_after_probe/);
  // Hosted Lab Codex asks its own server and displays the same error.
  assert.doesNotMatch(source,/if\(!hosted\)fetch\('\/api\/codex-quota'/);
  assert.doesNotMatch(source,/quotaLine.hidden=[^;]*\|\|hosted/);
});

test('Run page renders the specific failure and charged probe usage',()=>{
  const harness=fs.readFileSync(path.join(__dirname,'test_dev_workspace.cjs'),'utf8');
  const helper=harness.slice(harness.indexOf('function rendererAPI('),harness.indexOf("test('Current view keeps"))
    .replace('({runSettings,runOverview,executionViewer,requestPanel,resourceDetails,operatorHelpHistory,renderSidebar})',
      '({operatorOutcome,resourceDetails})');
  const {rendererAPI}=vm.runInNewContext(helper+'\n({rendererAPI})',{source,vm,URL,URLSearchParams});
  const snapshot={run:{status:'needs_review'},agent:{reason:'quota_unreadable_after_probe',quota:{error:'quota_unreadable_after_probe'}},attempts:[]};
  const api=rendererAPI(snapshot,false,{Lobby:{outcomeHtml:()=>''}});
  const html=api.operatorOutcome(snapshot);
  assert.match(html,/one minimal Codex probe still could not read the weekly quota/);
  assert.match(html,/quota_unreadable_after_probe/);
  assert.doesNotMatch(html,/stopped unexpectedly/);
  const notice=source.slice(source.indexOf('  function quotaProbeNotice('),source.indexOf('  function attemptCopy('));
  const quotaProbeNotice=vm.runInNewContext(notice+'\nquotaProbeNotice',{esc:String});
  snapshot.agent.quota.quota_probe={status:'completed',usage:{input_tokens:3000,cached_input_tokens:2000,output_tokens:1}};
  assert.match(quotaProbeNotice(snapshot),/3,001 tokens charged to this run/);
  assert.match(source,/\$\{quotaProbeNotice\(snapshot\)\}/);
});
