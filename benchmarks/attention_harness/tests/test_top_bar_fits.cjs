// The top bar grows a row instead of widening the page. The hardest case is the Run page of a development test with the Remote
// tab: four tabs and the long flag need about 1416px. The main UI's rule for the bar outranks the phone rule in shell.css, so the
// bar once stayed on one row at every width and pushed the whole page sideways, on laptops as well as phones.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const ui = path.join(__dirname, '../ui');
const read = name => fs.readFileSync(path.join(ui, name), 'utf8');
const pages = {
  '/ui/': read('index.html').replace('data-mode="demo"', 'data-mode="live"').replace('data-mode="live"', 'data-mode="live" data-control-token="t" data-remote-tab="1"'),
  '/ui/home.html': read('home.html').replace('<body', '<body data-remote-tab="1"'),
  '/ui/memory.html': read('memory.html').replace('<body', '<body data-remote-tab="1"'),
};
const runOptions = fs.readFileSync(path.join(__dirname, 'fixtures/run_options_agent.json'), 'utf8');
let browser;
before(async () => { browser = await chromium.launch({executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox']}); });
after(async () => { await browser?.close(); });

async function open(t, url, width) {
  const page = await browser.newPage({viewport: {width, height: 844}}); t.after(() => page.close());
  if (process.env.UI_TARGET_TIMEOUT) page.setDefaultTimeout(Number(process.env.UI_TARGET_TIMEOUT));
  await page.route('**/*', route => {
    const {pathname} = new URL(route.request().url());
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: typeof body === 'string' ? body : JSON.stringify(body)});
    if (pages[pathname]) return route.fulfill({contentType: 'text/html', body: pages[pathname]});
    const asset = /^\/ui\/([\w.-]+\.(js|css))$/.exec(pathname);
    if (asset && fs.existsSync(path.join(ui, asset[1])))
      return route.fulfill({contentType: asset[2] === 'js' ? 'text/javascript; charset=utf-8' : 'text/css', body: read(asset[1])});
    if (pathname === '/api/run-options') return json(runOptions);
    if (pathname === '/api/runs') return json({runs: [], launches: []});
    if (pathname === '/api/notices') return json({generated_at: null, notices: []});
    return json({error: 'not in this fixture'}, 503);
  });
  await page.goto(`http://attention.test${url}`);
  await page.locator('.ab-shell-top .ab-pages a[data-page="remote"]').waitFor({state: 'attached'});
  await page.evaluate(() => { const flag = document.querySelector('.ab-shell-flag'); if (flag) flag.hidden = false; });  // A development run shows it.
  return page;
}
const measure = page => page.evaluate(async () => {
  await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
  return {page: document.documentElement.scrollWidth, screen: innerWidth, bar: document.querySelector('.ab-shell-top').offsetHeight,
    variable: getComputedStyle(document.documentElement).getPropertyValue('--sh-bar').trim()};
});

for (const [url, name] of [['/ui/', 'Run'], ['/ui/home.html', 'Home']]) for (const width of [1280, 1000, 861, 768, 390, 320]) test(`TOP-BAR: ${name} is no wider than a ${width}px screen`, async t => {
  const page = await open(t, url, width), seen = await measure(page);
  assert.ok(seen.page <= seen.screen, `the page is ${seen.page}px wide`);
  assert.equal(seen.variable, `${seen.bar}px`);
});

test('TOP-BAR: the Run page is the view these rules are for', async t => {
  const page = await open(t, '/ui/', 1280);
  assert.equal(await page.locator('#attentionbench-clear').getAttribute('data-operator-view'), 'true');
});

test('TOP-BAR: with room for everything the bar keeps its one row', async t => {
  const seen = await measure(await open(t, '/ui/', 1900));
  assert.equal(seen.bar, 68);
});

test('TOP-BAR: the task list sits below a bar of two rows', async t => {
  const page = await open(t, '/ui/', 1280), seen = await measure(page);
  assert.ok(seen.bar > 68, `the bar is ${seen.bar}px tall`);
  assert.equal(await page.locator('.ab-sidebar').evaluate(list => getComputedStyle(list).top), `${seen.bar + 10}px`);
});

for (const [url, name] of [['/ui/', 'Run'], ['/ui/home.html', 'Home']]) test(`TOP-BAR: on a phone the Notices panel of ${name} stays on the screen`, async t => {
  const page = await open(t, url, 390);
  await page.locator('.ab-shell-top .ab-notices-button').click();
  const panel = await page.locator('.ab-shell-top .ab-notices-panel').evaluate(node => { const box = node.getBoundingClientRect(); return {left: box.left, right: box.right}; });
  assert.ok(panel.left >= 0 && panel.right <= 390, `the panel runs from ${panel.left} to ${panel.right}`);
});

// Real checks carry a long sentence that may not wrap on a wide screen, and 64-character hashes.
test('TOP-BAR: on a phone the Automatic checks box of Memory stays on the screen', async t => {
  const page = await open(t, '/ui/memory.html', 390);
  await page.locator('#automatic-checks-body').evaluate(body => {
    body.innerHTML = `<div class="ac-head"><span class="ac-what">Checks</span><span class="ac-runs"><span>${'Separate runs, each on its own copy of the data, '.repeat(2)}</span></span></div><small>${'a'.repeat(64)}</small>`;
  });
  const seen = await measure(page);
  assert.ok(seen.page <= seen.screen, `the page is ${seen.page}px wide`);
});
