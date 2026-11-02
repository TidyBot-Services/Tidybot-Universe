"""Real HTTP handlers with in-memory connections, for socket-restricted CI."""
import io
import json
import socket
import threading
import pytest


class MemoryServer:
    def __init__(self, address, handler):
        self.server_address=address if address[1] else ('127.0.0.1',12345)
        self.server_port=self.server_address[1];self.RequestHandlerClass=handler
        self.stop=threading.Event()
    def serve_forever(self):self.stop.wait()
    def shutdown(self):self.stop.set()
    def server_close(self):pass


def exchange(server, method, path, body=None, headers=None, raw=None):
    payload=raw if raw is not None else (json.dumps(body).encode() if body is not None else b'')
    headers={'Host':'127.0.0.1','Content-Type':'application/json','Content-Length':str(len(payload)),**(headers or {})}
    request=('%s %s HTTP/1.0\r\n'%(method,path)+''.join('%s: %s\r\n'%v for v in headers.items())+'\r\n').encode()+payload
    class Connection:
        def __init__(self):self.response=bytearray()
        def makefile(self,*args):return io.BytesIO(request)
        def sendall(self,data):self.response.extend(data)
    connection=Connection();server.RequestHandlerClass(connection,('127.0.0.1',1),server)
    raw=bytes(connection.response);head,_,body=raw.partition(b'\r\n\r\n')
    return int(head.split()[1]),body,head


@pytest.fixture
def loopback():
    try:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0))
    except PermissionError:pytest.skip('sandbox denies AF_INET sockets, including 127.0.0.1:0')
