import io,json,urllib.error
import pytest
from curobo_v2.openarm_sdk import checked_transport,V2ReadOnlyPlanRejected

class Owner:
    def __init__(self,code='v2_plan_rejected_no_motion',status=422,stop_error=False):self.code,self.status,self.stop_error=code,status,stop_error;self.calls=[]
    def request(self,method,path,body,timeout):
        self.calls.append(path)
        if path=='/stop':
            if self.stop_error:raise TimeoutError('stop unavailable')
            return {'epoch':1,'stopped':True}
        raise urllib.error.HTTPError('http://127.0.0.1',self.status,'rejected',{},io.BytesIO(json.dumps({'error':{'code':self.code,'message':'collision veto'}}).encode()))

def test_only_typed_authenticated_zero_motion_rejection_uses_readonly_class():
    owner=Owner()
    with pytest.raises(V2ReadOnlyPlanRejected,match='collision veto')as error:checked_transport(owner,'POST','/plan',{},8.)
    assert owner.calls==['/plan','/stop']and error.value.stop_receipt['epoch']==1

@pytest.mark.parametrize('path,code,status',[('/trajectory/actions','v2_plan_rejected_no_motion',422),('/plan','v2_rejected',422),('/plan','v2_plan_rejected_no_motion',503)])
def test_unproven_rejection_does_not_claim_zero_physical_motion(path,code,status):
    owner=Owner(code,status)
    with pytest.raises(RuntimeError)as error:checked_transport(owner,'POST',path,{},8.)
    assert not isinstance(error.value,V2ReadOnlyPlanRejected)and owner.calls==[path]

def test_readonly_rejection_requires_completed_stop_barrier():
    with pytest.raises(TimeoutError):checked_transport(Owner(stop_error=True),'POST','/plan',{},8.)
