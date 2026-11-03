"""A test image with a known answer, so a provider that accepts an image and ignores it fails the Images check."""
import re
import zlib

EXPECTED = 'HELLO4821'
PROMPT = 'Read the text printed in the image. Answer with only that text.'

_FONT = {  # 5x7 bitmap glyphs for the characters in "HELLO 4821"
    'H': ['X...X', 'X...X', 'X...X', 'XXXXX', 'X...X', 'X...X', 'X...X'],
    'E': ['XXXXX', 'X....', 'X....', 'XXXX.', 'X....', 'X....', 'XXXXX'],
    'L': ['X....', 'X....', 'X....', 'X....', 'X....', 'X....', 'XXXXX'],
    'O': ['.XXX.', 'X...X', 'X...X', 'X...X', 'X...X', 'X...X', '.XXX.'],
    '4': ['...X.', '..XX.', '.X.X.', 'X..X.', 'XXXXX', '...X.', '...X.'],
    '8': ['.XXX.', 'X...X', 'X...X', '.XXX.', 'X...X', 'X...X', '.XXX.'],
    '2': ['.XXX.', 'X...X', '....X', '...X.', '..X..', '.X...', 'XXXXX'],
    '1': ['..X..', '.XX..', '..X..', '..X..', '..X..', '..X..', '.XXX.'],
    ' ': ['.....'] * 7,
}


def _png(width, height, pixel):
    rows = b''.join(b'\x00' + b''.join(bytes(pixel(x, y)) for x in range(width)) for y in range(height))
    def chunk(kind, data):
        body = kind + data
        return len(data).to_bytes(4, 'big') + body + zlib.crc32(body).to_bytes(4, 'big')
    header = width.to_bytes(4, 'big') + height.to_bytes(4, 'big') + bytes([8, 2, 0, 0, 0])
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')


def text_png(text='HELLO 4821', scale=12, margin=32):
    """Big black letters on light yellow, large enough for any vision encoder."""
    cols = len(text) * 6 - 1
    def pixel(x, y):
        cx, cy = (x - margin) // scale, (y - margin) // scale
        if 0 <= cy < 7 and 0 <= cx < cols and cx % 6 < 5 and _FONT[text[cx // 6]][cy][cx % 6] == 'X':
            return (0, 0, 0)
        return (255, 244, 170)
    return _png(cols * scale + 2 * margin, 7 * scale + 2 * margin, pixel)


def reads_text(answer):
    return EXPECTED in re.sub(r'[^A-Za-z0-9]', '', answer or '').upper()
