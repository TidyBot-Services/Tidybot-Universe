"""Exact RoboCasa365 applicability around the common evidence-gated Memory Service."""

from .config import memory_context

SCOPE = (
    "benchmark",
    "suite",
    "robot",
    "controller",
    "task_id",
    "perception_mode",
    "scene_id",
    "object_set_id",
    "camera_config_id",
    "task_variant_id",
)


class ScopedMemoryGateway:
    def __init__(self, gateway, context):
        if context.get("suite") != "robocasa365" or any(
            not isinstance(context.get(k), str) or not context[k] for k in SCOPE
        ):
            raise ValueError("complete RoboCasa365 Memory identity required")
        self.gateway = gateway
        self.context = dict(context)

    def __getattr__(self, name):
        return getattr(self.gateway, name)

    def _check(self, context):
        if context != self.context:
            raise PermissionError("Memory retrieval/authorization context mismatch")

    def retrieve(self, context, *, now):
        self._check(context)
        return [
            m
            for m in self.gateway.retrieve(context, now=now)
            if getattr(m.status, "value", m.status) == "trusted"
            and type(m.version) is int
            and m.version > 0
            and all(m.applicability.get(k) == context[k] for k in SCOPE)
        ]

    def authorize_use(self, context, *, memory_id, attempt_id, now):
        self._check(context)
        matches = self.retrieve(context, now=now)
        item = next((m for m in matches if m.memory_id == memory_id), None)
        if item is None:
            raise PermissionError("Memory version has no validated RoboCasa365 scope")
        grant = self.gateway.authorize_use(
            context, memory_id=memory_id, attempt_id=attempt_id, now=now
        )
        if (
            grant.get("context") != context
            or grant.get("attempt_id") != attempt_id
            or grant.get("version") != item.version
        ):
            raise PermissionError("Memory attempt-bound grant mismatch")
        return grant

    def create_candidate(self, **payload):
        scope = payload.get("applicability", {})
        if any(
            scope.get(k) != self.context[k]
            for k in ("suite", "task_id", "perception_mode")
        ) or any(k in scope and scope[k] != self.context[k] for k in SCOPE):
            raise PermissionError("candidate source scope mismatch")
        payload["applicability"] = {k: self.context[k] for k in SCOPE}
        return self.gateway.create_candidate(**payload)
