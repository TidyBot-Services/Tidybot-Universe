"""Public hint-derived titles, shared by run notes and the offline importer."""
import re


def _cut(text, limit):
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    if ' ' in head:
        head = head.rsplit(' ', 1)[0]
    return head.rstrip(' ,;:') + '…'


def note_title(content, failure_signature=None):
    from attention_memory_service.note_title import validate_title
    text = re.sub(r'[`*]', '', content)
    text = re.split(r'[\r\n]|(?<=[.!?。！？])\s+', text.strip(), maxsplit=1)[0]
    text = ' '.join(text.split())
    if len(text) > 80:
        clause = re.split(r'; |: | — ', text, maxsplit=1)[0]
        text = _cut(clause if len(clause) >= 20 else text, 80)
    try:
        return validate_title(text)
    except ValueError:
        text = re.sub(r'\S*(?:[a-z][a-z0-9+.-]*://|www\.|[\w.-]+\.(?:com|org|net|io|ai|dev|edu|gov)\b)\S*', '', text, flags=re.I)
        text = ' '.join(text.replace('/', ' ').replace('\\', ' ').replace('<', '').replace('>', '').split())
        try:
            return validate_title(text)
        except ValueError:
            operation = (failure_signature or {}).get('operation')
            try:
                return validate_title(f'Hint for {operation}' if operation else 'Hint')
            except ValueError:
                return 'Hint'


def unique_title(base, taken, version):
    taken = {title.casefold() for title in taken}
    if base.casefold() not in taken:
        return base
    n = 1
    while True:
        suffix = f' (v{version})' if n == 1 else f' (v{version}, {n})'
        candidate = _cut(base, 80 - len(suffix)) + suffix
        if candidate.casefold() not in taken:
            return candidate
        n += 1
