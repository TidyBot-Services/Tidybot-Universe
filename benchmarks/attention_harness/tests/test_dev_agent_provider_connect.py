"""Connect-time DNS admission; DNS and public sockets are always faked."""
import io
import ipaddress
import socket
import ssl
import urllib.error
import urllib.request

import pytest

from benchmarks.attention_harness import dev_agent_provider as providers
from benchmarks.attention_harness.tests.fakes.byok_http import loopback
from benchmarks.attention_harness.tests.fakes.fake_provider import FakeProvider


def addresses(ips, port):
    return [(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM,
             socket.IPPROTO_TCP, '', (ip, port, 0, 0) if ':' in ip else (ip, port)) for ip in ips]


@pytest.fixture(autouse=True)
def isolated_network(monkeypatch):
    monkeypatch.setattr(providers, '_PRIVATE_ORIGINS', set())
    def no_dns(*args, **kwargs): raise AssertionError('unexpected DNS lookup')
    monkeypatch.setattr(socket, 'getaddrinfo', no_dns)
    connect = socket.socket.connect
    def local_connect(sock, address):
        assert ipaddress.ip_address(address[0]).is_loopback
        return connect(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', local_connect)


class FakeSocket:
    response = b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok'
    def __init__(self, *args):
        self.sent = b''; self.closed = False
    def settimeout(self, value): self.timeout = value
    def setsockopt(self, *args): pass
    def connect(self, address): self.address = address
    def sendall(self, data): self.sent += data
    def makefile(self, *args): return io.BytesIO(self.response)
    def close(self): self.closed = True


@pytest.fixture
def fake_sockets(monkeypatch):
    sockets = []; tls = []
    def create(*args):
        sock = FakeSocket(*args); sockets.append(sock)
        return sock
    def wrap(context, sock, *, server_hostname):
        tls.append((server_hostname, context.check_hostname, context.verify_mode))
        return sock
    monkeypatch.setattr(socket, 'socket', create)
    monkeypatch.setattr(ssl.SSLContext, 'wrap_socket', wrap)
    return sockets, tls


def test_dns_rebinding_is_refused_at_connect(monkeypatch, fake_sockets):
    calls = []
    def resolve(host, port, **kwargs):
        assert host == 'rebind.test'
        calls.append(host)
        assert len(calls) <= 2
        return addresses(['93.184.216.34' if len(calls) == 1 else '127.0.0.1'], port)
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    spec = providers.resolve_provider('openai-compatible', 'https://rebind.test/v1', allow_custom=True)
    with pytest.raises(providers.ProviderError) as exc:
        providers.open_request(urllib.request.Request(spec.chat_url))
    assert exc.value.code == 'base_url_private_host'
    assert len(calls) == 2 and not fake_sockets[0]


@pytest.mark.parametrize('private', ['127.0.0.1', '10.0.0.1', '172.17.0.2', '169.254.169.254',
                                   '100.64.0.1', '::1', 'fc00::1', 'fe80::1', '0.0.0.0'])
@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_any_non_global_answer_refuses_every_address(monkeypatch, fake_sockets, private, scheme):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, **kw:
                        addresses(['93.184.216.34', private, '2606:4700:4700::1111'], port))
    with pytest.raises(providers.ProviderError) as exc:
        providers.open_request(urllib.request.Request(scheme + '://mixed.test/v1'))
    assert exc.value.code == 'base_url_private_host'
    assert not fake_sockets[0]  # Check the whole answer before opening even the first public socket.


def test_explicit_private_host_connects_to_loopback(loopback, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, **kw: addresses(['127.0.0.1'], port))
    monkeypatch.setenv('HTTP_PROXY', 'http://proxy.invalid:9')
    with FakeProvider('local-key') as fake:
        spec = providers.resolve_provider('openai-compatible', fake.base_url,
            allow_custom=True, allow_private_hosts=('127.0.0.1',))
        request = urllib.request.Request(spec.models_url, headers={'Authorization': 'Bearer local-key'})
        with providers.open_request(request, timeout=2) as response:
            assert response.status == 200 and b'fake-model' in response.read()
        assert fake.authorizations == ['Bearer local-key']


@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_connect_uses_vetted_ip_and_original_host_ignoring_proxies(monkeypatch, fake_sockets, scheme):
    calls = []
    def resolve(host, port, **kwargs):
        calls.append((host, port))
        assert len(calls) == 1 and host == 'provider.test'
        return addresses(['93.184.216.34'], port)
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    for name in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy'):
        monkeypatch.setenv(name, 'http://proxy.invalid:9')
    for name in ('NO_PROXY', 'no_proxy'): monkeypatch.delenv(name, raising=False)
    request = urllib.request.Request(scheme + '://provider.test:8443/v1', headers={'Authorization': 'Bearer test'})
    with providers.open_request(request, timeout=3) as response: assert response.read() == b'ok'
    sockets, tls = fake_sockets
    assert calls == [('provider.test', 8443)] and len(sockets) == 1
    assert sockets[0].address == ('93.184.216.34', 8443) and sockets[0].timeout == 3
    assert b'Host: provider.test:8443\r\n' in sockets[0].sent
    assert b'Authorization: Bearer test\r\n' in sockets[0].sent
    assert tls == ([('provider.test', True, ssl.CERT_REQUIRED)] if scheme == 'https' else [])


def test_failed_address_falls_back_without_resolving_again(monkeypatch, fake_sockets):
    calls = []
    def resolve(host, port, **kwargs):
        calls.append(host)
        assert len(calls) == 1
        return addresses(['93.184.216.34', '2606:4700:4700::1111'], port)
    def connect(sock, address):
        sock.address = address
        if address[0] == '93.184.216.34': raise OSError('unreachable')
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    monkeypatch.setattr(FakeSocket, 'connect', connect)
    with providers.open_request(urllib.request.Request('https://fallback.test/v1')) as response:
        assert response.read() == b'ok'
    sockets, tls = fake_sockets
    assert len(sockets) == 2 and sockets[0].closed
    assert sockets[1].address == ('2606:4700:4700::1111', 443, 0, 0)
    assert tls == [('fallback.test', True, ssl.CERT_REQUIRED)]


def test_each_new_connection_is_checked(monkeypatch, fake_sockets):
    calls = []
    def resolve(host, port, **kwargs):
        calls.append(host)
        return addresses(['93.184.216.34' if len(calls) == 1 else '127.0.0.1'], port)
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    request = urllib.request.Request('https://again.test/v1')
    with providers.open_request(request) as response: assert response.read() == b'ok'
    with pytest.raises(providers.ProviderError) as exc: providers.open_request(request)
    assert exc.value.code == 'base_url_private_host' and len(fake_sockets[0]) == 1


def test_private_allowance_is_limited_to_explicit_host_and_port(monkeypatch, fake_sockets):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, **kw: addresses(['127.0.0.1'], port))
    spec = providers.resolve_provider('openai-compatible', 'https://local.test:8443/v1',
        allow_custom=True, allow_private_hosts=('local.test',))
    with providers.open_request(urllib.request.Request(spec.chat_url)) as response: assert response.read() == b'ok'
    for url in ('https://local.test:9443/v1', 'https://other.test:8443/v1'):
        with pytest.raises(providers.ProviderError) as exc: providers.open_request(urllib.request.Request(url))
        assert exc.value.code == 'base_url_private_host'
    assert len(fake_sockets[0]) == 1


def test_redirect_is_not_followed(monkeypatch, fake_sockets):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, **kw: addresses(['93.184.216.34'], port))
    monkeypatch.setattr(FakeSocket, 'response', b'HTTP/1.1 302 Found\r\nLocation: https://other.test/\r\nContent-Length: 0\r\n\r\n')
    with pytest.raises(urllib.error.HTTPError) as exc:
        providers.open_request(urllib.request.Request('https://provider.test/v1'))
    assert exc.value.code == 302 and len(fake_sockets[0]) == 1
