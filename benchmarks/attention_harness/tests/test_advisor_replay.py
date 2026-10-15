import hashlib
import pytest
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.v2_advisor import SimGTAdvisorProxy


@pytest.mark.parametrize('shared',[False,True])
def test_replay_is_read_only_and_never_calls_provider(tmp_path,shared):
    store=AttentionStore(tmp_path/'store.sqlite')
    cache=tmp_path/'cache.sqlite' if shared else None
    packet={'trace_id':'first', 'failure':{'message':'miss'}}
    live=SimGTAdvisorProxy(store, cache_path=cache,transport=lambda request:'exact reply',sleeper=lambda _:None)
    first=live.answer(request_type='hint',trace_packet=packet)
    def forbidden(request):pytest.fail('provider called in replay mode')
    before=(cache or store.path).read_bytes()
    replay=SimGTAdvisorProxy(store,cache_path=cache,replay_only=True,transport=forbidden,sleeper=lambda _:None)
    reply=replay.answer(request_type='hint',trace_packet={**packet,'trace_id':'second'})
    assert reply.content==first.content and reply.cache_key==first.cache_key
    assert reply.cached and reply.provider_attempts==0 and reply.total_tokens==0
    with pytest.raises(RuntimeError,match='^advisor_cache_miss$'):
        replay.answer(request_type='hint',trace_packet={'failure':'different'})
    assert (cache or store.path).read_bytes()==before


def test_replay_missing_cache_does_not_create_files(tmp_path):
    store=AttentionStore(tmp_path/'store.sqlite')
    cache=tmp_path/'missing'/'cache.sqlite'
    proxy=SimGTAdvisorProxy(store,cache_path=cache,replay_only=True,
        transport=lambda _:pytest.fail('provider called'),sleeper=lambda _:None)
    with pytest.raises(RuntimeError,match='advisor_cache_miss'):
        proxy.answer(request_type='hint',trace_packet={'failure':'miss'})
    assert not cache.parent.exists()


def test_scheduler_replay_uses_shared_cache_without_constructing_provider(tmp_path,monkeypatch):
    from test_async_requests import run
    import benchmarks.attention_harness.sim_gt_attention_run as scheduler
    def policy(sdk,context):pass
    first=run(tmp_path,policy,request_mode='blocking')
    assert first['requests']
    def forbidden(**kwargs):pytest.fail('default provider initialized during replay')
    monkeypatch.setattr(scheduler,'default_glm_transport',forbidden)
    second=run(tmp_path,policy,request_mode='blocking',
               advisor_transport=None,advisor_replay_only=True)
    assert second['requests'] and all(row['cached'] for row in second['requests'])
