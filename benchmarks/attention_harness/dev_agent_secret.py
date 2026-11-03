"""Explicit in-memory secrets and error redaction."""
import hmac
import json
import re


class Secret:
    __slots__ = ('_value',)
    def __init__(self, value):
        if not isinstance(value, str): raise TypeError('secret must be a string')
        self._value = value
    def __repr__(self): return '<secret>'
    def __str__(self): return '<secret>'
    def __format__(self, spec): return '<secret>'
    def reveal(self): return self._value
    def __bool__(self): return bool(self._value)
    def __eq__(self, other):
        if isinstance(other, Secret): other = other._value
        if not isinstance(other, str): return NotImplemented
        return hmac.compare_digest(self._value.encode(), other.encode())
    def __hash__(self): raise TypeError('secret is not hashable')
    def __reduce__(self): raise TypeError('secret cannot be serialized')
    def __reduce_ex__(self, protocol): raise TypeError('secret cannot be serialized')
    def __copy__(self): raise TypeError('secret cannot be copied')
    def __deepcopy__(self, memo): raise TypeError('secret cannot be copied')


def scrub(text, secrets):
    text = text if isinstance(text, str) else str(text)
    original = text
    values = [s.reveal() if isinstance(s, Secret) else s for s in secrets]
    forms = {form for v in values if v for form in (v,json.dumps(v)[1:-1],json.dumps(v,ensure_ascii=False)[1:-1])}
    for value in sorted(forms, key=len, reverse=True):
        text = text.replace(value, '[REDACTED:key]')
    # The third form is a provider's masked echo of a rejected key ("****1226", "AIza****Q9xK", "••••1226").
    for pattern in (r'sk-[A-Za-z0-9_\-*.]{4,}', r'(?i)bearer\s+\S+', r'[A-Za-z0-9_-]{0,8}(?:\*{3,}|[•●]{3,})[A-Za-z0-9]{2,8}'):
        text = re.sub(pattern, '[REDACTED:key]', text)
    text = re.sub(r'(?i)(key(?:\s+provided)?[\s"\x27:=]+)[A-Za-z0-9_./+*=-]{16,}',
                  lambda match: match[1]+'[REDACTED:key]', text)
    return text, text != original
