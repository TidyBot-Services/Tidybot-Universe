const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const fixture=path.join(__dirname,'fixtures/request_view_real_shape_20261019');
const help=JSON.parse(fs.readFileSync(path.join(fixture,'help.json'),'utf8'));
const advisorInput=JSON.parse(fs.readFileSync(path.join(fixture,'advisor-input.json'),'utf8'));
const block=source.slice(source.indexOf('  // Operator presentation:'),source.indexOf('  // End operator presentation.'));
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const {operatorEvalReview,operatorRequestView,operatorHelpHistory,devHelpResponder}=vm.runInNewContext(
  block+'\n({operatorEvalReview,operatorRequestView,operatorHelpHistory,devHelpResponder})',
  {DEBUG_VIEW:false,esc,humanDuration:seconds=>`${seconds}s`,selectedMomentDetails:()=>'',readableReply:reply=>esc(reply.content)});

test('Eval review uses the objective reason and a separate optional suggestion',()=>{
  const passed=operatorEvalReview({status:'completed',passed:true,reason:'Done <safely>',reference_suggestion:'Keep <watching>'},'attempt-1');
  assert.match(passed,/data-eval-review[^>]*data-attempt="attempt-1"/);
  assert.match(passed,/data-ai-review/);
  assert.match(passed,/data-eval-verdict[^>]*>Completed/);
  assert.match(passed,/data-eval-reason/);
  assert.match(passed,/data-eval-reference/);
  assert.match(passed,/Done &lt;safely&gt;/);
  assert.match(passed,/Keep &lt;watching&gt;/);
  assert.doesNotMatch(passed,/<safely>|<watching>/);
  const failed=operatorEvalReview({status:'completed',passed:false,reason:'Move failed',reference_suggestion:''},'attempt-2');
  assert.match(failed,/Not completed/);
  assert.match(failed,/Move failed/);
  assert.doesNotMatch(failed,/data-eval-reference|Reference suggestion/);
});

test('missing review and legacy v2 feedback are distinguished',()=>{
  const missing=operatorEvalReview(null,'attempt-3');
  assert.match(missing,/Not reviewed/);
  assert.doesNotMatch(missing,/data-eval-reason|data-eval-reference/);
  assert.match(operatorEvalReview({status:'failed',passed:false},'attempt-3'),/Not reviewed/);
  const legacy=operatorEvalReview({status:'completed',feedback:'old <feedback>'},'attempt-4');
  assert.match(legacy,/Legacy review/);
  assert.match(legacy,/data-eval-reason/);
  assert.match(legacy,/old &lt;feedback&gt;/);
  assert.doesNotMatch(legacy,/<feedback>|data-eval-reference/);
  assert.match(operatorEvalReview({status:'completed',passed:false,reason:'old feedback',feedback:'old feedback',legacy_review:true},'attempt-5'),/Legacy review/);
});

test('request panel renders only the supplied server checklist',()=>{
  assert.equal(help.provider,'human_proxy');
  assert.equal(Object.hasOwn(help,'request_view'),false);
  assert.equal(advisorInput.schema_version,'attentionbench.request-view.v1');
  assert.ok(advisorInput.frames.every(frame=>frame.view && frame.sha256 && !frame.data_url));
  const view={items:[{label:'Task',value:advisorInput.task.task_prompt},{label:'Question',value:advisorInput.question},
    {label:'Overview frames',value:String(advisorInput.frames.filter(frame=>frame.view==='overview').length)}],
    never_shown:'Never shown: simulator ground truth and the native success score'};
  const html=operatorRequestView(view,'help-1');
  assert.match(html,/data-request-view/);
  assert.equal((html.match(/data-request-view-item/g)||[]).length,3);
  assert.match(html,/Pick the object from the counter/);
  assert.match(html,/Overview frames<\/b> 2/);
  assert.match(html,/Never shown: simulator ground truth and the native success score/);
  assert.doesNotMatch(html,/data_url|sha256|recording_frames/);
  const thread=operatorHelpHistory({requests:[{request_id:'help-1',response_id:'reply-1',state:'answered',request_view:view}],
    responses:{'reply-1':{responder:devHelpResponder(help.provider),content:help.content}},attempts:[]});
  assert.equal((thread.match(/data-request-view(?:\s|>)/g)||[]).length,1);
  assert.equal((thread.match(/data-request-view-item/g)||[]).length,3);
  assert.match(thread,/Advisor \(simulated human\)/);
  assert.equal(operatorRequestView(null,'help-2'),'');
  assert.match(source,/request_view:version\.request_view/);
});

for(const [provider,label] of [['advisor','Advisor (simulated human)'],['human_proxy','Advisor (simulated human)'],['human','You']]) {
  test(`help provider ${provider} keeps the correct answerer label`,()=>{
    const responder=devHelpResponder(provider);
    const snapshot={requests:[{request_id:'help-1',response_id:'reply-1',state:'answered'}],
      responses:{'reply-1':{responder,content:'Try smaller steps'}},attempts:[]};
    const html=operatorHelpHistory(snapshot);
    assert.ok(html.includes(`<span class="op-who">${label}</span>`));
    assert.doesNotMatch(html,/data-request-view/);
  });
}

test('legacy request without a view has no request panel',()=>{
  const responder=devHelpResponder('advisor');
  const html=operatorHelpHistory({requests:[{request_id:'old',response_id:'old-reply',state:'answered'}],
    responses:{'old-reply':{responder,content:'Old answer'}},attempts:[]});
  assert.match(html,/Advisor \(simulated human\)/);
  assert.doesNotMatch(html,/data-request-view|data-request-view-item/);
});
