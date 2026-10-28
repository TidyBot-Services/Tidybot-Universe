"""Agent event wrapper. Rejections are values, fatal RPC failures still raise."""
from types import SimpleNamespace
from openarm365_dev.policy_validation import OPERATIONS


def build_sdk(owner, record_event):
    def method(group, operation):
        def call(*args, **kwargs):
            event = dict(source=group, operation=operation, arguments=dict(args=list(args), kwargs=kwargs))
            try:
                value = owner.rpc(group, operation, args, kwargs)
                rejected = isinstance(value, dict) and value.get('rejected') is True
                event.update(status='rejected' if rejected else 'completed', result=value)
                record_event(event)
                return value
            except Exception as exc:
                # Same shape as the one-call SDK event: the runner reads error.type
                # (OutcomeUnknown); a bare string crashed it after a real fault (r5).
                event.update(status='failed', error={'type': type(exc).__name__, 'message': str(exc)[:1200]})
                record_event(event); raise
        return call
    return SimpleNamespace(**{g: SimpleNamespace(**{op: method(g, op) for op in ops}) for g, ops in OPERATIONS.items()})
