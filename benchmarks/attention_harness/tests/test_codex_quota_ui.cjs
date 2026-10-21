const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const dialog=source.slice(source.indexOf('  function codexQuotaText('),source.indexOf('  function runConfigDefaultModelMinutes('));
const codexQuotaText=vm.runInNewContext(dialog+'\ncodexQuotaText');

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
