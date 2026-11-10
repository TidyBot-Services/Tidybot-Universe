// 11-10 flicker fix: the preview keeps the current card until the new pictures are decoded, a slower earlier
// request never replaces a later one, and the text area gets the tallest task's height.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
const start=source.indexOf('  const taskPictureReady=new Map();'),end=source.indexOf('  // 11-09: a stopped run says');

test('pictures load once and their sources are read from the preview html',async()=>{
  const made=[];
  class FakeImage{constructor(){made.push(this);}decode(){return this.src.includes('bad')?Promise.reject(new Error('x')):Promise.resolve();}}
  const api=vm.runInNewContext(source.slice(start,end)+'\n({loadTaskPicture,taskPictureSources})',{Image:FakeImage});
  const cardHtml='<img src="./tasks/a-101-start.jpg" alt="" width="640" height="360" decoding="sync" data-tp-img><img src="./tasks/a-101-done.jpg" alt="" decoding="sync" data-tp-img>';
  assert.deepEqual([...api.taskPictureSources(cardHtml)],['./tasks/a-101-start.jpg','./tasks/a-101-done.jpg']);
  const one=api.loadTaskPicture('./tasks/a-101-start.jpg');
  assert.equal(api.loadTaskPicture('./tasks/a-101-start.jpg'),one);assert.equal(made.length,1);
  assert.ok(await one);
  assert.equal(await api.loadTaskPicture('./tasks/bad-101-start.jpg'),null,'a failed picture resolves, so the card still swaps and shows its error');
});
test('the preview swaps only after decoding, and only for the latest task pointed at',()=>{
  assert.match(source,/picker\.preview=value;  \/\/ set first[^\n]*\n\s*if\(box\.firstElementChild\?\.dataset\.taskPreviewCard===value\)return;/);
  assert.match(source,/const swap=\(\)=>\{if\(picker\.preview===value&&box\.isConnected\)box\.innerHTML=html;\};/);
  assert.match(source,/Promise\.all\(taskPictureSources\(html\)\.map\(loadTaskPicture\)\)\.then\(swap\);/);
  assert.equal(/box\.innerHTML=taskPreviewHtml/.test(source),false,'no immediate swap left');
});
test('preview pictures are not lazy, and the text area keeps the tallest height',()=>{
  assert.equal(source.includes('loading="lazy" decoding="async" data-tp-img'),false);
  assert.match(source,/steadyTaskPreview\(visible,showSeed\);/);
  assert.match(html,/\.tp-preview-card \.tp-body\{[^}]*min-height:var\(--tp-body-min,0px\)/);
});
