// 11-10 task picker (design A2): cards with generated start/done pictures replace the Task dropdown's face.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const UI=path.join(__dirname,'../ui');
const source=fs.readFileSync(path.join(UI,'live.js'),'utf8');
const html=fs.readFileSync(path.join(UI,'index.html'),'utf8');
const manifest=JSON.parse(fs.readFileSync(path.join(UI,'tasks/manifest.json'),'utf8'));
const code=source.slice(source.indexOf('  function fixtureTaskKey('),source.indexOf('  function runConfigSeedOptions('));
const esc=v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const api=vm.runInNewContext(code+'\n({taskPickerCards,taskPickerFamilies,taskPickerMatches,taskCardHtml,taskMarksSvg,taskPickerToolbarHtml,taskSeedChipsHtml,seedListText,fixtureTaskOptions})',{esc,taskName:v=>v,root:{dataset:{}}});
const PROFILE='r5-fixture-20261031';
const plain=x=>JSON.parse(JSON.stringify(x));  // arrays from the vm context are another realm's
const REMOTE={pick_place_counter_to_sink:[101,102],turn_on_microwave:[101,102],pick_place_counter_to_cabinet:[101,102]};
const remoteOptions=[
  {value:'pick_place_counter_to_sink',label:'Counter → Sink',group:'',seeds:[101,102]},
  {value:'turn_on_microwave@'+PROFILE,label:'turn_on_microwave',group:'',seeds:[101,102]},
  {value:'pick_place_counter_to_cabinet@'+PROFILE,label:'pick_place_counter_to_cabinet',group:'',seeds:[101,102]},
];

test('every task Remote offers has verified pictures for both of its seeds',()=>{
  for(const [task,seeds] of Object.entries(REMOTE)){
    const entry=manifest.tasks.find(t=>t.task_id===task);
    assert.ok(entry,task);
    for(const seed of seeds){
      const row=entry.seeds.find(s=>s.seed===seed);
      assert.ok(row?.pictures,`${task} ${seed}`);
      for(const file of [row.start,row.done]){
        const bytes=fs.readFileSync(path.join(UI,'tasks',file));
        assert.equal(bytes.subarray(0,2).toString('hex'),'ffd8',file);  // a JPEG
        assert.ok(bytes.length<200_000,`${file} is small`);
      }
      assert.ok(row.marks.length>=1&&row.marks.every(m=>m.circle&&m.circle.r>0),'object or button is circled');
      assert.ok(row.alt_start&&row.alt_done&&row.rule&&row.sentence.some(s=>s.mark==='o'));
    }
  }
  // Pick/place: the object is circled and the target outlined with an arrow; the microwave: the button circled.
  const sink=manifest.tasks.find(t=>t.task_id==='pick_place_counter_to_sink').seeds[0].marks[0];
  assert.equal(sink.kind,'move');assert.ok(sink.region.points.length>=3&&sink.arrow.length===4);
  const press=manifest.tasks.find(t=>t.task_id==='turn_on_microwave').seeds[0].marks[0];
  assert.equal(press.kind,'press');assert.match(press.part,/start_button/);
});

test('cards state success rule v2 and the remaining check gaps',()=>{
  const by=id=>manifest.tasks.find(t=>t.task_id===id);
  // Success rule v2 (43ebfe2): either arm presses, so the microwave card names no hand; the three fixed tasks
  // carry no defect; "both hands" wherever release counts.
  assert.equal(/\b(left|right)\b/i.test(by('turn_on_microwave').rule+(by('turn_on_microwave').note||'')),false);
  assert.match(by('pick_place_counter_to_cabinet').note,/loose/);assert.match(by('pick_place_counter_to_cabinet').rule,/both hands/);
  for(const id of ['heat_kebab_sandwich','prepare_coffee','open_stand_mixer_head'])assert.equal(by(id).defect,null,id);
  assert.match(by('prepare_coffee').rule,/started with the mug already upright under the outlet/);
  assert.match(by('heat_kebab_sandwich').rule,/door is shut and the timer was set with the door shut/);
  assert.match(by('open_stand_mixer_head').note,/seed 101/);
  for(const t of manifest.tasks)assert.equal(/left hand|left arm/i.test(t.rule),false,t.task_id);
  // A seed whose goal state is not a native success has no pictures, and says why.
  const mixer101=by('open_stand_mixer_head').seeds.find(s=>s.seed===101);
  assert.equal(mixer101.pictures,false);assert.match(mixer101.reason,/not a native success/);
  // The public manifest never carries host paths.
  assert.equal(JSON.stringify(manifest).includes('/home/'),false);
  for(const t of manifest.tasks)for(const s of t.seeds)if(s.pictures){
    assert.ok(fs.existsSync(path.join(UI,'tasks',s.start))&&fs.existsSync(path.join(UI,'tasks',s.done)),`${t.task_id} ${s.seed}`);
  }
});

test('cards: family tabs, search, selected card with marks, text card without pictures',()=>{
  const cards=api.taskPickerCards(remoteOptions,manifest);
  assert.deepEqual(plain(cards.map(c=>c.task_id)),['pick_place_counter_to_sink','turn_on_microwave','pick_place_counter_to_cabinet']);
  // Only families with offered tasks get a tab; Counter → Sink goes with cabinet/sink.
  assert.deepEqual(plain(api.taskPickerFamilies(cards).map(f=>[f.id,f.n])),[['B',1],['D',2]]);
  const toolbar=api.taskPickerToolbarHtml(cards,'all');
  assert.match(toolbar,/All · 3/);assert.match(toolbar,/Buttons &amp; knobs · 1/);assert.match(toolbar,/Cabinet &amp; sink · 2/);
  assert.equal(toolbar.includes('Open &amp; close'),false);
  assert.deepEqual(plain(cards.filter(c=>api.taskPickerMatches(c,'D','')).map(c=>c.task_id)),['pick_place_counter_to_sink','pick_place_counter_to_cabinet']);
  assert.deepEqual(plain(cards.filter(c=>api.taskPickerMatches(c,'all','microwave')).map(c=>c.task_id)),['turn_on_microwave']);
  const sink=api.taskCardHtml(cards[0],{selected:true,seed:102});
  assert.match(sink,/role="radio" aria-checked="true" data-task-card="pick_place_counter_to_sink"/);
  assert.match(sink,/src="\.\/tasks\/pick_place_counter_to_sink-102-start\.jpg" alt="Start: [^"]+"/);
  assert.match(sink,/src="\.\/tasks\/pick_place_counter_to_sink-102-done\.jpg" alt="Done: [^"]+"/);
  assert.match(sink,/class="tp-mark-object"/);assert.match(sink,/class="tp-mark-region"/);assert.match(sink,/marker-end="url\(#tp-arrow-/);
  assert.match(sink,/<b class="tp-o">[^<]+<\/b>/);assert.match(sink,/<b class="tp-b">sink<\/b>/);
  assert.match(sink,/1 object · 1 step · seeds 101, 102/);assert.match(sink,/Counts as done:/);
  const microwave=api.taskCardHtml(cards[1],{seed:101});
  assert.match(microwave,/aria-checked="false"/);assert.match(microwave,/No object · 1 step/);assert.equal(microwave.includes('Known gap'),false);
  assert.match(api.taskCardHtml(cards[2],{seed:101}),/Known gap:<\/b> The check’s “inside” margin is loose/);
  // A seed without pictures (e.g. main's 25 sink seeds) is a text card: no <img>, never a broken picture.
  const text=api.taskCardHtml(cards[0],{seed:117});
  assert.equal(text.includes('<img'),false);assert.match(text,/No picture for seed 117/);
  // Without a manifest every task is still offered, as text.
  const bare=api.taskPickerCards(remoteOptions,null);
  assert.equal(bare.length,3);assert.equal(api.taskCardHtml(bare[1],{seed:101}).includes('<img'),false);
});

test('marks are drawn from numbers only; steps are numbered; seed lists read naturally',()=>{
  const svg=api.taskMarksSvg([{circle:{cx:'10"><script>',cy:20,r:5},n:1},{circle:{cx:50,cy:60,r:9},region:{points:[[1,2],[3,4],[5,6]]},arrow:[50,60,300,200],n:2}],'x');
  assert.equal(svg.includes('<script>'),false);
  assert.match(svg,/<text[^>]*>1<\/text>/);assert.match(svg,/<text[^>]*>2<\/text>/);assert.match(svg,/points="1,2 3,4 5,6"/);
  assert.equal(api.seedListText(Array.from({length:25},(_,i)=>101+i)),'seeds 101–125');
  assert.equal(api.seedListText([101]),'seed 101');assert.equal(api.seedListText([102,101,107]),'seeds 101, 102, 107');
  assert.match(api.taskSeedChipsHtml([101,102],'102',false),/data-task-seed="102" aria-pressed="true"/);
  assert.match(api.taskSeedChipsHtml([101],'101',true),/disabled/);
});

test('the form still submits the same task_id and seed; the select stays, cards only set it',()=>{
  assert.match(source,/<label class="ab-launch-field" hidden data-task-select>Task<select name="task_id" aria-label="Task" required><\/select><\/label>/);
  assert.match(source,/form\.task_id\.value=card\.dataset\.taskCard;\s*form\.task_id\.dispatchEvent\(new Event\('change',\{bubbles:true\}\)\);/);
  assert.match(source,/form\.seed\.value=chip\.dataset\.taskSeed;\s*form\.seed\.dispatchEvent\(new Event\('change',\{bubbles:true\}\)\);/);
  assert.match(source,/tasks:\[profile\.task_id\],seeds:\[Number\(form\.seed\.value\)\]/);
  // Errors loading the pictures are shown, and the cards fall back to text.
  assert.match(source,/Task pictures could not load: \$\{error\?\.message\|\|String\(error\)\}/);
  assert.match(source,/Picture could not load/);
});

test('both themes and phone width are styled',()=>{
  assert.match(html,/#attentionbench-clear \.tp-grid\{display:grid;grid-template-columns:minmax\(200px,280px\) minmax\(0,1fr\)/,'H3: list left, preview right');
  assert.match(html,/@media \(max-width:760px\)\{\s*#attentionbench-clear \.tp-grid\{grid-template-columns:1fr\}/);
  const dark=html.slice(html.indexOf('<style id="ab-dark-theme">'));
  assert.match(dark,/\[data-theme="dark"\] button\.tp-card\{/);assert.match(dark,/\[data-theme="dark"\] \.tp-defect\{/);
  assert.match(html,/button\.tp-card:focus-visible\{outline:3px solid var\(--orange\)/);
});

test('H3 (user 11-10): text list on the left, pointed task previewed large with its marks',()=>{
  const live=require('node:fs').readFileSync(require('node:path').join(__dirname,'../ui/live.js'),'utf8');
  assert.match(live,/function taskListItemHtml\(card,selected\)/);
  assert.match(live,/function taskPreviewHtml\(card,seed,extra=''\)/);
  assert.match(live,/taskCardHtml\(card,\{selected:false,seed\}\)\.replace/,'the preview reuses the card with its marks');
  assert.match(live,/addEventListener\('pointerover'/);
  assert.match(live,/addEventListener\('pointerleave',\(\)=>\{picker\.preview=null;previewTask\(form\.task_id\.value\);\}\)/,'leaving returns to the chosen task');
  assert.match(live,/querySelectorAll\('\.tp-item\[data-task-card\]'\)/,'arrow keys move along the list');
});

test('the task list is as tall as the preview and scrolls inside; families head the list (user 11-10)',()=>{
  assert.match(html,/#attentionbench-clear \.tp-list\{display:flex;flex-direction:column;gap:8px;height:0;min-height:100%;overflow-y:auto/);
  assert.match(html,/\.tp-grid\{display:grid;grid-template-columns:minmax\(200px,280px\) minmax\(0,1fr\);gap:16px;align-items:stretch\}/);
  const live=require('node:fs').readFileSync(require('node:path').join(__dirname,'../ui/live.js'),'utf8');
  assert.match(live,/function taskListGroupsHtml\(cards,chosen\)/);
});


test('one seed control: the picker hides the old Scene seed dropdown once pictures load (user 11-10)',()=>{
  const live=require('node:fs').readFileSync(require('node:path').join(__dirname,'../ui/live.js'),'utf8');
  assert.match(live,/const seedField=form\.seed\.closest\('label'\)[^;]*;\s*if\(seedField\)seedField\.hidden=true;/);
  assert.match(live,/note\.classList\.add\('tp-seed-note'\);box\.append\(note\)/,'the seed note stays visible under the chips');
});

test('seed chips sit inside the chosen task\'s preview card, not in a loose row (user 11-10)',()=>{
  const live=require('node:fs').readFileSync(require('node:path').join(__dirname,'../ui/live.js'),'utf8');
  assert.match(live,/previewed\.value===form\.task_id\.value\?picker\.chips:''/);
  assert.match(live,/box\.querySelector\('\[data-task-seeds\]'\)\.innerHTML='';/);
  assert.match(html,/\.tp-preview-card \.tp-seeds\{/);
});
