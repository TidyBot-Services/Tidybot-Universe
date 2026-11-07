// Real keyboard input on the New run dialog. The browser fires input and then change for one choice, and the form repaints
// on every input; a value assigned from a script (select.value = x; dispatchEvent(change)) skips that order and hid a bug in
// which the Model source snapped back before its own change handler ran. Everything here goes through the browser's input.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const ui = path.join(__dirname, '../ui');
const html = fs.readFileSync(path.join(ui, 'index.html'), 'utf8').replace('data-mode="demo"', 'data-mode="live"').replace('data-mode="live"', 'data-mode="live" data-control-token="t"');
const runOptions = fs.readFileSync(path.join(__dirname, 'fixtures/run_options_agent.json'), 'utf8');
// The fixture offers one New run task; these tests need a second one to switch to.
function withSecondTask() {
  const value = JSON.parse(runOptions), first = value.dev_v2.profiles.find(p => p.task_id === 'pick_place_counter_to_sink');
  value.dev_v2.profiles.push({...first, id: 'second-task', task_id: 'pick_place_counter_to_cabinet'});
  return JSON.stringify(value);
}
const otherTask = task => task.evaluate(el => [...el.options].find(o => !o.disabled && o.value !== el.value).value);
const presets = ['deepseek', 'openai', 'parcc', 'openai-compatible'].map(id => ({id, label: id, needs_key: true, suggested_models: ['m1'], thinking: null}));
let browser;
before(async () => { browser = await chromium.launch({executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox']}); });
after(async () => { await browser?.close(); });

const lobbySnapshot = tasks => ({demo: true, demo_info: {recordings: 7, history_limit: 8, tasks}, clock: {speed: 60}, me: {nickname: 'Lynx-1'}, robot: {state: 'idle', running: null},
  queue: [], recent: [], counts: {}, limits: {help_wait_seconds: 300}, events: []});
async function dialog(t, {credentials = [], width = 1280, options = runOptions, remote = null, gateway = false} = {}) {
  const page = await browser.newPage({viewport: {width, height: 2400}}); t.after(() => page.close());
  if (process.env.UI_TARGET_TIMEOUT) page.setDefaultTimeout(Number(process.env.UI_TARGET_TIMEOUT));
  await page.route('**/*', route => {
    const {pathname} = new URL(route.request().url());
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: typeof body === 'string' ? body : JSON.stringify(body)});
    if (pathname === '/ui/') return route.fulfill({contentType: 'text/html', body: remote ? html.replace('data-mode="live"', 'data-mode="live" data-remote="1"') : gateway ? html.replace('data-mode="live"', 'data-mode="live" data-gateway="1"') : html});
    if (pathname === '/api/lobby' && remote) return json(lobbySnapshot(remote));
    const asset = /^\/ui\/([\w.-]+\.(js|css))$/.exec(pathname);
    if (asset && fs.existsSync(path.join(ui, asset[1])))
      return route.fulfill({contentType: asset[2] === 'js' ? 'text/javascript; charset=utf-8' : 'text/css', body: fs.readFileSync(path.join(ui, asset[1]))});
    if (pathname === '/api/run-options') return json(options);
    if (pathname === '/api/runs') return json({runs: [], launches: []});
    if (pathname === '/api/provider/presets') return json(presets);
    if (pathname === '/api/provider/credentials') return json(credentials);
    if (pathname === '/api/codex-quota') return json({used_percent: 50, window_minutes: 10080});
    return json({error: 'not in this fixture'}, 503);
  });
  await page.goto('http://attention.test/ui/');
  if (remote) await page.waitForFunction(() => window.Lobby?.enabled);
  await page.locator('[data-open-launch]').first().click();
  const form = page.locator('#ab-launch-form');
  // Remote folds models, reviewer, limits and advanced under More settings; the checks below look inside it.
  if (await form.locator('[data-more-settings]').count()) await form.locator('[data-more-settings] > summary').click();
  await form.locator('[data-keys-source]').waitFor();
  // The My-key options are enabled by the credentials read; wait for that, not for a timer.
  await page.waitForFunction(() => document.querySelector('#ab-launch-form [data-keys-source] option[value="all"]')?.disabled === false);
  await page.evaluate(() => document.querySelectorAll('#ab-launch-form details').forEach(d => { d.open = true; }));
  return {page, form, source: form.locator('[data-keys-source]'), radio: () => form.locator('[data-keys-mode]:checked').getAttribute('value')};
}
// Logs the events one control receives, so a test can pin the browser's order (input, then change).
const watch = (locator) => locator.evaluate(el => {
  window.__events = [];
  for (const type of ['input', 'change']) el.addEventListener(type, () => window.__events.push(type + ':' + el.value), true);
});

for (const width of [1280, 390]) test(`MODEL-SOURCE-KEYS: a real key press on the Model source keeps the choice at ${width}px`, async t => {
  const {page, form, source, radio} = await dialog(t, {width});
  await watch(source);
  await source.focus();
  await page.keyboard.press('ArrowDown');                      // codex -> PARCC -> My key for all agents
  await page.keyboard.press('ArrowDown');
  assert.deepEqual(await page.evaluate(() => window.__events), ['input:parcc', 'change:parcc', 'input:all', 'change:all']);
  assert.equal(await source.inputValue(), 'all');
  assert.equal(await radio(), 'all');
  assert.equal(await form.locator('[data-key-value]').isVisible(), true);   // the add-key form appeared
  await page.keyboard.press('ArrowDown');
  assert.equal(await source.inputValue(), 'different');
  assert.equal(await radio(), 'different');
  for (let step = 0; step < 3; step++) await page.keyboard.press('ArrowUp');
  assert.equal(await source.inputValue(), 'codex');
  assert.equal(await radio(), 'lab');
  assert.equal(await form.locator('[data-key-value]').isVisible(), false);
});

test('MODEL-SOURCE-SELECT: choosing with selectOption (input then change) sticks, both ways, and the Billed to card follows', async t => {
  const {form, source, radio} = await dialog(t);
  const bill = form.locator('[data-bill-name]');
  assert.match(await bill.innerText(), /Codex subscription/);
  for (const [value, mode, billed] of [['all', 'all', /Your key/], ['different', 'different', /Set per agent/], ['codex', 'lab', /Codex subscription/], ['all', 'all', /Your key/]]) {
    await source.selectOption(value);
    assert.equal(await source.inputValue(), value);
    assert.equal(await radio(), mode);
    assert.match(await bill.innerText(), billed);
  }
});

const models = form => form.evaluate(f => Object.fromEntries(['dev', 'eval', 'advisor'].map(role => {
  const select = f.elements[`${role}_model`];
  return [role, {value: select.value, options: [...select.options].map(o => o.value)}];
})));

for (const width of [1280, 390]) test(`MODEL-SOURCE-PARCC-AGENT: PARCC is a normal choice in Agent mode at ${width}px`, async t => {
  const {page, form, source, radio} = await dialog(t, {width});
  const parcc = source.locator('option[value="parcc"]');
  assert.deepEqual(await parcc.evaluate(o => [o.disabled, o.textContent]), [false, 'Lab models · PARCC (GLM + Qwen)']);
  await watch(source);
  await source.focus();
  await page.keyboard.press('ArrowDown');                      // codex -> parcc, through the browser's input then change
  assert.deepEqual(await page.evaluate(() => window.__events), ['input:parcc', 'change:parcc']);
  await page.waitForTimeout(150);                              // anything scheduled by the repaint
  assert.equal(await source.inputValue(), 'parcc');
  assert.equal(await radio(), 'lab');
  assert.equal(await form.locator('[data-bill-name]').innerText(), 'PARCC (free)');
  assert.match(await form.locator('[data-source-help]').innerText(), /GLM reads text only/);
  assert.doesNotMatch(await form.locator('[data-agent-reason]').innerText(), /quota/);
  assert.equal(await form.locator('[data-codex-quota]').isVisible(), false);
  assert.deepEqual(await models(form), {
    dev: {value: 'parcc/GLM', options: ['parcc/GLM']},
    eval: {value: 'parcc/GLM', options: ['parcc/GLM']},
    advisor: {value: 'parcc/GLM', options: ['parcc/GLM', 'parcc/Qwen']},
  });
  // Other controls repaint the form; the choice has to survive them.
  await form.locator('input[name="max_attempts"]').fill('3');
  await form.locator('select[name="seed"]').selectOption({index: 1});
  await page.waitForTimeout(150);
  assert.equal(await source.inputValue(), 'parcc');
  assert.equal(await form.locator('select[name="advisor_model"]').inputValue(), 'parcc/GLM');
});

test('MODEL-SOURCE-PARCC-AGENT: each source keeps its own model picks', async t => {
  const {form, source} = await dialog(t);
  await form.locator('select[name="eval_model"]').selectOption('gpt-6-astra');
  await source.selectOption('parcc');
  await form.locator('select[name="advisor_model"]').selectOption('parcc/Qwen');
  await source.selectOption('codex');
  const codex = await models(form);
  assert.equal(codex.eval.value, 'gpt-6-astra');
  assert.deepEqual(codex.dev.options, ['gpt-6.1-sol', 'gpt-6-astra', 'gpt-6-sol']);
  assert.deepEqual(codex.advisor.options, ['gpt-6.1-sol', 'gpt-6-astra']);
  await source.selectOption('parcc');
  const parcc = await models(form);
  assert.equal(parcc.advisor.value, 'parcc/Qwen');
});

test('NEW-RUN-SOURCE: changing the task keeps the Model source and its model picks', async t => {
  const {page, form, source} = await dialog(t, {options: withSecondTask()});
  await source.selectOption('parcc');
  await form.locator('select[name="advisor_model"]').selectOption('parcc/Qwen');
  const task = form.locator('select[name="task_id"]');
  await task.selectOption(await otherTask(task));
  await page.waitForTimeout(150);
  assert.equal(await source.inputValue(), 'parcc');
  assert.equal(await form.locator('[data-bill-name]').innerText(), 'PARCC (free)');
  assert.equal(await form.locator('select[name="advisor_model"]').inputValue(), 'parcc/Qwen');
});

test('NEW-RUN-BUDGET: a lowered budget survives repaints and a new task starts from the official one', async t => {
  const {page, form} = await dialog(t, {options: withSecondTask()});
  const versions = form.locator('input[name="max_attempts"]'), wall = form.locator('input[name="wall_seconds"]');
  assert.deepEqual([await versions.inputValue(), await wall.inputValue()], ['10', '21600']);
  await versions.fill('3');
  await wall.fill('7200');
  await form.locator('[data-policy-card]').nth(1).check();
  await page.waitForTimeout(150);
  assert.deepEqual([await versions.inputValue(), await wall.inputValue()], ['3', '7200']);
  assert.match(await form.locator('[data-agent-reason]').innerText(), /^3 formal versions .* 2 hours/);
  assert.equal(await form.locator('[data-reduced-note]').isVisible(), true);
  const task = form.locator('select[name="task_id"]');
  await task.selectOption(await otherTask(task));
  await page.waitForTimeout(150);
  assert.deepEqual([await versions.inputValue(), await wall.inputValue()], ['10', '21600']);
});

const sectionHeads = form => form.locator('h4').evaluateAll(els => els.filter(e => e.getClientRects().length).map(e => e.textContent.trim()));
const fieldNames = form => form.locator('.ab-launch-section input[name], .ab-launch-section select[name]').evaluateAll(els => els.map(e => e.name).filter(name => name !== 'lb_share').sort());

test('REMOTE-FORM: Remote shows main\'s sections and fields, plus Queue', async t => {
  const main = await dialog(t), remote = await dialog(t, {remote: ['pick_place_counter_to_sink']});
  assert.deepEqual(await sectionHeads(remote.form), [...await sectionHeads(main.form), '07Queue']);
  assert.deepEqual(await fieldNames(remote.form), await fieldNames(main.form));
  const hidden = await remote.form.locator('.ab-launch-section').evaluateAll(els => els.filter(e => !e.getClientRects().length).length);
  assert.equal(hidden, 0, 'with More settings open every section shows');
  assert.deepEqual(await remote.form.locator('[data-more-settings] [data-run-config-section]').evaluateAll(els => els.map(e => e.dataset.runConfigSection)),
    ['models', 'reviewer', 'limits', 'advanced'], 'what to run and help stay in view; the rest is folded');
  assert.equal(await main.form.locator('[data-more-settings]').count(), 0, 'the local page folds nothing');
  assert.equal(await remote.form.locator('[data-launch-preview]').isVisible(), true);
  assert.match(await remote.page.locator('[data-remote-note]').innerText(), /Real runs are not connected yet/);
});

test('REMOTE-FORM-CODEX: Remote offers no Codex anywhere and asks nothing about its quota', async t => {
  const asked = [];
  const {page, form, source} = await dialog(t, {remote: ['pick_place_counter_to_sink']});
  page.on('request', request => asked.push(new URL(request.url()).pathname));
  assert.deepEqual(await source.evaluate(el => [...el.options].map(o => o.value)), ['parcc', 'all', 'different']);
  assert.equal(await source.inputValue(), 'parcc');
  assert.equal(await form.locator('[data-bill-name]').innerText(), 'PARCC (free)');
  assert.deepEqual((await models(form)).dev.options, ['parcc/GLM']);
  await source.selectOption('different');
  await form.locator('[data-keys-details]').evaluate(el => { el.open = true; });
  for (const role of ['dev', 'eval', 'advisor']) {
    const row = form.locator(`[data-role-choice="${role}"]`);
    assert.deepEqual(await row.evaluate(el => [...el.options].map(o => o.textContent)), ['Lab models · PARCC']);
  }
  assert.equal(await form.locator('[data-codex-quota]').isVisible(), false);
  assert.equal((await form.evaluate(f => f.textContent)).includes('Codex subscription'), false);
  await source.selectOption('parcc');
  assert.equal(asked.includes('/api/codex-quota'), false);
});

test('REMOTE-FORM-LIMITS: the shared host caps 05 and the help count, and says so', async t => {
  const {page, form} = await dialog(t, {remote: ['pick_place_counter_to_sink']});
  const versions = form.locator('input[name="max_attempts"]'), wall = form.locator('input[name="wall_seconds"]');
  assert.deepEqual([await versions.inputValue(), await wall.inputValue(), await form.locator('input[name="help_count"]').inputValue()], ['3', '3600', '3']);
  assert.equal(await wall.evaluate(el => el.readOnly), true);
  assert.match(await form.locator('[data-official-budget]').innerText(), /Remote limit: 3 versions · 1 hour/);
  assert.equal(await form.locator('[data-reduced-note]').isVisible(), false);
  await versions.fill('4');
  await page.waitForTimeout(150);
  assert.match(await form.locator('[data-limit-error="versions"]').innerText(), /Largest allowed: 3 versions/);
  assert.equal(await form.locator('[data-start-yes]').isDisabled(), true);
  await versions.fill('2');
  await page.waitForTimeout(150);
  assert.equal(await form.locator('[data-start-yes]').isDisabled(), false);
  assert.match(await form.locator('[data-reduced-text]').innerText(), /^2 of 3 versions · 1 of 1 hour\.$/);
});

test('REMOTE-FORM-JOIN: Start run is off, the demo join works, and a task without a recording says so', async t => {
  const {page, form} = await dialog(t, {remote: ['pick_place_counter_to_sink'], options: withSecondTask()});
  assert.equal(await form.locator('[data-remote-start]').isDisabled(), true);
  assert.equal(await form.locator('[data-start-no]').isVisible(), false);
  assert.equal(await form.locator('input[name="goal_id"]').evaluate(el => el.readOnly), true);
  const join = form.locator('[data-start-yes]');
  assert.equal(await join.innerText(), 'Join demo');
  await form.locator('select[name="task_id"]').selectOption('pick_place_counter_to_cabinet');
  await page.waitForTimeout(150);
  assert.equal(await join.innerText(), 'No demo yet');assert.equal(await join.isDisabled(), true);
  await form.locator('select[name="task_id"]').selectOption('pick_place_counter_to_sink');
  await page.waitForTimeout(150);
  const request = page.waitForRequest(r => r.method() === 'POST' && new URL(r.url()).pathname === '/api/lobby/runs');
  await join.click();
  assert.deepEqual((await request).postDataJSON(), {task_key: 'pick_place_counter_to_sink', strategy: 'reactive_help', dev_source: 'lab_glm', share_replies: false});
});

// What the gateway serves: its own limits in the options, and a goal it sets itself.
function hostedOptions() {
  const value = JSON.parse(runOptions);
  value.dev_v2.goal_key = 'set-by-host';
  value.hosted = {limits: {versions: 3, wall: 3600, help: 3, maxHelp: 3, versionsRange: '1–3 · Remote limit', wallRange: '1 hour · Remote limit', note: 'Remote limit: 3 versions.'}};
  return JSON.stringify(value);
}

test('GATEWAY-PAGE: the page behind the gateway is the same form with real Start, no Codex and the host\'s limits', async t => {
  const asked = [];
  const {page, form, source} = await dialog(t, {gateway: true, options: hostedOptions()});
  page.on('request', request => asked.push(new URL(request.url()).pathname));
  assert.deepEqual(await sectionHeads(form), ['01What to run', '02Help and attention', '03Models & keys', '04Automatic reviewer', '05Limits', '06Advanced']);
  assert.deepEqual(await source.evaluate(el => [...el.options].map(o => o.value)), ['parcc', 'all', 'different']);
  assert.deepEqual([await form.locator('input[name="max_attempts"]').inputValue(), await form.locator('input[name="wall_seconds"]').inputValue()], ['3', '3600']);
  assert.equal(await form.locator('input[name="goal_id"]').evaluate(el => [el.value, el.readOnly].join()), 'set-by-host,true');
  assert.equal(await form.locator('[data-start-yes]').innerText(), 'Start run');
  assert.equal(await form.locator('[data-start-yes]').isDisabled(), false);
  assert.equal(await form.locator('[data-start-no]').isVisible(), true);
  assert.equal(await page.locator('[data-remote-note]').count(), 0);
  assert.deepEqual(await page.locator('.ab-pages a').evaluateAll(els => els.map(a => a.textContent.trim())), ['Run']);
  const sent = await submitted(page, form);
  assert.deepEqual(sent.agent_provider.roles.eval, lab('parcc/GLM'));
  assert.equal(asked.includes('/api/codex-quota'), false);
});

test('GATEWAY-PAGE: signing out ends the session, and a lost session goes back to login', async t => {
  const {page} = await dialog(t, {gateway: true, options: hostedOptions()});
  await page.locator('[data-close-launch]').first().click();
  let ended = null;
  await page.route('**/api/session', route => { ended = route.request(); return route.fulfill({status: 200, contentType: 'application/json', body: '{"ok":true}'}); });
  await page.route('**/login', route => route.fulfill({contentType: 'text/html', body: '<h1>login</h1>'}));
  await page.locator('[data-sign-out]').click();
  await page.waitForURL('**/login');
  assert.equal(ended.method(), 'DELETE');
  assert.equal(ended.headers()['x-attention-token'], 't');
  await page.goto('http://attention.test/ui/');
  await page.route('**/api/runs', route => route.fulfill({status: 401, contentType: 'application/json', body: '{"code":"login_required"}'}));
  await page.waitForURL('**/login', {timeout: 8000});
});

test('MODEL-SOURCE-ROLES: Different per agent offers PARCC per role, and Dev on the lab still submits', async t => {
  const now = Math.floor(Date.now() / 1000), credentials = [{credential_id: 'mine', provider: 'deepseek', expires_at: now + 3600, base_url_host: 'api.deepseek.com'}];
  const {page, form, source} = await dialog(t, {credentials});
  await source.selectOption('different');
  await form.locator('[data-keys-details]').evaluate(el => { el.open = true; });
  await form.locator('[data-role-choice="eval"]').selectOption('parcc');
  assert.deepEqual((await models(form)).eval.options, ['parcc/GLM']);
  const sent = await submitted(page, form);
  assert.deepEqual(sent.agent_provider, {roles: {eval: lab('parcc/GLM'), dev: {credential: 'lab', provider: 'openai', model: 'gpt-6.1-sol'}}});
});

test('NEW-RUN-AGENT-ONLY: nothing in New run offers One call or a fixed policy', async t => {
  const {form} = await dialog(t);
  const text = await form.evaluate(f => f.textContent);
  for (const gone of ['Run type', 'Fixed policy', 'Dev mode', 'One call', 'All seven', 'AI thinking', 'Wait for a person', 'Token budget', 'Execution location', 'Entry support matrix', 'Independent resource limits'])
    assert.equal(new RegExp(`${gone}\\b`).test(text), false, gone);   // "Dev model" is not "Dev mode".
  for (const name of ['run_type', 'dev_mode', 'fixed_policy', 'fixed_assistance', 'fixed_credits', 'fixed_human_deadline', 'token_limit', 'gpu_seconds', 'request_mode', 'model_seconds_minutes', 'human_wait_seconds'])
    assert.equal(await form.locator(`[name="${name}"]`).count(), 0, name);
  const policies = await form.locator('select[name="v2_policy"]').evaluate(el => [...el.options].map(o => o.value));
  assert.deepEqual(policies, JSON.parse(runOptions).dev_v2.agent_policies.policies.map(p => p.policy_id));
  assert.equal(await form.locator('input[name="eval_agent_enabled"]').isChecked(), true);
  assert.equal(await form.locator('input[name="eval_agent_enabled"]').isDisabled(), true);
  assert.equal(await form.locator('[data-start-no]').isVisible(), true);
});

test('NEW-RUN-TIER: a server off the dev tier says why and cannot start', async t => {
  const value = JSON.parse(runOptions);value.dev_v2.budget_tier = 'legacy';
  const {form} = await dialog(t, {options: JSON.stringify(value)});
  assert.match(await form.locator('[data-agent-reason]').innerText(), /modern dev tier/);
  assert.equal(await form.locator('[data-start-yes]').isDisabled(), true);
  assert.equal(await form.locator('[data-start-no]').isDisabled(), true);
});

async function submitted(page, form) {
  const request = page.waitForRequest(r => r.method() === 'POST' && new URL(r.url()).pathname === '/api/dev-v2/experiments');
  await form.locator('[data-start-no]').click();
  return (await request).postDataJSON().selection;
}
const lab = model => ({credential: 'lab', provider: 'parcc', model});

test('MODEL-SOURCE-PARCC-SUBMIT: the run asks for the lab key on all three roles and sends no key', async t => {
  const {page, form, source} = await dialog(t);
  await source.selectOption('parcc');
  await form.locator('select[name="advisor_model"]').selectOption('parcc/Qwen');
  const sent = await submitted(page, form);
  assert.deepEqual(sent.agent_provider, {roles: {dev: lab('parcc/GLM'), eval: lab('parcc/GLM'), advisor: lab('parcc/Qwen')}});
  assert.equal(sent.dev_mode, 'agent');
  assert.equal(sent.model_source, 'codex');                    // The frozen Agent schema; public views label the run PARCC.
  assert.equal(sent.goal_key, await form.locator('input[name="goal_id"]').inputValue());
  assert.equal('configuration' in sent, false);
  assert.doesNotMatch(JSON.stringify(sent), /api_key|"key"|secret/i);
});

test('MODEL-SOURCE-CODEX-SUBMIT: a Codex run sends no provider routing', async t => {
  const {page, form, source} = await dialog(t);
  await source.selectOption('parcc');
  await source.selectOption('codex');
  const sent = await submitted(page, form);
  assert.equal('agent_provider' in sent, false);
  assert.equal(sent.dev_model, 'gpt-6.1-sol');
});

test('NEW-RUN-POLICY: three groups say who starts help, and a card sets the value the run is created with', async t => {
  const {form} = await dialog(t);
  const select = form.locator('select[name="v2_policy"]');
  assert.deepEqual(await form.locator('[data-policy-group] > p b').allInnerTexts(), ['No help during the run', 'Dev asks', 'System steps in']);
  assert.equal(await select.isVisible(), false);
  assert.equal(await form.locator('[data-policy-card]:checked').getAttribute('value'), await select.inputValue());
  const card = form.locator('.ab-policy-card', {hasText: 'Reactive help'});
  assert.match(await card.innerText(), /must ask for help after every failed Eval/);
  await card.click();
  assert.equal(await select.inputValue(), 'reactive_help');
  assert.equal(await form.locator('[data-policy-card]:checked').count(), 1);
});

// The same trap anywhere else in the dialog: a control the form repaints on input must still hold what the user chose.
for (const name of ['dev_model', 'eval_model', 'advisor_model', 'v2_policy', 'v2_mode', 'seed', 'task_id']) {
  test(`DIALOG-SELECT-${name}: a real key press on ${name} keeps the value it set`, async t => {
    const {page, form} = await dialog(t);
    const select = form.locator(`select[name="${name}"]`);
    const enabled = await select.evaluate(el => [...el.options].filter(o => !o.disabled).map(o => o.value));
    if (enabled.length < 2 || !(await select.isVisible()) || await select.isDisabled()) return t.skip(`${name} has nothing to switch to in this fixture`);
    const before = await select.inputValue();
    await select.focus();
    await page.keyboard.press('ArrowDown');
    const after = await select.inputValue();
    assert.notEqual(after, before);
    await page.waitForTimeout(150);                              // anything scheduled by the repaint
    assert.equal(await select.inputValue(), after);
  });
}

for (const [name, text] of [['max_attempts', '3'], ['wall_seconds', '7200'], ['name_suffix', 'abc']]) {
  test(`DIALOG-INPUT-${name}: typing ${text} into ${name} is kept`, async t => {
    const {page, form} = await dialog(t);
    const input = form.locator(`input[name="${name}"]`);
    await input.click();
    await input.press('Control+A');
    await page.keyboard.type(text);
    await page.waitForTimeout(150);
    assert.equal(await input.inputValue(), text);
  });
}

// 03's line under the heading says who pays; it used to quote the lab quota next to "billed to your own key".
test('MODEL-SOURCE-SUMMARY: the 03 summary follows the source and never mixes quota with an own key', async t => {
  const {form, source} = await dialog(t);
  const summary = form.locator('[data-agent-reason]');
  assert.match(await summary.innerText(), /formal versions .* stops at 90% quota/);
  await source.selectOption('all');
  const own = await summary.innerText();
  assert.match(own, /own key; the lab quota does not apply\. Limits are set in 05/);
  assert.doesNotMatch(own, /90%|stops at|formal versions/);
  await source.selectOption('different');
  assert.match(await summary.innerText(), /Agent models below\. Limits are set in 05/);
  await source.selectOption('codex');
  assert.match(await summary.innerText(), /stops at 90% quota/);
  await form.locator('input[name="max_attempts"]').fill('2');          // 05 still feeds the Lab line
  assert.match(await summary.innerText(), /^2 formal versions/);
});

// Layout: 03 and 05 share one three-column grid, so the Billed to card, the key form and the role rows land on the
// same column edges as the fields above and below them, and the two sides of the source row are as tall as each other.
const box = locator => locator.evaluate(el => { const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, r: r.right, b: r.bottom, w: r.width, h: r.height}; });
const near = (a, b, message) => assert.ok(Math.abs(a - b) <= 1, `${message}: ${a} vs ${b}`);

test('MODEL-SOURCE-LAYOUT: Billed to sits on the third column and is as tall as the source side', async t => {
  const {form, source} = await dialog(t, {width: 1280});
  for (const choice of ['codex', 'all', 'different']) {
    await source.selectOption(choice);
    const field = await box(form.locator('.ab-model-source'));
    const bill = await box(form.locator('.ab-bill'));
    const label = await box(form.locator('.ab-bill-k'));
    const thirdColumn = await box(form.locator('[data-run-config-section="limits"] .ab-launch-grid > .ab-launch-field').nth(4));
    near(bill.x, thirdColumn.x, `${choice}: card left edge = third column`); near(bill.r, thirdColumn.r, `${choice}: card right edge = third column`);
    near(label.y, (await box(form.locator('.ab-model-source'))).y, `${choice}: the two labels share a line`);
    const select = await box(source);
    near(bill.y, select.y, `${choice}: card top = select top`);
    if (choice !== 'codex') near(bill.b, field.b, `${choice}: card bottom = end of the help line`);
    else assert.ok(bill.b >= field.b - 1);                              // Codex adds a meter line, so the card may be taller
    assert.ok(field.r <= bill.x - 8, `${choice}: the two sides do not overlap`);
  }
});

test('MODEL-SOURCE-LAYOUT: the key form and the Agent models rows use the same three columns', async t => {
  const {form, source} = await dialog(t, {width: 1280});
  await source.selectOption('all');
  const columns = await form.locator('[data-run-config-section="limits"] .ab-launch-grid > .ab-launch-field').evaluateAll(els => els.slice(2, 5).map(e => { const r = e.getBoundingClientRect(); return [r.x, r.right]; }));
  const provider = await box(form.locator('[data-key-provider-field]'));
  const secret = await box(form.locator('[data-key-secret-field]'));
  near(provider.x, columns[0][0], 'Provider = first column'); near(secret.r, columns[2][1], 'API key ends on the third column');
  await source.selectOption('different');
  await form.locator('[data-keys-details]').evaluate(el => { el.open = true; });
  const role = form.locator('[data-key-role="eval"]');
  near((await box(role.locator('.ab-keys-role-pick'))).x, columns[1][0], 'Source = second column');
  near((await box(role.locator('[data-role-model-wrap], .ab-keys-role-model').first())).x, columns[2][0], 'Model = third column');
  near((await box(role.locator('.ab-keys-role-pick'))).r, columns[1][1], 'Source ends on the second column');
});

test('NEW-RUN-LIMITS: 05 keeps what you can change on the first row and what the task sets on the second', async t => {
  const {form} = await dialog(t, {width: 1280});
  const fields = await form.locator('[data-run-config-section="limits"] .ab-launch-grid > .ab-launch-field').evaluateAll(els => els.map(e => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y}; }));
  assert.equal(fields.length, 5);
  near(fields[0].y, fields[1].y, 'versions and run time share a row');
  near(fields[2].y, fields[3].y, 'robot time and steps share a row');near(fields[3].y, fields[4].y, 'steps and tokens share a row');
  near(fields[0].x, fields[2].x, 'the second row starts on the first column');
  assert.ok(fields[2].y > fields[0].y);
});

test('MODEL-SOURCE-LAYOUT-NARROW: at 390px the card stacks under the select, full width, and headings are left aligned', async t => {
  const {form, source, page} = await dialog(t, {width: 390});
  await source.selectOption('all');
  const select = await box(source), bill = await box(form.locator('.ab-bill'));
  near(bill.x, select.x, 'card left = select left'); near(bill.r, select.r, 'card right = select right');
  assert.ok(bill.y > select.b, 'the card is under the select');
  const overflow = await form.evaluate(el => { const body = el.closest('.ab-launch-body') || el; const room = body.clientWidth; return [...el.querySelectorAll('[data-run-config-section="models"] *')].filter(n => n.getClientRects().length && n.getBoundingClientRect().right > body.getBoundingClientRect().left + room + 1).map(n => n.tagName + '.' + n.className); });
  assert.deepEqual(overflow, []);                                     // nothing in 03 pokes out sideways
  const field = await box(form.locator('[data-keys-source]'));
  for (const section of ['models', 'limits']) near((await box(form.locator(`[data-run-config-section="${section}"] .ab-run-section-head h4`))).x, field.x, `${section} heading = field left`);
});

test('REMOTE-FIRST-RUN: a first run on Remote starts interactive with help, says the person is asked first, and folds the rest', async t => {
  const {form} = await dialog(t, {remote: ['pick_place_counter_to_sink']});
  assert.equal(await form.locator('select[name="v2_mode"]').inputValue(), 'interactive');
  assert.equal(await form.locator('select[name="v2_policy"]').inputValue(), 'reactive_help');
  assert.match(await form.locator('[data-policy-human-note]').innerText(), /comes to you first.*2 minutes/s);
  const local = await dialog(t);
  assert.equal(await local.form.locator('[data-policy-human-note]').count(), 0);
  assert.notEqual(await local.form.locator('select[name="v2_policy"]').inputValue(), 'reactive_help');
});
