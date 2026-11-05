"""The signature the public gateway puts on every request to a harness behind it; shared by the UI servers."""
import hashlib
import hmac
import os
import re
from urllib.parse import urlsplit

from .dev_agent_provider import ProviderError
from .dev_agent_secret import Secret


class OwnerError(ProviderError):
    def __init__(self,code,message,status):
        super().__init__(code,message);self.status=status


def owner_of(request):
    if hasattr(request,'_request_owner'):return request._request_owner
    owner=request.headers.get('X-Attention-Owner','')
    auth=request.headers.get('X-Attention-Gateway-Auth','')
    if not request.server.public_gateway_mode:
        if 'X-Attention-Owner' in request.headers or 'X-Attention-Gateway-Auth' in request.headers:
            raise OwnerError('owner_header_not_accepted','Owner headers are not accepted',400)
        return 'local'
    if request.client_address[0] not in ('127.0.0.1','::1'):
        raise OwnerError('gateway_peer_not_loopback','Gateway peer must be loopback',403)
    invalid=OwnerError('gateway_auth_invalid','Invalid gateway authentication',401)
    if len(request.headers.get_all('X-Attention-Owner',[]))!=1 or len(request.headers.get_all('X-Attention-Gateway-Auth',[]))!=1:raise invalid
    match=re.fullmatch(r'v1\.([0-9]{1,12})\.([a-f0-9]{64})',auth)
    if not re.fullmatch(r'[a-z0-9_-]{1,64}',owner) or not match:raise invalid
    ts,digest=match.groups()
    if abs(request.server.gateway_clock()-int(ts))>30:
        raise OwnerError('gateway_auth_expired','Gateway authentication expired',401)
    body=request.read_body(8192 if urlsplit(request.path).path.startswith('/api/provider/') else 32768)
    message='\n'.join((owner,ts,request.command,request.path,hashlib.sha256(body).hexdigest()))
    expected=hmac.new(request.server.gateway_secret.reveal().encode(),message.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,digest):raise invalid
    return owner


def read_gateway_secret(fd):
    try:
        with os.fdopen(fd,'rb') as stream:raw=stream.read(65537)
        if not raw or len(raw)>65536:raise ValueError()
        return Secret(raw.decode())
    except Exception:raise ValueError('Invalid gateway secret fd') from None


def control_token_for(secret):
    """The control token of a gateway-mode harness: derived, so no page ever carries one and the gateway needs no second secret."""
    return hmac.new(secret.reveal().encode(),b'attentionbench-control-token',hashlib.sha256).hexdigest()
