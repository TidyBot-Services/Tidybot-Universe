"""Regenerate the operator UI dark theme (<style id="ab-dark-theme"> in ui/index.html).

Run after any change to the light theme CSS:  python3 scripts/gen_dark_theme.py

Every rule in the light theme blocks that sets a colour is re-emitted under a
[data-theme="dark"] scope with the colour mapped to the dark palette approved by
the user (charcoal surfaces, warm light text, orange accent, salmon failures,
soft green success). Rules without colours are not repeated.
"""
import colorsys, re, sys
from pathlib import Path

BLOCK_IDS = ('ab-industrial-theme', 'operator-view-presentation', 'operator-polish')

# light -> dark, chosen from the approved preview palette
TABLE = {
    'ece9e2': '15171a', 'f8f6f0': '1d2024', 'e9e6de': '262a2f', 'ebe8df': '262a2f',
    'eeeede': '262a2f', 'f3ead6': '2e2a22', 'f4e2c8': '2b2520', 'f3e2d6': '3a2824',
    'faf9f1': '262a2f', 'eceadd': '22262b', 'eae9dd': '24282c', 'e6e3da': '2a2e33',
    'c5c8b8': '3a3f45', 'd3d4c6': '33383d', 'aeb2a2': '4b5056', 'b3b8a6': '40454b',
    'a9ae9f': '4b5056', '939b89': '5a5f64',
    '242822': 'ecebe6', '686e64': 'a3a199', '0b0d0b': '000000',
    'd94d27': 'ec6a43', 'a23f32': 'e08670', '48683b': '58b583', 'b7791f': 'e0a24a',
    '8a5a12': 'e0a24a', '7a5212': 'd9a55a', '71634a': 'b8a78a',
}
MEDIA = re.compile(r'op-loading|op-stage|op-video|ab-station-camera')
TEXTISH = {'color', 'fill', '-webkit-text-fill-color', 'caret-color', 'text-decoration-color'}
SHADOWISH = {'box-shadow', 'text-shadow'}
COLOR_PROPS = re.compile(r'^(color|background(-color|-image)?|border(-(top|right|bottom|left))?(-color)?|outline(-color)?|box-shadow|text-shadow|fill|stroke|-webkit-text-fill-color|caret-color|text-decoration(-color)?|column-rule(-color)?|accent-color|--[\w-]+)$')
HEX = re.compile(r'#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})\b')
RGB = re.compile(r'rgba?\(([^)]*)\)')


def expand(h):
    if len(h) in (3, 4):
        h = ''.join(c * 2 for c in h)
    return h.lower()


def fallback(rgb, prop):
    r, g, b = (int(rgb[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    text = prop in TEXTISH
    if s < 0.15 or max(r, g, b) - min(r, g, b) < 0.1:  # HLS saturation overstates pale creams
        # warm light greys would come out olive; use the charcoal palette's cool hue
        h, s = 0.59, min(s, 0.08)
        if text:
            l2 = max(0.36, min(0.92, 1.05 - l))
        else:
            l2 = 0.10 + (1 - l) * 0.5 if l > 0.5 else 0.95 - l * 0.6
    elif text:
        # coloured text must stay readable on charcoal
        l2 = min(0.82, max(l, 0.68))
    elif l > 0.8:
        l2, s = 0.2, s * 0.5
    elif l < 0.45:
        l2 = min(0.72, l + 0.28)
    else:
        l2 = l
    r, g, b = colorsys.hls_to_rgb(h, max(0, min(1, l2)), s)
    return '%02x%02x%02x' % (round(r * 255), round(g * 255), round(b * 255))


def map_hex(h, prop):
    h = expand(h)
    base, alpha = h[:6], h[6:]
    shadow = prop in SHADOWISH or 'shadow' in prop
    if base in ('ffffff', 'fffdf5'):
        if prop in TEXTISH:
            return '#' + h
        if shadow:
            return '#ffffff0d'
        return '#23272c' + alpha
    if shadow:
        a = int(alpha, 16) if alpha else 0x66
        return '#000000%02x' % min(255, int(a * 1.8) + 10)
    new = TABLE.get(base) or fallback(base, prop)
    if alpha:
        a = int(alpha, 16)
        chromatic = base in ('d94d27', 'a23f32', '48683b', 'b7791f')
        if chromatic and a < 0x40:
            a = min(0x60, int(a * 1.7) + 4)
        return '#%s%02x' % (new, a)
    return '#' + new


WHITE = re.compile(r'#fff(fff)?|#fffdf5|white|var\(--op-paper[^)]*\)|var\(--panel[^)]*\)', re.I)
INK = re.compile(r'var\(--(op-ink|text|ink)\b|#242822', re.I)


def on_ink(old, new):
    if INK.search(old):
        return True
    m = HEX.search(new)
    if not m or 'gradient' in new:
        return False
    h = expand(m.group(1))[:6]
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hls(r, g, b)[1] > 0.75 and h not in ('ffffff', 'fffdf5')


def map_rgb(m, prop):
    parts = [p.strip() for p in m.group(1).replace('/', ',').split(',')]
    try:
        r, g, b = (round(float(x)) for x in parts[:3])
    except ValueError:
        return m.group(0)
    mapped = map_hex('%02x%02x%02x' % (r, g, b), prop)[1:7]
    rr, gg, bb = (int(mapped[i:i + 2], 16) for i in (0, 2, 4))
    if len(parts) > 3:
        return 'rgba(%d,%d,%d,%s)' % (rr, gg, bb, parts[3])
    return 'rgb(%d,%d,%d)' % (rr, gg, bb)


def map_value(prop, value):
    if prop == 'color-scheme':
        return value.replace('light', 'dark')
    v = HEX.sub(lambda m: map_hex(m.group(1), prop), value)
    return RGB.sub(lambda m: map_rgb(m, prop), v)


def scope(sel):
    sel = sel.strip()
    if not sel:
        return None
    if sel.startswith(('html', 'body')):
        return re.sub(r'^html', 'html[data-theme="dark"]', sel) if sel.startswith('html') else 'html[data-theme="dark"] ' + sel
    if ':root' in sel:
        return None
    if '#attentionbench-clear' in sel:
        return sel.replace('#attentionbench-clear', '#attentionbench-clear[data-theme="dark"]', 1)
    return 'html[data-theme="dark"] ' + sel


def split_sels(prelude):
    # commas inside :is()/:not()/:has() do not separate selectors
    out, depth, cur = [], 0, ''
    for ch in prelude:
        depth += ch == '(' or -(ch == ')') if ch in '()' else 0
        if ch == ',' and depth == 0:
            out.append(cur)
            cur = ''
        else:
            cur += ch
    out.append(cur)
    return out


def split_decls(body):
    out, depth, cur = [], 0, ''
    for ch in body:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ';' and depth == 0:
            out.append(cur)
            cur = ''
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def walk(css):
    """Yield (prelude, body) top-level items; nested at-rules are returned with raw body."""
    i, n = 0, len(css)
    while i < n:
        j = css.find('{', i)
        if j < 0:
            break
        prelude = css[i:j].strip()
        depth, k = 1, j + 1
        while k < n and depth:
            if css[k] == '{':
                depth += 1
            elif css[k] == '}':
                depth -= 1
            k += 1
        yield prelude, css[j + 1:k - 1]
        i = k


def convert(css):
    out = []
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    for prelude, body in walk(css):
        if prelude.startswith('@media') or prelude.startswith('@supports'):
            inner = convert(body)
            if inner.strip():
                out.append(prelude + '{' + inner + '}')
            continue
        if prelude.startswith('@'):
            continue
        if MEDIA.search(prelude):
            continue  # camera stage and loading screen are dark in both themes
        # Every colour declaration of a rule is re-emitted (mapped or verbatim) so
        # the dark copies keep the same cascade order among themselves; emitting
        # only the hex ones let a base rule's dark copy beat a more specific
        # light rule that uses currentColor or var().
        decls, changed, inverted = [], False, False
        for d in split_decls(body):
            if ':' not in d:
                continue
            prop, val = d.split(':', 1)
            prop, val = prop.strip().lower(), val.strip()
            if not (COLOR_PROPS.match(prop) or prop == 'color-scheme'):
                continue
            new = map_value(prop, val)
            changed |= new != val
            if prop in ('background', 'background-color', 'fill') and on_ink(val, new):
                inverted = True
            decls.append((prop, new))
        if not decls:
            continue
        if inverted:
            # light theme paired ink surfaces with white text; ink turns light in
            # the dark theme, so the text has to turn dark
            decls = [(p, '#15171a' if p in TEXTISH and WHITE.fullmatch(v.replace('!important', '').strip()) else v)
                     for p, v in decls]
        decls = [p + ':' + v for p, v in decls]
        sels = [s for s in (scope(x) for x in split_sels(prelude)) if s]
        if sels:
            out.append(','.join(sels) + '{' + ';'.join(decls) + '}')
    return '\n'.join(out)


def build(html):
    css = []
    for bid in BLOCK_IDS:
        m = re.search(r'<style id="%s">(.*?)</style>' % re.escape(bid), html, re.S)
        if not m:
            sys.exit('missing block ' + bid)
        css.append(m.group(1))
    m = re.search(r'<style id="ab-industrial-theme">.*?</style>\s*<style>(.*?)</style>', html, re.S)
    if m:
        css.insert(1, m.group(1))
    head = ('html[data-theme="dark"],html[data-theme="dark"] body{background:#15171a;color-scheme:dark}\n'
            '#attentionbench-clear[data-theme="dark"]{color-scheme:dark}\n')
    return head + '\n'.join(convert(c) for c in css) + '\n'


def main(path):
    html = open(path, encoding='utf-8').read()
    light = re.sub(r'<style id="ab-dark-theme">.*?</style>\n?', '', html, flags=re.S)
    dark = build(light)
    new, n = re.subn(r'(<style id="ab-dark-theme">).*?(</style>)', lambda m: m.group(1) + '\n' + dark + m.group(2), html, flags=re.S)
    if n != 1:
        sys.exit('expected exactly one <style id="ab-dark-theme"> block')
    if new != html:
        open(path, 'w', encoding='utf-8').write(new)
    print(len(dark.splitlines()), 'rules,', 'updated' if new != html else 'unchanged')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / 'benchmarks/attention_harness/ui/index.html'))
