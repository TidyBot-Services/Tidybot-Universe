"""Downloads survive interruptions and report what they could not do."""
import io
import urllib.error

import pytest

from scripts.attentionbench import fetch


class Response(io.BytesIO):
    def __init__(self, data, status):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def server(data, *, faults=(), ranges=True, seen=None):
    faults = list(faults)
    def opener(request, timeout):
        if seen is not None:
            seen.append(request.get_header('Range'))
        if faults:
            raise faults.pop(0)
        start = int((request.get_header('Range') or 'bytes=0-')[6:-1]) if ranges else 0
        return Response(data[start:], 206 if ranges and start else 200)
    return opener


def quiet():
    return dict(sleep=lambda seconds: None, emit=lambda line: None)


def test_downloads_through_a_part_file(tmp_path):
    target = tmp_path / 'sub/a.zip'
    fetch.download('http://x/a', target, size=4, opener=server(b'data'), **quiet())
    assert target.read_bytes() == b'data' and not target.with_name('a.zip.part').exists()


def test_existing_complete_file_is_not_fetched_again(tmp_path):
    target = tmp_path / 'a.zip'
    target.write_bytes(b'data')
    fetch.download('http://x/a', target, size=4, opener=lambda *a, **k: pytest.fail('fetched'), **quiet())


def test_resumes_from_the_part_file(tmp_path):
    target = tmp_path / 'a.zip'
    target.with_name('a.zip.part').write_bytes(b'da')
    seen = []
    fetch.download('http://x/a', target, size=4, opener=server(b'data', seen=seen), **quiet())
    assert target.read_bytes() == b'data' and seen == ['bytes=2-']


def test_a_server_that_ignores_the_range_starts_over(tmp_path):
    target = tmp_path / 'a.zip'
    target.with_name('a.zip.part').write_bytes(b'xx')
    fetch.download('http://x/a', target, size=4, opener=server(b'data', ranges=False), **quiet())
    assert target.read_bytes() == b'data'


def test_network_errors_are_retried(tmp_path):
    target = tmp_path / 'a.zip'
    faults = [OSError('reset'), urllib.error.HTTPError('u', 503, 'busy', {}, io.BytesIO())]
    fetch.download('http://x/a', target, size=4, opener=server(b'data', faults=faults), **quiet())
    assert target.read_bytes() == b'data'


def test_a_missing_file_fails_at_once(tmp_path):
    calls = []
    def opener(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError('u', 404, 'gone', {}, io.BytesIO())
    with pytest.raises(urllib.error.HTTPError):
        fetch.download('http://x/a', tmp_path / 'a.zip', opener=opener, **quiet())
    assert len(calls) == 1


def test_gives_up_with_the_reason(tmp_path):
    with pytest.raises(OSError, match='after 3 attempts.*reset'):
        fetch.download('http://x/a', tmp_path / 'a.zip', attempts=3, opener=server(b'', faults=[OSError('reset')] * 3), **quiet())


def test_short_body_is_continued_not_accepted(tmp_path):
    target = tmp_path / 'a.zip'
    bodies = [Response(b'da', 200), Response(b'ta', 206)]
    fetch.download('http://x/a', target, size=4, opener=lambda request, timeout: bodies.pop(0), **quiet())
    assert target.read_bytes() == b'data'


def test_oversized_part_file_is_discarded(tmp_path):
    target = tmp_path / 'a.zip'
    target.with_name('a.zip.part').write_bytes(b'too long for this file')
    fetch.download('http://x/a', target, size=4, opener=server(b'data'), **quiet())
    assert target.read_bytes() == b'data'
