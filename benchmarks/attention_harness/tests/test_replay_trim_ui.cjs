// Node-only checks of the trimmed-replay helpers in live.js; real browser verification is separate.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const block=source.slice(source.indexOf('  function replayTarget('),source.indexOf('  function operatorReplayTarget('));
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const saved=new Map();
const api=vm.runInNewContext(block+'\n({replayTarget,trimMode,trimUsable,attachTrim,tryReplayDue,skipText,operatorSkipRow,operatorTrimBar})',{
  esc,preference:(key,value)=>{if(value===undefined)return saved.get(key)??null;saved.set(key,value);},
  devFile:(session,uri)=>`/files/${uri}`,
  operatorTime:seconds=>{const v=Math.max(0,Math.round(Number(seconds)||0));return `${Math.floor(v/60)}:${String(v%60).padStart(2,'0')}`;}
});
const original={basis:'relative',event_clock:'sdk_completion',frame_trace_seconds:[1,3,50,52],frame_video_seconds:[0,2,49,51]};
const trim={schema:'attentionbench.replay-trim.v1',original_duration_seconds:51,trimmed_duration_seconds:5,skipped_seconds:46,
  replay_timing:{frame_trace_seconds:[1,3,50,52],frame_video_seconds:[0,2,3,4],frame_orig_video_seconds:[0,2,49,51]},
  breaks:[{orig_start:3,orig_end:49,skipped_seconds:46,trace_start:4,trace_end:50,trim_at:3,kind:'wait'}],
  videos:{overview:{uri:'trim/overview.mp4'},side:{uri:'trim/side.mp4'}}};
const traceWith=()=>({recording:{views:{external:{},side:{}},replay_timing:original,duration_seconds:51},
  evidence:[{kind:'advisor_replay',media_url:'/files/o.mp4',evidence_id:'m'},{kind:'advisor_replay_side',media_url:'/files/s.mp4',evidence_id:'m:side'},
    {kind:'recording_frame',media_url:'/files/f.png'}]});

test('a sidecar is usable only when its timing is consistent and every camera view has a trimmed video',()=>{
  assert.equal(api.trimUsable(trim,{external:{},side:{}}),true);
  assert.equal(api.trimUsable(trim,{external:{},top:{}}),false,'top has no trimmed video');
  assert.equal(api.trimUsable(null,null),false);
  assert.equal(api.trimUsable({...trim,schema:'other'},null),false);
  const bad=structuredClone(trim);bad.replay_timing.frame_video_seconds=[0,2,2,4];
  assert.equal(api.trimUsable(bad,null),false,'video seconds must increase');
  const short=structuredClone(trim);short.replay_timing.frame_orig_video_seconds.pop();
  assert.equal(api.trimUsable(short,null),false);
});

test('Trimmed and Original swap the recording timing and video urls; unrelated evidence and new stills are untouched',()=>{
  const trace=traceWith();api.attachTrim(trace,trim,{});
  saved.delete('attention.replay-trim');
  assert.equal(trace.recording.replay_timing.frame_video_seconds.join(),'0,2,3,4');
  assert.equal(trace.recording.duration_seconds,5);
  assert.equal(trace.evidence[0].media_url,'/files/trim/overview.mp4');
  assert.equal(trace.evidence[1].media_url,'/files/trim/side.mp4');
  assert.equal(trace.evidence[2].media_url,'/files/f.png');
  assert.notEqual(trace.evidence[0].evidence_id,'m','a different evidence id changes the player key');
  saved.set('attention.replay-trim','original');
  assert.equal(trace.recording.replay_timing,original);
  assert.equal(trace.evidence[0].media_url,'/files/o.mp4');
  assert.equal(trace.evidence[0].evidence_id,'m');
  trace.evidence[0].media_url='/files/changed.mp4';
  assert.equal(trace.evidence[0].media_url,'/files/changed.mp4','later assignments still reach the original url');
  saved.delete('attention.replay-trim');
  assert.equal(trace.evidence[0].media_url,'/files/trim/overview.mp4');
  trace.evidence.push({kind:'recording_frame_side',media_url:'/files/x.png'});
  assert.equal(trace.evidence.length,4);
});

test('events keep their meaning: the same trace time lands on the same frame in either timeline',()=>{
  const trace=traceWith();api.attachTrim(trace,trim,{});
  const at=(timestamp,mode)=>{saved.set('attention.replay-trim',mode);
    const timing=trace.recording.replay_timing;const hit=api.replayTarget({timestamp_basis:'relative',timestamp},{...trace.recording,replay_timing:timing},true);return {...hit,index:timing.frame_trace_seconds.findLastIndex(t=>t<=timestamp)};};
  for(const timestamp of [1,2.9,3,40,50,51.9,52]) {
    const a=at(timestamp,'original'),b=at(timestamp,'trimmed');
    assert.equal(a.index,b.index,`frame at ${timestamp}`);
    assert.equal(a.mode,'sample');assert.equal(b.mode,'sample');
  }
  assert.equal(at(40,'trimmed').seconds,2,'an event inside the skipped wait shows the last frame before it');
  assert.equal(at(50,'trimmed').seconds,3);
  assert.equal(at(60,'trimmed').seconds,null,'outside the recorded sample range stays unavailable');
  saved.delete('attention.replay-trim');
});

test('skipped spans read as plain words with the real time range',()=>{
  assert.equal(api.skipText(41.2),'41 s');
  assert.equal(api.skipText(125),'2 min 05 s');
  const row=api.operatorSkipRow(trim.breaks[0]);
  assert.match(row,/data-trim-break data-trim-at="3"/);
  assert.match(row,/Skipped 46 s of waiting/);
  assert.match(row,/real time 0:03 → 0:49/);
  assert.match(api.operatorSkipRow({...trim.breaks[0],kind:'still'}),/of no change/);
  saved.delete('attention.replay-trim');
  const bar=api.operatorTrimBar(trim);
  assert.match(bar,/data-trim-mode="trimmed" aria-pressed="true"/);
  assert.doesNotMatch(bar,/op-trim-note|of waiting ·/);  // 11-12 user: no summary line beside the toggle
  saved.set('attention.replay-trim','original');
  assert.match(api.operatorTrimBar(trim),/data-trim-mode="original" aria-pressed="true"/);
  assert.doesNotMatch(api.operatorTrimBar(trim),/Full recording/);
  saved.delete('attention.replay-trim');
});

test('a try replay read before its trim was built is asked for again, for a bounded time',()=>{
  const untrimmed={recording:{video_saved:true}},at=first=>({first,last:first});
  assert.equal(api.tryReplayDue(undefined,undefined,0),true,'never read');
  assert.equal(api.tryReplayDue(untrimmed,at(0),5000),false,'too soon after the last read');
  assert.equal(api.tryReplayDue(untrimmed,at(0),9000),true,'the trim may have arrived');
  assert.equal(api.tryReplayDue(untrimmed,{first:0,last:355000},364000),false,'the build is over');
  assert.equal(api.tryReplayDue({recording:{video_saved:true,trim}},at(0),9000),false,'trim arrived');
  assert.equal(api.tryReplayDue({recording:{video_saved:false}},at(0),9000),false,'no video, no trim to wait for');
});
