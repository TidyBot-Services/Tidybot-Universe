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
            except Exception:
                event.update(status='failed', error='fatal_sdk_error'); record_event(event); raise
        return call
    return SimpleNamespace(**{g: SimpleNamespace(**{op: method(g, op) for op in ops}) for g, ops in OPERATIONS.items()})
