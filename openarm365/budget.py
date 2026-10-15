"""Single-attempt full-task budgets require approved config and policy hashes."""
from .config import BUDGETS

def validate_run_budget(value,config_sha,policy_sha):
    task=value.get('task_id')
    if task not in BUDGETS: raise ValueError('unsupported OpenArm365 task budget')
    horizon,calls,attempt,whole=BUDGETS[task]
    expected=dict(schema_version='attentionbench.openarm365-run-budget.v1',suite='robocasa365',formal_eligible=False,max_attempts=1,assistance_credits=0,token_limit=0,human_deadline_seconds=30,attempt_wall_seconds=attempt,whole_case_wall_seconds=whole,overall_deadline_seconds=whole,task_id=task,approved_config_sha256s=[config_sha],approved_policy_sha256=policy_sha)
    if value!=expected or any(type(value[k]) is not type(v) for k,v in expected.items()): raise ValueError('OpenArm365 complete run budget/config/policy mismatch')
    return value
