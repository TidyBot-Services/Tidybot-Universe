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
const presets = ['deepseek', 'openai', 'parcc', 'openai-compatible'].map(id => ({id, label: id, needs_key: true, suggested_models: ['m1'], thinking: null}));
let browser;
before(async () => { browser = await chromium.launch({executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox']}); });
after(async () => { await browser?.close(); });

async function dialog(t, {credentials = [], width = 1280} = {}) {
  const page = await browser.newPage({viewport: {width, height: 2400}}); t.after(() => page.close());
  if (process.env.UI_TARGET_TIMEOUT) page.setDefaultTimeout(Number(process.env.UI_TARGET_TIMEOUT));
  await page.route('**/*', route => {
    const {pathname} = new URL(route.request().url());
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: typeof body === 'string' ? body : JSON.stringify(body)});
    if (pathname === '/ui/') return route.fulfill({contentType: 'text/html', body: html});
    const asset = /^\/ui\/([\w.-]+\.(js|css))$/.exec(pathname);
    if (asset && fs.existsSync(path.join(ui, asset[1])))
      return route.fulfill({contentType: asset[2] === 'js' ? 'text/javascript; charset=utf-8' : 'text/css', body: fs.readFileSync(path.join(ui, asset[1]))});
    if (pathname === '/api/run-options') return json(runOptions);
    if (pathname === '/api/runs') return json({runs: [], launches: []});
    if (pathname === '/api/provider/presets') return json(presets);
    if (pathname === '/api/provider/credentials') return json(credentials);
    if (pathname === '/api/codex-quota') return json({used_percent: 50, window_minutes: 10080});
    return json({error: 'not in this fixture'}, 503);
  });
  await page.goto('http://attention.test/ui/');
  await page.locator('[data-open-launch]').click();
  const form = page.locator('#ab-launch-form');
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
  await page.keyboard.press('ArrowDown');                      // codex -> the next enabled option (My key for all agents)
  assert.deepEqual(await page.evaluate(() => window.__events), ['input:all', 'change:all']);
  assert.equal(await source.inputValue(), 'all');
  assert.equal(await radio(), 'all');
  assert.equal(await form.locator('[data-key-value]').isVisible(), true);   // the add-key form appeared
  await page.keyboard.press('ArrowDown');
  assert.equal(await source.inputValue(), 'different');
  assert.equal(await radio(), 'different');
  await page.keyboard.press('ArrowUp'); await page.keyboard.press('ArrowUp');
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

test('MODEL-SOURCE-PARCC: PARCC is greyed in Agent mode and chosen normally in One call', async t => {
  const {page, form, source, radio} = await dialog(t);
  const parcc = source.locator('option[value="parcc"]');
  assert.deepEqual(await parcc.evaluate(o => [o.disabled, o.textContent]), [true, 'Lab models · PARCC (GLM + Qwen) · One call only']);
  await form.locator('select[name="dev_mode"]').selectOption('one_call');
  await page.waitForFunction(() => !document.querySelector('#ab-launch-form [data-keys-source] option[value="parcc"]').disabled);
  await source.selectOption('parcc');
  assert.equal(await source.inputValue(), 'parcc');
  assert.equal(await form.locator('select[name="model_source"]').inputValue(), 'parcc');
  assert.equal(await radio(), 'lab');
  await source.selectOption('codex');
  assert.equal(await source.inputValue(), 'codex');
  assert.equal(await form.locator('select[name="model_source"]').inputValue(), 'codex');
});

// The same trap anywhere else in the dialog: a control the form repaints on input must still hold what the user chose.
for (const name of ['dev_model', 'eval_model', 'advisor_model', 'v2_policy', 'v2_mode', 'seed', 'task_id', 'run_type']) {
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
