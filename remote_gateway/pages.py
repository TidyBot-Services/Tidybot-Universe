"""The one page the gateway writes itself; everything after login is the main UI's own page."""
LOGIN_PAGE = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>AttentionBench</title>
<style>
:root{color-scheme:light dark;--text:#242822;--muted:#686e64;--line:#d3d4c6;--panel:#f7f5ef;--paper:#fff;--accent:#d94d27}
@media(prefers-color-scheme:dark){:root{--text:#ecebe6;--muted:#a3a199;--line:#33383d;--panel:#1b1e22;--paper:#23272c;--accent:#ee6a43}}
*{box-sizing:border-box}body{min-height:100vh;margin:0;display:grid;place-items:center;padding:16px;background:var(--panel);color:var(--text);font:15px/1.5 system-ui,sans-serif}
form{display:grid;gap:12px;width:min(360px,100%)}h1{margin:0;font-size:26px}p{margin:0;color:var(--muted)}label{display:grid;gap:6px;font-size:13px}
input{min-height:42px;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--paper);color:var(--text);font:inherit}
button{min-height:42px;border:0;border-radius:999px;background:var(--text);color:var(--paper);font:600 14px system-ui,sans-serif;cursor:pointer}
strong{color:var(--accent);font-weight:600}strong:empty{display:none}
</style></head><body>
<form method="post" action="/login"><h1>AttentionBench</h1><p>Enter your invite code to start a run. Anyone can <a href="/ui/">watch without one</a>.</p>
<label>Invite code<input name="code" autocomplete="off" spellcheck="false" required autofocus></label>
<strong role="alert">__MESSAGE__</strong><button type="submit">Continue</button></form>
</body></html>
'''
