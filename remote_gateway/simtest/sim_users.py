"""Simulated people. Each has their own browser context, invite code and account, and uses the real page by clicking it.

Everything a person does or meets goes to events.jsonl as one line, and the monitor folds those lines into lanes. Nothing
is summarised away here: every failed step, refused request, page error and failed request is written down.
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

REPLY = 'Move the gripper lower and close it slowly on the middle of the object.'
READ_PAGE = '''(id) => {
  const L = window.Lobby, snap = L && L.snap, card = id && L ? L.card(id) : null;
  const q = s => document.querySelector(s), text = s => (q(s)?.textContent || '').trim();
  const frame = q('#ab-camera-frame');
  return {lobby: !!(L && L.enabled), robot: snap?.robot?.running?.nickname || null, queue_length: (snap?.queue || []).length, nickname: snap?.me?.nickname || null,
    card: card ? {state: card.state, position: card.queue_position, result: card.result} : null, frame: frame && !frame.hidden ? frame.src : null,
    reply: !!(q('[data-agent-reply]')?.offsetParent), viewing: (location.hash.match(/exp=(devexp-[a-f0-9]{24})/) || [])[1] || null, countdown: text('[data-agent-reply-countdown]'), message: text('#ab-live-message'), launch_message: text('#ab-launch-message'),
    readonly: !!q('[data-lobby-readonly]'), outcome: text('[data-operator-outcome]').slice(0, 120), stop_shown: !!(q('#ab-emergency-interrupt,#ab-cancel-run') && q('#ab-emergency-interrupt,#ab-cancel-run').offsetParent),
    picture: text('[data-picture-warning]')};
}'''
FETCH = '''async ([method, path, body, csrf]) => {
  const token = csrf ? (document.getElementById('attentionbench-clear')?.dataset.controlToken || '') : '';
  const r = await fetch(path, {method, credentials: 'same-origin', headers: {...(body === null ? {} : {'Content-Type': 'application/json'}), ...(token ? {'X-Attention-Token': token} : {})}, body: body === null ? undefined : JSON.stringify(body)});
  return {status: r.status, text: (await r.text()).slice(0, 300)};
}'''


class Log:
    """One line per thing that happened. Invite codes never reach this file."""
    def __init__(self, path, secrets=()):
        self.path, self.secrets = Path(path), tuple(secrets);self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a', encoding='utf-8', buffering=1)

    def write(self, **row):
        line = json.dumps(dict(at=round(time.time(), 3), **row), ensure_ascii=False)
        for secret in self.secrets:line = line.replace(secret, '[invite]')
        self.stream.write(line + '\n')


@dataclass
class Settings:
    base: str
    out: Path
    scale: float = 1.0  # How much longer than the fake's seconds a wait may last; a real run is minutes, a fake one seconds.
    task: str = 'pick_place_counter_to_sink'
    seed: int = 101
    wall_seconds: int = 1200
    versions: int = 2
    shot_seconds: float = 4
    drop_seconds: float = 15
    run_cap_seconds: float | None = None  # Real runs only: the owner stops the run after this long, so one person cannot use the whole test.
    headed: bool = False


class Person:
    def __init__(self, lane, name, role, code, browser, settings, log, shared):
        self.lane, self.name, self.role, self.code, self.browser, self.settings, self.log, self.shared = lane, name, role, code, browser, settings, log, shared
        self.context = self.page = None;self.run_id = None;self.expected = [];self.last = {};self.frames = set();self.polling = None;self.offline = False;self.stopped = False;self.warned = set();self.own_run = None

    # Writing it down
    def emit(self, kind, **row):
        self.log.write(lane=self.lane, name=self.name, role=self.role, kind=kind, **row)

    def check(self, name, ok, detail=''):
        self.emit('check', check=name, ok=bool(ok), detail=str(detail)[:300])
        return ok

    def problem(self, source, message, level='error'):
        self.emit('problem', source=source, level=level, message=str(message)[:500])

    @asynccontextmanager
    async def step(self, action, required=True):
        """One visible action. A failure is written with the page's own words and a picture, then stops this person unless it was optional."""
        started = time.time();self.emit('action', action=action, phase='start')
        try:
            yield
        except Exception as error:
            shot = await self.shot(f'error-{int(started)}')
            self.emit('action', action=action, phase='failed', seconds=round(time.time() - started, 1))
            self.problem('步骤失败', f'{action}：{type(error).__name__}: {str(error).splitlines()[0] if str(error) else ""}' + (f' 截图 {shot}' if shot else ''))
            if required:raise
        else:
            self.emit('action', action=action, phase='done', seconds=round(time.time() - started, 1))

    # The browser
    async def open(self):
        self.context = await self.browser.new_context(viewport=dict(width=1440, height=900), locale='en-US')
        self.page = await self.context.new_page()
        self.page.on('console', lambda m:self.problem('页面控制台', m.text, 'error') if m.type == 'error' and not self.ignorable_console(m.text) else None)
        self.page.on('pageerror', lambda e:self.problem('页面脚本异常', e))
        self.page.on('response', self.on_response)
        self.page.on('requestfailed', self.on_failed)
        self.polling = asyncio.create_task(self.poll())

    @staticmethod
    def ignorable_console(text):
        # The browser repeats every failed response here; those are reported from the response itself, once, with the route.
        return 'Failed to load resource' in text or 'fonts.g' in text

    def on_response(self, response):
        path = urlsplit(response.url).path
        if path == '/api/dev-v2/experiments' and response.request.method == 'POST':
            asyncio.create_task(self.read_created(response))
        if response.status < 400:return
        route = re.sub(r'devexp-[a-f0-9]{24}', '{run}', re.sub(r'cells/[a-f0-9]{64}', 'cells/{cell}', path))
        if any(status == response.status and part in path for status, part in self.expected):
            return self.emit('http', status=response.status, route=route, method=response.request.method, expected=True)
        # The page throws away a frame whose identity moved on and reads again: normal between tries, but still counted and shown.
        level = 'notice' if response.status in (404, 409) and route.endswith('/live/frame') else 'error'
        self.emit('http', status=response.status, route=route, method=response.request.method, expected=False, level=level)

    async def read_created(self, response):
        try:
            value = await response.json()
            if response.ok and value.get('experiment_id'):
                self.run_id = self.own_run = value['experiment_id'];self.emit('state', run_id=self.run_id)
        except Exception as error:
            self.problem('读创建结果', error)

    def on_failed(self, request):
        path = urlsplit(request.url).path
        text = request.failure or ''
        if 'ERR_ABORTED' in text or self.offline or 'fonts.g' in request.url or path.startswith('blob'):return  # The page aborts its own frame reads when the view changes.
        self.problem('请求失败', f'{request.method} {path}: {text}')

    async def shot(self, name='latest'):
        if not self.page or self.page.is_closed():return None
        folder = self.settings.out / 'shots';folder.mkdir(exist_ok=True)
        path = folder / f'{self.lane}.jpg' if name == 'latest' else folder / f'{self.lane}-{name}.jpg'
        try:
            await self.page.screenshot(path=path, type='jpeg', quality=45, timeout=8000)
            return path.name
        except Exception:return None

    async def poll(self):
        """Read what this person's page says, write down what changed, and keep a current picture of it."""
        last_shot = 0
        while not self.stopped:
            await asyncio.sleep(1)
            if not self.page or self.page.is_closed():continue
            try:
                now = await self.page.evaluate(READ_PAGE, self.run_id)
            except Exception:continue
            if now['frame']:self.frames.add(now['frame'])
            if now['reply'] and now['viewing'] != self.own_run and now['viewing'] not in self.warned:
                self.warned.add(now['viewing'])  # Whoever is asked must be the owner: anyone else who answers only gets a refusal.
                self.problem('别人的求助', f'我不是 run {now["viewing"][-8:] if now["viewing"] else "?"} 的主人，页面却给了回复框')
            seen = {k: v for k, v in now.items() if k not in ('frame', 'countdown')}
            seen['live'] = bool(now['frame'])
            if seen != self.last:
                self.emit('state', run_id=self.run_id, page=seen)
                self.last = seen
            if now['countdown']:self.emit('state', countdown=now['countdown'])
            if time.time() - last_shot >= self.settings.shot_seconds:
                last_shot = time.time();await self.shot()

    async def read(self):
        return await self.page.evaluate(READ_PAGE, self.run_id)

    async def until(self, what, test, seconds):
        deadline = time.time() + seconds * self.settings.scale
        while time.time() < deadline:
            if self.shared.get('abort'):raise RuntimeError(f'测试被安全线叫停：{self.shared["abort"]}')
            now = await self.read()
            if test(now):return now
            await asyncio.sleep(.5)
        raise TimeoutError(f'等了 {seconds * self.settings.scale:.0f} 秒没有等到：{what}；页面最后是 {json.dumps({k: v for k, v in now.items() if k != "frame"}, ensure_ascii=False)[:300]}')

    async def call(self, method, path, body=None, *, csrf=True):
        return await self.page.evaluate(FETCH, [method, path, body, csrf])

    # What a person does
    async def login(self):
        async with self.step('登录'):
            await self.page.goto(self.settings.base + '/login')
            await self.page.fill('input[name=code]', self.code)
            await self.page.click('button[type=submit]')
            await self.page.wait_for_url('**/ui/**', timeout=15000)

    async def wrong_code(self):
        async with self.step('输入错误邀请码'):
            self.expected.append((303, '/login'))
            await self.page.goto(self.settings.base + '/login')
            await self.page.fill('input[name=code]', 'not-a-real-code')
            await self.page.click('button[type=submit]')
            await self.page.wait_for_url('**/login?failed=1', timeout=10000)
            self.check('错误邀请码被拒绝并显示提示', 'That code is not valid.' in await self.page.inner_text('body'))

    async def lobby(self):
        async with self.step('看大厅'):
            await self.page.goto(self.settings.base + '/ui/')
            now = await self.until('大厅出现', lambda s:s['lobby'], 20)
            self.emit('state', nickname=now['nickname'])
            await self.page.wait_for_selector('[data-lobby-pin],[data-lobby-queue]', timeout=15000)
            await self.page.click('[data-lb-mode="queue"]')
            await self.page.wait_for_selector('[data-lobby-queue]', timeout=5000)
            await self.page.click('[data-lb-mode="all"]')
            self.check('大厅显示机器人和队列', await self.page.locator('[data-lobby-pin]').count() == 1)

    async def new_run(self, policy='reactive_help', mode='interactive'):
        async with self.step(f'开 run（{policy}）'):
            await self.page.wait_for_selector('[data-open-launch]:not([disabled])', timeout=20000)
            await self.page.locator('[data-open-launch]:not([disabled])').first.click()
            form = self.page.locator('dialog[open] form, form:has([data-start-yes])').first
            await form.locator('select[name=task_id]').wait_for(timeout=10000)
            await form.locator('select[name=seed]').select_option(str(self.settings.seed))
            await form.locator('select[name=v2_mode]').select_option(mode)
            await form.locator('select[name=v2_policy]').select_option(policy)
            async with self.page.expect_response(lambda r:urlsplit(r.url).path == '/api/dev-v2/experiments' and r.request.method == 'POST', timeout=30000) as made:
                await form.locator('[data-start-yes]').click()
            response = await made.value
            if not response.ok:raise RuntimeError(f'New run 被拒绝 HTTP {response.status}: {(await response.text())[:200]}')
            await self.until('run 编号', lambda s:self.run_id is not None, 10)
            self.shared.setdefault('runs', {})[self.lane] = self.run_id

    async def wait_robot(self, seconds=1200):
        async with self.step('排队等待'):
            now = await self.until('轮到我，开始运行', lambda s:(s['card'] or {}).get('state') in ('running', 'finished', 'cancelled', 'created'), seconds)
            if now['card']['state'] == 'created':raise RuntimeError(f'run 被移出队列但没有启动，页面只写着：{now["outcome"][:90]}')

    async def watch_live(self, seconds=60):
        async with self.step('看实时画面'):
            self.frames.clear()
            now = await self.until('实时画面出现，或 run 已结束', lambda s:len(self.frames) >= 2 or (s['card'] or {}).get('state') in ('finished', 'cancelled'), seconds)
            if len(self.frames) >= 2:self.check('实时画面在变化', True, f'{len(self.frames)} 张不同的帧')
            else:self.problem('没有看到画面', f'run 已结束，只看到 {len(self.frames)} 张帧：{now["card"]}', 'notice')

    async def answer(self, how, seconds=300):
        """A real help request, answered as this person answers: a reply, a skip, or nothing."""
        async with self.step({'reply': '回复求助', 'skip': '跳过求助', 'ignore': '不理求助'}[how]):
            now = await self.until('机器人求助，或 run 已结束', lambda s:s['reply'] or (s['card'] or {}).get('state') in ('finished', 'cancelled'), seconds)
            if not now['reply']:  # A model may simply never ask. Said plainly, not counted as an answer.
                self.emit('state', no_help=True);self.problem('未触发求助', 'run 结束前机器人没有求助', 'notice')
                return
            form = self.page.locator('[data-agent-reply]')
            if how == 'reply':
                await form.locator('textarea').fill(REPLY)
                await form.locator('button[type=submit]').click()
            elif how == 'skip':
                await form.locator('[data-hr-skip]').click()
            await self.until('求助结束', lambda s:not s['reply'], 150)

    async def stop_run(self):
        async with self.step('Cancel run'):
            button = self.page.locator('#ab-emergency-interrupt:visible,#ab-cancel-run:visible').first
            if await button.is_disabled(timeout=10000):raise RuntimeError('Stop run 按钮是灰的，页面上没有别的取消入口')
            await button.click(timeout=10000)
            dialog = self.page.locator('[data-end-run-dialog]')
            await dialog.locator('button[value=end]').click(timeout=5000)
            now = await self.until('run 已取消', lambda s:(s['card'] or {}).get('state') == 'cancelled', 60)
            self.emit('state', run_id=self.run_id, page=dict(now, frame=None, live=False))

    async def finish(self, seconds=900):
        cap = self.settings.run_cap_seconds
        if cap:
            try:
                async with self.step(f'最多再看 {cap:.0f} 秒', required=False):
                    await self.until('run 结束', lambda s:(s['card'] or {}).get('state') in ('finished', 'cancelled'), cap / self.settings.scale)
            finally:
                if ((await self.read())['card'] or {}).get('state') == 'running':await self.stop_run()
        async with self.step('等 run 结束'):
            now = await self.until('run 结束', lambda s:(s['card'] or {}).get('state') in ('finished', 'cancelled'), seconds)
            self.emit('state', run_id=self.run_id, page=dict(now, frame=None, live=False), finished=(now['card'] or {}).get('result') or (now['card'] or {}).get('state'))

    async def leave_line(self):
        """Leave the queue from the page. If the page offers no way, that is written down, then the same request is sent so the line can be tested."""
        await self.until('页面显示排队', lambda s:(s['card'] or {}).get('state') == 'queued', 30)
        async with self.step('排队时点 Stop run', required=False):
            button = self.page.locator('#ab-emergency-interrupt:visible,#ab-cancel-run:visible').first
            if await button.is_disabled(timeout=10000):raise RuntimeError('排队时 Stop run 是灰的，用户无法从页面离开队列')
            await button.click(timeout=10000)
            await self.page.locator('[data-end-run-dialog] button[value=end]').click(timeout=5000)
            await self.until('已离开队列', lambda s:(s['card'] or {}).get('state') == 'cancelled', 30)
            return
        async with self.step('直接请求离开队列'):
            answer = await self.call('POST', f'/api/dev-v2/experiments/{self.run_id}/cancel', {})
            self.check('网关接受离开队列', answer['status'] == 200, answer)
            now = await self.until('已离开队列', lambda s:(s['card'] or {}).get('state') == 'cancelled', 30)
            self.emit('state', run_id=self.run_id, page=dict(now, frame=None, live=False))

    async def follow_robot(self):
        """A guest who watches whoever is on the robot, until everyone is done."""
        async with self.step('访客跟着看机器人'):
            current = None;seen = 0
            while self.shared.get('done', 0) < self.shared.get('people', 0) and not self.shared.get('abort'):
                running = await self.page.evaluate('() => window.Lobby?.snap?.robot?.running?.experiment_id || null')
                if running and running != current:
                    current = running;seen += 1;self.run_id = running
                    await self.page.evaluate('(id) => window.Lobby.open(id)', running)
                await asyncio.sleep(2)
            self.check('访客看到了别人的实时画面', len(self.frames) >= 2, f'看了 {seen} 个 run，{len(self.frames)} 张帧')

    async def drop_line(self, seconds=None):
        """Lose the network, come back, and see that the run and the picture came back too."""
        seconds = seconds or self.settings.drop_seconds
        async with self.step(f'断网 {seconds:g} 秒'):
            self.offline = True;self.emit('state', offline=True)
            await self.context.set_offline(True)
            await asyncio.sleep(seconds)
            await self.context.set_offline(False)
            self.emit('state', offline=False);self.offline = False
        async with self.step('断线后恢复'):
            await self.until('大厅恢复', lambda s:s['lobby'], 30)
            now = await self.read()
            self.check('断线后 run 还在我名下', now['card'] is not None, now['card'])

    async def reopen(self):
        """Close the page and open it again later: the cookie and the place in line must survive."""
        async with self.step('关闭页面再打开'):
            await self.page.close()
            await asyncio.sleep(3)
            self.page = await self.context.new_page()
            self.page.on('response', self.on_response);self.page.on('pageerror', lambda e:self.problem('页面脚本异常', e));self.page.on('requestfailed', self.on_failed)
            await self.page.goto(f'{self.settings.base}/ui/#exp={self.run_id}')
            now = await self.until('页面恢复', lambda s:s['card'] is not None, 30)
            self.check('重开页面后位置还在', now['card']['state'] in ('queued', 'running', 'finished'), now['card'])

    async def someone_elses_run(self, seconds=600):
        async def find():
            deadline = time.time() + seconds * self.settings.scale
            while time.time() < deadline:
                others = [r for lane, r in self.shared.get('runs', {}).items() if lane != self.lane]
                if others:return others[0]
                await asyncio.sleep(1)
            raise TimeoutError('没有别人的 run 可以试')
        return await find()

    async def refused(self, name, method, path, body, want, *, csrf=True):
        """An attempt that must fail, and fail the right way. Anything else is an error."""
        part = urlsplit(path).path
        self.expected.append((want, part))
        answer = await self.call(method, path, body, csrf=csrf)
        self.check(name, answer['status'] == want, f'HTTP {answer["status"]}，应为 {want}：{answer["text"][:120]}')

    async def overstep(self):
        target = await self.someone_elses_run()
        base = f'/api/dev-v2/experiments/{target}'
        async with self.step('无权限操作'):
            await self.refused('不能取消别人的 run', 'POST', base + '/cancel', {}, 404)
            await self.refused('不能启动别人的 run', 'POST', base + '/run', {}, 404)
            await self.refused('不能回复别人的求助', 'POST', base + '/help-reply', dict(cell='c' * 64, execution_id='e000001', content='x'), 404)
            await self.refused('没有 CSRF 不能写', 'POST', base + '/cancel', {}, 403, csrf=False)
            await self.refused('运维接口不存在', 'GET', '/api/orchestrator/snapshot', None, 404)
            await self.refused('Codex 额度不可读', 'GET', '/api/codex-quota', None, 404)
            await self.refused('Memory 接口不存在', 'GET', '/api/memories', None, 404)
            await self.refused('路径穿越被拒绝', 'GET', base + '/cells/%2e%2e%2f%2e%2e/view', None, 404)
            await self.refused('lab 这个 key 名字不能占用', 'PUT', '/api/provider/credentials/lab', dict(provider='openai', key='x'), 400)
        async with self.step('看别人的 run'):
            await self.page.goto(f'{self.settings.base}/ui/#exp={target}')
            self.run_id, keep = target, self.run_id
            await self.page.reload()
            await asyncio.sleep(4)
            now = await self.read()
            self.check('别人的 run 没有回复框', not now['reply'], now['reply'])
            self.check('别人的 run 没有 Stop', not now['stop_shown'], now['stop_shown'])
            self.run_id = keep

    async def guest_tries(self):
        target = await self.someone_elses_run()
        async with self.step('访客尝试写操作'):
            await self.refused('访客不能取消 run', 'POST', f'/api/dev-v2/experiments/{target}/cancel', {}, 401, csrf=False)
            await self.refused('访客不能开 run', 'POST', '/api/dev-v2/experiments', dict(selection={}), 401, csrf=False)
        async with self.step('访客看别人的 run'):
            await self.page.goto(f'{self.settings.base}/ui/#exp={target}')
            await asyncio.sleep(5)

    async def give_back(self):
        """A person who stopped early must not leave a run on the robot or in the line."""
        if not self.own_run or not self.page or self.page.is_closed():return
        try:
            state = ((await self.read())['card'] or {}).get('state')
            if state in ('queued', 'running'):
                answer = await self.call('POST', f'/api/dev-v2/experiments/{self.own_run}/cancel', {})
                self.emit('action', action=f'中断后取消自己的 run（HTTP {answer["status"]}）', phase='done')
        except Exception as error:
            self.problem('中断后取消失败', f'{self.own_run}: {error}')

    async def close(self):
        self.stopped = True
        if self.polling:self.polling.cancel()
        await self.shot()
        if self.context:await self.context.close()


# Who does what. Each is a short story; the run page and the queue are the same page for all of them.
async def careful(p):
    await p.new_run();await p.wait_robot();await p.watch_live();await p.answer('reply');await p.finish()

async def skipper(p):
    await p.new_run();await p.wait_robot();await p.answer('skip');await p.finish()

async def silent(p):
    await p.new_run();await p.wait_robot();await p.answer('ignore');await p.finish()

async def quitter(p):
    await p.new_run();await p.wait_robot();await p.watch_live();await p.stop_run()

async def leaves_line(p):
    await p.new_run();await p.leave_line()

async def flaky(p):
    await p.new_run();await p.wait_robot();await p.watch_live();await p.drop_line();await p.answer('reply');await p.watch_live();await p.finish()

async def returns_later(p):
    await p.new_run();await p.reopen();await p.wait_robot();await p.answer('reply');await p.finish()

async def overstepper(p):
    await p.overstep();await p.new_run(policy='autonomous');await p.refused('一人只能有一个进行中的 run', 'POST', '/api/dev-v2/experiments',
        dict(selection=dict(request_key='second-run-attempt', tasks=[p.settings.task], seeds=[p.settings.seed], policies=['autonomous'], mode='interactive', help_budget=1,
                            agent_provider=dict(roles={r: dict(credential='lab', provider='parcc', model='parcc/GLM') for r in ('dev', 'eval', 'advisor')})), start=True), 409)
    await p.wait_robot();await p.finish()

STORIES = {'认真回复': careful, '跳过求助': skipper, '不回复': silent, '中途取消': quitter, '排队时取消': leaves_line, '断线重连': flaky, '关页再回来': returns_later, '越权尝试': overstepper}


async def live_one(person, story, delay):
    await asyncio.sleep(delay)
    try:
        await person.open()
        person.emit('state', status_hint='started')
        if story == '访客':
            await person.lobby();await person.guest_tries();await person.follow_robot()
        else:
            if story == '越权尝试':await person.wrong_code()
            await person.login();await person.lobby();await STORIES[story](person)
        person.emit('done', ok=True)
    except Exception as error:
        person.emit('done', ok=False, error=f'{type(error).__name__}: {str(error)[:300]}')
        await person.give_back()
    finally:
        if story != '访客':person.shared['done'] = person.shared.get('done', 0) + 1
        try:await person.close()
        except Exception as error:person.problem('关闭浏览器', error)


async def run_all(settings, people, log_path, secrets=(), shared=None):
    """people: (lane, name, story, code). Returns when everyone is done. shared['abort'] set by a watchdog stops everyone."""
    from playwright.async_api import async_playwright
    log = Log(log_path, secrets);shared = shared if shared is not None else {};shared['people'] = sum(1 for p in people if p[2] != '访客')
    log.write(lane='-', kind='begin', people=[dict(lane=p[0], name=p[1], story=p[2]) for p in people], base=settings.base)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel='chrome', headless=not settings.headed, args=['--disable-gpu', '--mute-audio'])
        try:
            await asyncio.gather(*(live_one(Person(lane, name, story, code, browser, settings, log, shared), story, index * 2) for index, (lane, name, story, code) in enumerate(people)))
        finally:
            await browser.close()
    log.write(lane='-', kind='end')
