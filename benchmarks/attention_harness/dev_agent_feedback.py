"""Agent outcome semantics, independent of the frozen PUBLIC_FAILURE_RULE."""

def public_outcome(status, events, *, error=None):
    rejected = sum(e.get('status') == 'rejected' for e in events)
    fatal = any(e.get('status') in ('failed', 'timeout', 'cancelled') for e in events)
    completed = status == 'completed' and not error and not fatal
    return dict(schema='attentionbench.agent-public-outcome.v1',
                status=('completed_with_rejections' if rejected else 'completed') if completed else 'failed',
                completed=completed, rejected_calls=rejected, sdk_calls=len(events),
                error=None if completed else 'execution_failed')
