import ast
from benchmarks.attention_harness.robocasa_native.policy_sandbox import GeneratedPolicyError, FORBIDDEN_NAMES

OPERATIONS = {
    "sensors": {"get_observation", "find_objects"},
    "left_arm": {"move_delta"},
    "right_arm": {"move_delta"},
    "left_gripper": {"open", "close"},
    "right_gripper": {"open", "close"},
    "bimanual": {"move_joints"},
    "base": {"move_delta"},
}


def operations(config=None):
    from curobo_v2.fixture_interaction import config_enabled
    if not config_enabled(config):return OPERATIONS
    return {**OPERATIONS,'sensors':OPERATIONS['sensors'] | {'find_fixtures'},
        **{g:OPERATIONS[g] | {'move_to'} for g in ('left_gripper','right_gripper')}}


class Validator(ast.NodeVisitor):
    def __init__(self,config=None):
        self.operations=operations(config)

    def visit_ClassDef(self, node):
        raise GeneratedPolicyError("class definitions forbidden")

    def visit_Import(self, node):
        raise GeneratedPolicyError("plain imports forbidden")

    def visit_ImportFrom(self, node):
        if (
            node.module != "robot_sdk"
            or node.level != 0
            or any(a.name not in OPERATIONS or a.asname for a in node.names)
        ):
            raise GeneratedPolicyError("only RoboCasa365 SDK imports allowed")

    def visit_Attribute(self, node):
        if node.attr.startswith("_"):
            raise GeneratedPolicyError("private attributes forbidden")
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id.startswith("__"):
            raise GeneratedPolicyError("dunder names forbidden")

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_NAMES:
            raise GeneratedPolicyError("forbidden call")
        if isinstance(node.func, ast.Attribute) and node.func.attr not in set().union(
            *self.operations.values(), {"get", "keys", "items", "values"}
        ):
            raise GeneratedPolicyError("forbidden method")
        self.generic_visit(node)


def validate_generated_policy(code, *, config=None, manifest=None):
    from openarm365_dev_v2.source_policy import source_limit
    limit=20000 if config is None else source_limit(config,manifest)
    if len(code.encode()) > limit:
        raise GeneratedPolicyError("policy exceeds 20KB" if config is None else "policy exceeds source size limit")
    tree = ast.parse(code)
    Validator(config).visit(tree)
    if not any(isinstance(n, ast.ImportFrom) for n in tree.body):
        raise GeneratedPolicyError("SDK import required")
