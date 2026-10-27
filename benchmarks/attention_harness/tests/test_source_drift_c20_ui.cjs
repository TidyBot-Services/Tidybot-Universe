const assert=require('node:assert/strict');
const ui=require('../ui/memory.js');
const source_drift=[{origin_lock_sha256:'a'.repeat(64),current_lock_sha256:'b'.repeat(64),files:[{path:'<code>.py',origin_sha256:'c'.repeat(64),current_sha256:'d'.repeat(64)}]}];
const data=ui.adapt({notes:[{id:'test',title:'Note',status:'active',source_drift}]});
const html=ui.cardHtml(data.notes[0]);
assert.match(html,/Origin code differs: 1 files/);
assert.match(html,/&lt;code&gt;.py/);
assert.ok(html.includes('a'.repeat(64))&&html.includes('d'.repeat(64)));
