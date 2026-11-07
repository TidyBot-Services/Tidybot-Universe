"""The one page the gateway writes itself; everything after login is the main UI's own page. It carries no script: the motion is CSS."""
LOGIN_PAGE = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in · Attention Bench</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700;800&family=DM+Sans:wght@400;500;700&family=IBM+Plex+Mono:wght@500;600&display=swap">
<style>
:root{color-scheme:dark;--ink:#f3f1ea;--muted:#a3a199;--acc:#ec6a43;--fill:#d94d27;--stage:#0d0f12;--line:#ffffff24}
*{box-sizing:border-box}html,body{min-height:100%;margin:0}
body{position:relative;min-height:100vh;display:grid;grid-template-columns:minmax(0,1.25fr) minmax(320px,420px);align-items:center;gap:clamp(32px,6vw,96px);padding:48px clamp(16px,7vw,112px);overflow-x:hidden;background:radial-gradient(760px 560px at 74% 50%,#d94d2733,transparent 72%),var(--stage);color:var(--ink);font:16px/1.5 "DM Sans",system-ui,sans-serif}
.grid{position:fixed;inset:0;pointer-events:none;background-image:linear-gradient(#ffffff0a 1px,transparent 1px),linear-gradient(90deg,#ffffff0a 1px,transparent 1px);background-size:56px 56px;mask-image:radial-gradient(circle at 60% 50%,#000,transparent 85%)}
.scan{position:fixed;left:0;right:0;top:0;height:180px;pointer-events:none;background:linear-gradient(180deg,#d94d2700,#d94d2714 50%,#d94d2700);animation:scan 9s linear infinite}
.rings{position:fixed;right:calc(clamp(16px,7vw,112px) + 210px);top:50%;width:760px;height:760px;margin:-380px -380px 0 0;pointer-events:none;border-radius:50%;border:1px solid #ec6a4324;animation:breathe 6s ease-in-out infinite}
.rings::before,.rings::after{content:"";position:absolute;border-radius:50%;border:1px solid #ec6a432e}.rings::before{inset:110px}.rings::after{inset:220px;border-style:dashed;animation:turn 40s linear infinite}
.brand{position:relative}
.eyebrow{display:flex;align-items:center;gap:10px;margin:0;font:600 14px "IBM Plex Mono",monospace;letter-spacing:.18em;color:#e8b26a}
.eyebrow i{width:9px;height:9px;flex:none;border-radius:50%;background:#3fa36b;box-shadow:0 0 12px #3fa36b;animation:pulse 1.8s ease-in-out infinite}
h1{margin:20px 0 22px;font:800 clamp(64px,7vw,124px)/.86 "Barlow Condensed",Impact,sans-serif;letter-spacing:.005em;text-transform:uppercase}
h1 span{display:block}h1 span+span{color:var(--fill)}
.lead{max-width:30em;margin:0;font-size:19px;color:#d6d3ca}
form{position:relative;display:grid;gap:14px;padding:30px;border:1px solid var(--line);border-radius:18px;background:#14181dd9;box-shadow:0 30px 70px -30px #000,inset 0 1px #ffffff14;backdrop-filter:blur(8px)}
h2{margin:0;font:700 34px/1 "Barlow Condensed",Impact,sans-serif;letter-spacing:.03em;text-transform:uppercase}
form p{margin:0;font-size:15px;color:var(--muted)}
label{display:grid;gap:7px;margin-top:6px;font:600 12px "IBM Plex Mono",monospace;letter-spacing:.14em;color:var(--muted);text-transform:uppercase}
input{width:100%;min-height:54px;padding:10px 14px;border:1px solid #ffffff33;border-radius:10px;background:#0d0f12;color:var(--ink);font:500 18px "IBM Plex Mono",monospace;letter-spacing:.04em;transition:border-color .16s,box-shadow .16s}
input:focus{outline:0;border-color:var(--acc);box-shadow:0 0 0 3px #ec6a4340}
button{display:flex;align-items:center;justify-content:center;gap:10px;min-height:54px;border:0;border-radius:999px;background:var(--fill);color:#fff;font:700 22px "Barlow Condensed",Impact,sans-serif;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;box-shadow:0 12px 30px -10px #d94d27cc;transition:transform .16s cubic-bezier(.2,.8,.2,1),background .16s}
button:hover{background:#c44420;transform:translateY(-2px)}button span{transition:transform .16s}button:hover span{transform:translateX(4px)}
button:focus-visible,a:focus-visible{outline:2px solid var(--ink);outline-offset:3px}
strong{padding:9px 12px;border:1px solid #e2604f88;border-radius:10px;background:#e2604f22;color:#ffd0c9;font-size:14px;font-weight:500}strong:empty{display:none}
.watch{display:flex;justify-content:space-between;gap:12px;margin-top:6px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:14px}
.watch a{color:var(--ink);font-weight:700;text-decoration:none;white-space:nowrap}.watch a:hover{color:var(--acc)}
@keyframes scan{from{transform:translateY(-200px)}to{transform:translateY(100vh)}}
@keyframes pulse{50%{opacity:.35}}@keyframes turn{to{transform:rotate(360deg)}}@keyframes breathe{50%{transform:scale(1.03);opacity:.7}}
@media(max-width:1080px){body{grid-template-columns:minmax(0,1fr);align-content:center;gap:28px;padding:32px 16px}h1{font-size:clamp(38px,13.6vw,110px)}form{width:100%;max-width:460px}.eyebrow{font-size:12px;letter-spacing:.12em}.rings{right:50%;top:30%;width:520px;height:520px;margin:-260px -260px 0 0}.rings::before{inset:76px}.rings::after{inset:150px}.lead{font-size:17px}form{padding:22px}}
@media(prefers-reduced-motion:reduce){.scan{display:none}.eyebrow i,.rings,.rings::after{animation:none}}
</style></head><body>
<div class="grid" aria-hidden="true"></div><div class="scan" aria-hidden="true"></div><div class="rings" aria-hidden="true"></div>
<section class="brand"><p class="eyebrow"><i aria-hidden="true"></i>LIVE ROBOT RUNS · IN SIMULATION</p>
<h1><span>Tidybot</span><span>AttentionBench</span></h1>
<p class="lead">Watch an AI teach a robot to work, and step in when it asks for help.</p></section>
<form method="post" action="/login"><h2>Invite only</h2><p>Enter your invite code to start a run.</p>
<label>Invite code<input name="code" autocomplete="off" spellcheck="false" required autofocus></label>
<strong role="alert">__MESSAGE__</strong><button type="submit">Continue<span aria-hidden="true">→</span></button>
<p class="watch">No code yet?<a href="/ui/">Watch the runs →</a></p></form>
</body></html>
'''
