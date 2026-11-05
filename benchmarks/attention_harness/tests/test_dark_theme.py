import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
UI = ROOT / 'benchmarks/attention_harness/ui'


def _generator():
    spec = importlib.util.spec_from_file_location('gen_dark_theme', ROOT / 'scripts/gen_dark_theme.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dark_block_is_in_sync_with_light_css():
    html = (UI / 'index.html').read_text(encoding='utf-8')
    block = re.search(r'<style id="ab-dark-theme">\n(.*?)</style>', html, re.S)
    assert block, 'index.html has no <style id="ab-dark-theme"> block'
    light = re.sub(r'<style id="ab-dark-theme">.*?</style>\n?', '', html, flags=re.S)
    assert block.group(1) == _generator().build(light), 'light CSS changed: run python3 scripts/gen_dark_theme.py'


def test_dark_rules_are_scoped_to_the_dark_theme():
    html = (UI / 'index.html').read_text(encoding='utf-8')
    css = re.search(r'<style id="ab-dark-theme">\n(.*?)</style>', html, re.S).group(1)
    for rule in re.findall(r'^[^@\s}][^{]*\{', css, re.M):
        for sel in _generator().split_sels(rule[:-1]):
            assert 'data-theme="dark"' in sel, sel


def test_theme_toggle_is_wired():
    # The Light | Dark switch and the page switcher come from the shared shell (2026-10-23).
    js = (UI / 'live.js').read_text(encoding='utf-8')
    shell = (UI / 'shell.js').read_text(encoding='utf-8')
    assert 'const themeButton=shell?shell.themeSwitch():' in js
    assert 'shell.onTheme(applyMainTheme)' in js
    assert "localStorage.getItem('attention.theme')" in js  # fallback without shell.js
    assert "THEME_KEY = 'attention.theme', OLD_MEMORY_KEY = 'attention.memory.theme'" in shell
    assert "write('localStorage', THEME_KEY, theme);" in shell and "write('localStorage', OLD_MEMORY_KEY, theme);" in shell
    assert 'actions.append(devTag,alertsButton,noticeHost,themeButton)' in js


def test_all_three_pages_load_the_shared_shell():
    for page in ('index.html', 'home.html', 'memory.html'):
        html = (UI / page).read_text(encoding='utf-8')
        assert 'href="./shell.css"' in html and 'src="./shell.js"' in html, page
    for page in ('home.html', 'memory.html'):
        html = (UI / page).read_text(encoding='utf-8')
        assert 'class="ab-pages" data-shell-pages=' in html and 'data-shell-themes' in html, page
    css = (UI / 'shell.css').read_text(encoding='utf-8')
    assert '@view-transition{navigation:auto}' in css
    assert 'prefers-reduced-motion:reduce' in css


def test_generator_maps_palette():
    g = _generator()
    assert g.map_value('background', '#ece9e2') == '#15171a'
    assert g.map_value('color', '#242822') == '#ecebe6'
    assert g.map_value('color', '#fff') == '#ffffff'
    assert g.map_value('background', '#fff') == '#23272c'
    assert g.split_sels('#a :is(.b,.c), .d') == ['#a :is(.b,.c)', ' .d']


def _run_dialog_css(html):
    block = re.search(r'<style id="ab-run-dialog">\n(.*?)</style>', html, re.S)
    assert block, 'index.html has no <style id="ab-run-dialog"> block'
    return block.group(1)


def test_run_dialog_css_lives_outside_the_generated_dark_block():
    # The New run dialog styles were once appended inside ab-dark-theme and deleted by the next regeneration.
    html = (UI / 'index.html').read_text(encoding='utf-8')
    css = _run_dialog_css(html)
    assert html.index('<style id="ab-run-dialog">') > html.index('<style id="ab-dark-theme">')
    assert '.ab-launch-source' in css and '#run-config-summary' in css
    assert '@media(max-width:760px)' in css
    dark = re.search(r'<style id="ab-dark-theme">\n(.*?)</style>', html, re.S).group(1)
    assert 'ab-launch-source' not in dark


def test_regenerating_the_dark_theme_keeps_the_run_dialog_css(tmp_path):
    html = (UI / 'index.html').read_text(encoding='utf-8')
    page = tmp_path / 'index.html'
    # force a regeneration from scratch: empty the dark block, then rebuild it
    page.write_text(re.sub(r'(<style id="ab-dark-theme">).*?(</style>)', r'\1\n\2', html, flags=re.S), encoding='utf-8')
    _generator().main(str(page))
    assert page.read_text(encoding='utf-8') == html
