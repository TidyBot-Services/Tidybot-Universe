// Render a captured, offline payload with the production Memory renderers.
// node scripts/preview_memory_note_history.cjs <payload.json> <output-dir>
const fs=require('node:fs'),path=require('node:path');
const api=require('../benchmarks/attention_harness/ui/memory.js');
const [input,output]=process.argv.slice(2);
if(!input||!output)throw Error('Expected payload.json and output directory');
const payload=JSON.parse(fs.readFileSync(input,'utf8'));
fs.mkdirSync(output,{recursive:true});
const ui=path.join(__dirname,'../benchmarks/attention_harness/ui');
const css=['shell.css','memory.css'].map(name=>fs.readFileSync(path.join(ui,name),'utf8')).join('\n');
for(const theme of ['light','dark']){
 const model=api.adapt(payload.monitor);
 const card=api.cardHtml(model.notes[0],false,true,4);
 const reuse=api.reuseHtml(payload.reuse,false).replace('<details data-reuse-id=', '<details open data-reuse-id=');
 const style='body{margin:0;padding:24px;background:var(--sh-bg);color:var(--sh-ink);font-family:Arial,sans-serif}main{max-width:1180px;margin:auto}h1{margin:0 0 8px}main>p{color:var(--sh-mut)}#monitor{display:block;height:auto;min-height:0}#monitor .card{display:block;position:relative;inset:auto;max-width:none;width:auto;max-height:none;margin:12px 0;padding:18px;border:1px solid var(--sh-line2);background:var(--sh-panel);color:var(--sh-ink);animation:none}#monitor .close{display:none}#monitor .note-metadata a{color:var(--sh-acc)}';
 fs.writeFileSync(path.join(output,`memory-history-${theme}.html`),`<!doctype html><html data-theme="${theme}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Memory history · offline evidence preview</title><style>${css}\n${style}</style><main><h1>Memory · note history</h1><p>Offline R1–R3 evidence preview. R2/R3 Dev decisions come from their turn logs; audit and version results remain pending.</p><section id="reuse-notes"><h2>Confirm note reuse</h2>${reuse}</section><h2>Current-run card · R1 v4</h2><p>The saved audit records both an explicit reference and a not-used decision. The explicit not-used decision wins.</p><section id="monitor" class="${theme==='light'?'light':''}"><div class="card on">${card}</div></section></main></html>`);
}
console.log(output);
