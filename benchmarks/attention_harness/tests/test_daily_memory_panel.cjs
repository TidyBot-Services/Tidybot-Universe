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
 assert.match(page,/notes used in this run/);
 assert.match(page,/No notes yet/);
 assert.match(page,/Sample data/);
});

test('monitor includes reference controls and all four display states',()=>{
 for(const text of ['Back to run','Light','Dark','3D','2D','Show only used notes','Just now','Helps','Does not help','Not tested','Off'])assert.ok(page.includes(text),text);
 assert.match(page,/integrity="sha512-dLxUelApnYxpLt6K2iomGngnHO83iUvZytA3YjDUCjT0HDOHKXnVYdf3hU4JjM8uEhxf9nD1\/ey98U3t2vZ0qQ=="/);
});
