"""Live preview frames carry the 1-based Dev version the live monitor compares with the cell's active version."""
import copy,hashlib,json,time
import pytest
from openarm365_dev_v2 import extra_views as xv
from openarm365_dev_v2.runner import dev_version_label
from openarm365_dev_v2.tests.test_extra_views import Env
from benchmarks.attention_harness.dev_live_monitor import MonitorError,live

E='devexp-'+'a'*24;K='b'*64;EP='c'*32
class Store:
    def __init__(self,c):self.c=c
    def cell(self,key):return copy.deepcopy(self.c)

@pytest.mark.parametrize('attempt_id,label',[('attempt:'+K+':0','1'),('attempt:'+K+':4','5'),(None,'1'),('attempt:formal-test:0','1'),('reset:x','x')])
def test_attempt_index_becomes_the_one_based_dev_version_string(attempt_id,label):
    assert dev_version_label(attempt_id)==label               # recording_context keeps dev_version a string

@pytest.mark.parametrize('value,expected',[('2',2),(2,2),('x','x'),(None,None)])
def test_live_metadata_version_is_an_int_when_the_label_is_numeric(value,expected):
    assert xv.live_version({'dev_version':value})==expected

def episode(tmp_path,version):
    root=tmp_path/'cell';ep=root/'v2/native';(ep/'live').mkdir(parents=True)
    frame=ep/'live/frame-000003.jpg';frame.write_bytes(b'main-view-frame')
    (ep/'live/latest.json').write_text(json.dumps(dict(episode_id=EP,version=version,sequence=3,wall_time=time.time(),
        file=frame.name,sha256=hashlib.sha256(frame.read_bytes()).hexdigest())))
    c=dict(experiment_id=E,key=K,state='running',active_episode=str(ep),artifact_dir=str(root),active_version=2,jobs=[])
    return Store(c),ep

def test_dev_v2_frames_pass_the_monitor_for_every_view(tmp_path):
    s,ep=episode(tmp_path,xv.live_version({'dev_version':dev_version_label('attempt:'+K+':1')}))
    assert live(s,E,K)[1]['sequence']==3
    views=xv.ExtraViews(Env(),ep,{'dev_version':dev_version_label('attempt:'+K+':1')},EP,[0.3,-1.8,1.0])
    views.capture(5)
    for key in xv.VIEWS:
        meta=json.loads((ep/'live/views'/key/'latest.json').read_text())
        assert meta['version']==2 and live(s,E,K,key)[1]['sequence']==5

@pytest.mark.parametrize('stale',['2','1',1,3])
def test_monitor_still_rejects_a_string_or_different_version(tmp_path,stale):
    s,_=episode(tmp_path,stale)
    with pytest.raises(MonitorError) as caught:live(s,E,K)
    assert caught.value.code=='version_mismatch' and caught.value.status==409
