"""Cost accounting policy for the user-approved free provider models."""

# User decision, 2026-10-18: parcc/GLM (Dev and AdvisorProxy) and
# parcc/Qwen (Eval) are free. Tokens are recorded but have no cost quota.
FREE_MODELS = frozenset({'parcc/GLM', 'parcc/Qwen'})
TOKEN_RESOURCES = ('provider_tokens', 'input_tokens', 'output_tokens')


def is_unmetered_model(model_name):
    return model_name in FREE_MODELS


def eval_is_unmetered(config):
    # Eval's exemption is valid only for its fixed Qwen configuration.
    from .eval_agent import MODEL
    return isinstance(config, dict) and config.get('enabled') is True and config.get('model') == MODEL and is_unmetered_model(MODEL)


def limited_resources(*, eval_config=None, model_names=()):
    """Resources still checked when allocating a new Dev experiment."""
    from .dev_ledger import RESOURCES
    free_eval = eval_is_unmetered(eval_config)
    free_tokens = model_names and all(is_unmetered_model(name) for name in model_names)
    return tuple(k for k in RESOURCES if not (free_eval and k == 'eval_calls')
                 and not (free_tokens and k in TOKEN_RESOURCES))
