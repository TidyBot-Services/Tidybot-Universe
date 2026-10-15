"""A human Hint is tied to a hand and a named phase in a particular attempt."""
def validate_scope(value, *, attempt_id):
    if (not isinstance(value,dict) or set(value)!={'arm','phase','attempt_id'}
            or value['arm'] not in {'left','right','both'}
            or value['phase'] not in {'pickup','handoff','placement'}
            or value['attempt_id']!=attempt_id):
        raise ValueError('Both arms Hint requires an explicit hand, phase and matching request attempt')
    return dict(value)


def compile_hint(text, scope):
    import math, re
    receipt={'original':text,'compiled':text,'parameters':{},'status':'unrecognized','rule':None,'scope':scope}
    try:scope=validate_scope(scope,attempt_id=scope.get('attempt_id') if isinstance(scope,dict) else None)
    except ValueError:
        receipt.update(status='unsupported',rule='bimanual_scope_required_v1');return receipt
    permitted={('left','pickup'):{'grasp_offset_m':(0.,.020)},
               ('right','handoff'):{'receiving_offset_m':(.025,.035)}}.get((scope['arm'],scope['phase']),{})
    marker='AB_CONTROL_V1 '
    if not isinstance(text,str) or len(text)>4000 or text.count(marker)!=1 or not text.rstrip().endswith(';'):return receipt
    values={}
    for part in text.split(marker,1)[1].split(';')[:-1]:
        match=re.fullmatch(r'\s*([a-z_]+)=([0123456789.+eE-]+)',part)
        if match is None:return receipt
        key,raw=match.groups()
        if key not in permitted:
            receipt.update(status='unsupported',rule='bimanual_hand_phase_mismatch_v1');return receipt
        if key in values:return receipt
        try:value=float(raw)
        except ValueError:return receipt
        lo,hi=permitted[key]
        if not math.isfinite(value) or not lo<=value<=hi:return receipt
        values[key]=value
    if values:receipt.update(status='supported',parameters=values,rule='bimanual_scoped_controls_v1')
    return receipt
