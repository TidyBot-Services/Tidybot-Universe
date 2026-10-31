"""R5 extension over the frozen one-call SDK."""
from openarm365_dev.owner import build_sdk as legacy_sdk
from curobo_v2.fixture_interaction import config_enabled


def build_sdk(owner,record_event,*,config):
    sdk=legacy_sdk(owner,record_event)
    if not config_enabled(config):return sdk
    def find_fixtures():
        event=dict(source='sensors',operation='find_fixtures',status='started',arguments={})
        try:
            value=owner.rpc('sensors','find_fixtures')
            event.update(status='completed',result=value);record_event(event);return value
        except Exception as exc:
            event.update(status='failed',error={'type':type(exc).__name__,'message':str(exc)[:1200]})
            record_event(event);raise
    sdk.sensors.find_fixtures=find_fixtures
    def move_to(side):
        def call(width_m):
            from curobo_v2.fixture_gripper import wire_width
            width_m=wire_width(width_m)
            event=dict(source=side+'_gripper',operation='move_to',arguments=dict(args=[width_m],kwargs={}))
            try:
                value=owner.rpc(side+'_gripper','move_to',(width_m,),{})
                event.update(status='rejected' if isinstance(value,dict) and value.get('rejected') else 'completed',result=value)
                record_event(event);return value
            except Exception as exc:
                event.update(status='failed',error={'type':type(exc).__name__,'message':str(exc)[:1200]})
                record_event(event);raise
        return call
    for side in ('left','right'):setattr(getattr(sdk,side+'_gripper'),'move_to',move_to(side))
    return sdk
