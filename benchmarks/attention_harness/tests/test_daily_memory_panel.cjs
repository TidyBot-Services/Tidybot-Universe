const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..');
const source=fs.readFileSync(path.join(root,'ui/live.js'),'utf8');
const page=fs.readFileSync(path.join(root,'ui/memory.html'),'utf8');
const monitor=require('../ui/memory.js');
const directory=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/ui_real_20261019/dev_v2_memory_directory_with_title.json'),'utf8'));

test('real directory is projected on the monitor with a friendly title and status',()=>{
 const row=directory.memories[0];
 const data=monitor.adapt({run:{versions:[{n:1,note_ids:[row.memory_id]}]},notes:[{id:row.memory_id,title:row.title,group:'General',status:'active',test_seeded:row.seeded_for_tests}],events:[]});
 assert.equal(data.notes[0].title,'Cabinet opening tip');
 assert.equal(data.notes[0].effect,'not_tested');
 assert.equal(data.notes[0].version,1);
 assert.match(monitor.cardHtml(data.notes[0],true),/Test note/);
});

test('Memory panel, row and other records are absent from the main page',()=>{
 assert.doesNotMatch(source,/memoryPanel\(|data-memory-panel|data-dev-memory-directory|Other memory records|data-memory-note-used/);
 assert.match(source,/updateMemoryLink\(snapshot\)/);
 // 11-18 (user): the Memory page was removed; memory.html now only redirects to the Run page.
 assert.match(page,/http-equiv="refresh" content="0;url=\/ui\/"/);
 assert.doesNotMatch(page,/<script|memory\.js|memory\.css/);
});

// 11-18 (user): the Memory monitor page (its controls, display states and 3D library) was removed;
// the shell page switcher now offers only Home and Run.
test('shell page switcher has no Memory tab',()=>{
 const shell=fs.readFileSync(path.join(root,'ui/shell.js'),'utf8');
 assert.match(shell,/const PAGES = \[\['home', 'Home', '\/ui\/home\.html'\], \['run', 'Run', '\/ui\/'\]\];/);
 assert.match(shell,/a\[data-page="memory"\]'\)\.forEach\(a => a\.remove\(\)\)/);
});
