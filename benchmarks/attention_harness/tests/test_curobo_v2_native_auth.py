import copy
import socket
import pytest
from curobo_v2.identity import IdentityError,sign
from curobo_v2.native_auth import verify_native_payload,inherited_listener,require_native_owner
from curobo_v2.native_auth import authenticated_handler
from types import SimpleNamespace
import threading


def payload():
    expected=dict(pid=41,nonce='owned',lock_sha256='sha',python='/locked/python',prefix='/locked',
        project_root='/project',robot_config='/robot',task_source='/tasks',task='counter_to_sink',seed=101,arm='left')
    identity=dict(expected,ready=True,planner_identity=dict(lock_sha256='sha',kernel_backend='cuda_core',
        robot_config='/robot',runtime={'generation':2}))
    return expected,dict(identity=identity,signature=sign('secret',identity,'fresh-challenge'))


def test_authenticated_native_and_embedded_v2_identity():
    expected,response=payload()
    assert verify_native_payload(response,expected=expected,token='secret',challenge='fresh-challenge')['ready']


@pytest.mark.parametrize('field,value',[('pid',42),('python','/old/python'),('task','counter_to_cab'),
    ('nonce','foreign'),('lock_sha256','other'),('arm','right'),('seed',102)])
def test_signed_wrong_native_instance_or_selection_is_rejected(field,value):
    expected,response=payload();response['identity'][field]=value
    response['signature']=sign('secret',response['identity'],'fresh-challenge')
    with pytest.raises(IdentityError):verify_native_payload(response,expected=expected,token='secret',challenge='fresh-challenge')


@pytest.mark.parametrize('mode',['replay','modified','v1','other_backend','unsigned'])
def test_replay_tampering_and_legacy_planner_are_rejected(mode):
    expected,response=payload();challenge='fresh-challenge'
    if mode=='replay':challenge='next-challenge'
    elif mode=='modified':response['identity']['ready']=False
    elif mode=='unsigned':response.pop('signature')
    else:
        if mode=='v1':response['identity']['planner_identity']['runtime']['generation']=1
        else:response['identity']['planner_identity']['kernel_backend']='auto'
        response['signature']=sign('secret',response['identity'],challenge)
    with pytest.raises(IdentityError):verify_native_payload(response,expected=expected,token='secret',challenge=challenge)


def test_executor_only_accepts_parent_reserved_listening_socket():
    with socket.socket()as reserved:
        reserved.bind(('127.0.0.1',0));reserved.listen()
        adopted=inherited_listener(reserved.dup().detach())
        assert adopted.getsockname()==reserved.getsockname();adopted.close()
    with socket.socket()as unbound:
        with pytest.raises(IdentityError):inherited_listener(unbound.dup().detach())


def test_standalone_native_start_has_no_authorized_owner(monkeypatch):
    for key in ['CUROBO_V2_NATIVE_OWNER_PID','CUROBO_V2_NATIVE_TOKEN','CUROBO_V2_NATIVE_NONCE']:
        monkeypatch.delenv(key,raising=False)
    with pytest.raises(IdentityError):require_native_owner()


@pytest.mark.parametrize('method,path,full',[('GET','/state',False),('GET','/camera',False),
    ('GET','/actions/0%3A1',False),('GET','/health',True),('POST','/plan',True),('POST','/trajectory/actions',True)])
def test_continuous_identity_protects_telemetry_while_control_keeps_full_checks(monkeypatch,method,path,full):
    import curobo_v2.native_auth as auth
    calls=[]
    monkeypatch.setattr(auth,'verify_files',lambda *_:calls.append('files'))
    monkeypatch.setattr(auth,'verify_runtime',lambda *_,**__:calls.append('runtime'))
    class Base:
        def dispatch(self):self.served=True
        def respond(self,*args):self.response=args
    integrity=SimpleNamespace(check=lambda **_:calls.append('continuous'),fail=lambda reason:None)
    cls=authenticated_handler(SimpleNamespace(Handler=Base),lock={},lock_sha='sha',token='token',nonce='nonce',selection={})
    handler=cls();handler.server=SimpleNamespace(runtime=SimpleNamespace(integrity=integrity,planning_interrupt=threading.Event(),ready=True))
    handler.headers={'Authorization':'Bearer token','X-Curobo-Native':'nonce'};handler.command=method;handler.path=path
    handler.dispatch();assert handler.served
    assert calls==(['continuous','files','runtime']if full else['continuous'])


def test_failed_identity_cannot_block_cleanup_stop_or_cancel():
    class Base:
        def dispatch(self):self.served=True
        def respond(self,*args):self.response=args
    def unavailable():raise IdentityError('modified runtime')
    cls=authenticated_handler(SimpleNamespace(Handler=Base),lock={},lock_sha='sha',token='token',nonce='nonce',selection={})
    runtime=SimpleNamespace(integrity=SimpleNamespace(check=unavailable,fail=lambda reason:None),planning_interrupt=threading.Event())
    for path in ['/stop','/cancel']:
        handler=cls();handler.server=SimpleNamespace(runtime=runtime);handler.command='POST';handler.path=path
        handler.headers={'Authorization':'Bearer token','X-Curobo-Native':'nonce'};handler.dispatch();assert handler.served
