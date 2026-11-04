"""Real daily main/pipe, with only experiments.run replaced by offline chat jobs."""
import json
import os
from pathlib import Path
import resource
import sys
import time

from benchmarks.attention_harness import daily_dev_v2 as daily
from benchmarks.attention_harness import dev_agent_key_vault as vault
from benchmarks.attention_harness.canary_scan import scan
from benchmarks.attention_harness.dev_agent_byok_client import ByokChatClient
from benchmarks.attention_harness.dev_agent_loop import providers_for_run


calls = []
memory_provider = '--memory-provider' in sys.argv
if memory_provider:sys.argv.remove('--memory-provider')
original_harden = vault.harden_process


def harden():
    calls.append(True)
    original_harden()


def offline_run(store, eid, **kwargs):
    experiment = daily.experiments.snapshot(store, eid)
    manifest = json.loads(Path(experiment['manifest']['uri']).read_bytes())
    providers = providers_for_run(manifest, allow_private_hosts=kwargs.get('allow_private_hosts', ()))
    if memory_provider:
        # Additional pipe smoke test for sandboxes that prohibit loopback sockets.
        # The separate loopback E2E test never enables this transport.
        import io
        from benchmarks.attention_harness import dev_agent_provider
        def request(req, **options):
            body=json.loads(req.data)
            role=body['model'].removeprefix('fake-')
            assert req.get_header('Authorization')=='Bearer '+providers[role].secret.reveal()
            response=io.BytesIO(json.dumps(dict(model=body['model'],choices=[dict(
                message=dict(content='ok'),finish_reason='stop')],usage=dict(prompt_tokens=3,completion_tokens=2,total_tokens=5))).encode())
            response.headers={}
            return response
        dev_agent_provider.open_request=request
    obtained = []
    for role, provider in providers.items():
        assert provider.secret == vault.run_key(manifest['agent_provider']['roles'][role]['credential'])
        response = ByokChatClient(provider).chat(model=provider.model,
            messages=[dict(role='user',content='daily offline '+role)],max_tokens=32)
        assert response.content == 'ok'
        obtained.append(role)
    root = store.path.parent
    blobs = [json.dumps(sys.argv),json.dumps(dict(os.environ))]
    unreadable=[]
    for name in ('cmdline','environ'):
        try:blobs.append(Path('/proc/self/'+name).read_bytes())
        except PermissionError:unreadable.append(name)
    assert not scan(list(vault._RUN_KEYS.values()), roots=[root],sqlite_paths=[store.path],
                    extra_blobs=blobs,include_processes=False)
    assert calls and resource.getrlimit(resource.RLIMIT_CORE) == (0,0)
    (root/'offline-result.json').write_text(json.dumps(dict(roles=obtained,hardened=True,
        pid=os.getpid(),argv_clean=True,environ_clean=True,proc_unreadable=unreadable)))
    # Keep the real worker alive for the parent's /proc canary scan.
    deadline=time.monotonic()+20
    while not (root/'offline-release').exists():
        if time.monotonic()>deadline:raise TimeoutError('offline worker release timed out')
        time.sleep(.02)


if __name__ == '__main__':
    vault.harden_process = harden
    daily.experiments.run = offline_run
    daily.main()
