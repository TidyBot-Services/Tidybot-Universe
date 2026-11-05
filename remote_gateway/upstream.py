"""The signed link to the harness. Only this file knows how to reach it; the harness refuses anything unsigned."""
import hashlib
import hmac
import http.client
import json
import time
from urllib.parse import urlsplit


class UpstreamError(Exception):
    pass


def signature(secret, owner, method, target, body, now):
    ts = str(int(now))
    message = '\n'.join((owner, ts, method, target, hashlib.sha256(body).hexdigest()))
    return 'v1.%s.%s' % (ts, hmac.new(secret, message.encode(), hashlib.sha256).hexdigest())


def control_token(secret):
    return hmac.new(secret, b'attentionbench-control-token', hashlib.sha256).hexdigest()


class Upstream:
    def __init__(self, base, secret, *, clock=time.time, timeout=120):
        url = urlsplit(base)
        if url.scheme != 'http' or not url.hostname or url.path not in ('', '/') or url.query:raise ValueError('upstream must be http://host:port')
        self.host, self.port = url.hostname, url.port or 80
        self.secret, self.clock, self.timeout = secret, clock, timeout

    def open(self, owner, method, target, body=b'', headers=None):
        """One signed request. The caller reads the response, then closes the connection."""
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        send = {'X-Attention-Owner': owner, 'X-Attention-Gateway-Auth': signature(self.secret, owner, method, target, body, self.clock()),
                'X-Attention-Token': control_token(self.secret), **(headers or {})}
        if method in ('POST', 'PUT'):send['Content-Type'] = 'application/json'
        try:
            connection.request(method, target, body=body if method in ('POST', 'PUT') else None, headers=send)
            return connection, connection.getresponse()
        except OSError:
            connection.close()
            raise UpstreamError('the run host is not reachable') from None

    def json(self, owner, method, target, value=None):
        connection, response = self.open(owner, method, target, json.dumps(value).encode() if value is not None else b'')
        try:
            raw = response.read()
        except OSError:
            raise UpstreamError('the run host is not reachable') from None
        finally:connection.close()
        try:return response.status, json.loads(raw)
        except ValueError:raise UpstreamError('the run host gave an unreadable answer') from None
