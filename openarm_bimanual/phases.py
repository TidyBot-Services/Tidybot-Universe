"""Public DAG markers derived from completed physical SDK receipts.

These describe execution. Only the unchanged native evaluator judges the task.
The ledger does not dispatch commands or let policy code claim a phase finished.
"""


class PhaseLedger:
    def __init__(self):
        self.active=None;self.group=None;self.transferred=False;self.sequence=0;self.donor="left";self.receiver="right";self.grasp_schema=None

    def marker(self, event, node, kind, arms, *, start=False):
        time=float(event.get('timestamp',0))
        if start:time=max(0.,time-float(event.get('duration_ms',0))/1000.)
        marker={'schema_version':'attentionbench.sdk-event.v1','event_id':f'bimanual-phase-{self.sequence}',
            'timestamp':time,'timestamp_basis':event.get('timestamp_basis','relative'),
            'source':'openarm_bimanual.receipt_ledger','event_type':'skill.'+kind,'operation':node,
            'status':{'started':'started','finished':'completed','blocked':'failed'}[kind],'duration_ms':0.,
            'arguments':{'skill_id':node,'participating_arms':arms,'coordination_group':self.group,
                         'sdk_event_id':event.get('event_id')},
            'result':{'execution_only':True},'error':event.get('error') if kind=='blocked' else None}
        self.sequence+=1;return marker

    def consume(self, event):
        source=event.get('source');op=event.get('operation');ok=event.get('status')=='completed'
        result=event.get('result') or {};before=[];after=[]
        def start(node,arms):
            self.active=node;before.append(self.marker(event,node,'started',arms,start=True))
        def finish(node,arms):
            after.append(self.marker(event,node,'finished',arms));self.active=None
        if self.active is None and not self.transferred and source in {f'robot_sdk.{self.donor}_arm',f'robot_sdk.{self.donor}_gripper'}:
            start(self.donor+'_pickup',[self.donor])
        if source=='robot_sdk.bimanual' and op=='begin_handoff' and ok and result.get('phase')=='receiving':
            self.group=result.get('id')
            self.donor=result.get('donor',self.donor);self.receiver=result.get('receiver',self.receiver)
            self.grasp_schema=result.get('schema')
            if self.active and self.active.endswith('_pickup'):finish(self.active,[self.donor])
            # Receiving starts when the real donor proof has passed, after this call.
            self.active=self.receiver+'_acquire';after.append(self.marker(event,self.active,'started',[self.receiver]))
        if (source==f'robot_sdk.{self.receiver}_gripper' and op in {'close','grasp'} and self.active==self.receiver+'_acquire'
                and ok and result.get('status')=='completed' and result.get('contact_hold_committed') is True):
            finish(self.receiver+'_acquire',[self.receiver])
        if source=='robot_sdk.bimanual' and op=='handoff':
            start('physical_handoff',['left','right'])
            if ok and result.get('status')=='completed' and result.get('handoff') is True and (result.get('schema')!='openarm.grasp/2' or (result.get('handoff_completed') is True and result.get('ownership')==self.receiver)):
                finish('physical_handoff',['left','right']);self.transferred=True
        if self.transferred and source==f'robot_sdk.{self.receiver}_arm' and op=='move_to_position' and self.active is None:
            start(self.receiver+'_transport',[self.receiver])
        if self.transferred and source==f'robot_sdk.{self.receiver}_gripper' and op=='open':
            if self.active==self.receiver+'_transport':
                # The transport interval ends at the start of intentional release.
                end=self.marker(event,self.receiver+'_transport','finished',[self.receiver],start=True)
                before.append(end);self.active=None
            start('sink_release',[self.receiver])
            if ok and result.get('status')=='completed':finish('sink_release',[self.receiver])
        if not ok and self.active:
            arms=[self.donor] if self.active==self.donor+'_pickup' else ['left','right'] if self.active=='physical_handoff' else [self.receiver]
            after.append(self.marker(event,self.active,'blocked',arms))
        return before,after
