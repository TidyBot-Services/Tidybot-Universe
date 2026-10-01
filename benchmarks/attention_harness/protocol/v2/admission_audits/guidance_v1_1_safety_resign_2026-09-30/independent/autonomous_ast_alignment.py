"""Exact autonomous robot-program comparison after explicit constant reduction.

No simulator or policy worker is executed. The approved compiler's function
definitions are evaluated only with empty public input, after excluding SDK
names. The entire remaining robot program must match, not sampled fixtures.
"""
import ast
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT.parent / 'Tidybot-Universe-attention-native/benchmarks/attention_harness/protocol/v2/review_packages/guidance_adoption_v1_1_dev_2026-09-29'


def literal(value):
    return ast.parse(repr(value), mode='eval').body


def value(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


class EmptyInput(ast.NodeTransformer):
    def __init__(self, bindings=None):
        self.bindings = bindings or {}

    def visit_Name(self, node):
        return copy.deepcopy(self.bindings.get(node.id, node))

    def visit_Call(self, node):
        node = self.generic_visit(node)
        if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                and node.func.value.id == 'adoption_parameters' and node.func.attr == 'get'):
            if len(node.args) != 2 or node.keywords:
                raise ValueError('unproved parameter lookup')
            return node.args[1]
        # The unchanged SDK's explicit default is the old implicit default.
        if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                and node.func.value.id == 'gripper' and node.func.attr in ['open', 'close']):
            node.keywords = [k for k in node.keywords if not (k.arg == 'settle_steps' and value(k.value) == 10)]
        if isinstance(node.func, ast.Name) and node.func.id == 'approach':
            defaults = {'tolerance': .018, 'max_steps': 80, 'stop_distance': .025}
            node.keywords = [k for k in node.keywords if not (k.arg in defaults and value(k.value) == defaults[k.arg])]
        return node

    def visit_Compare(self, node):
        node = self.generic_visit(node)
        if (len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)) and
                isinstance(node.comparators[0], ast.Name) and node.comparators[0].id == 'adoption_parameters'):
            return ast.Constant(isinstance(node.ops[0], ast.NotIn))
        if len(node.ops) == 1 and isinstance(node.left, ast.Constant) and isinstance(node.comparators[0], ast.Constant):
            if isinstance(node.ops[0], ast.Eq):
                return ast.Constant(node.left.value == node.comparators[0].value)
        return node

    def visit_BoolOp(self, node):
        node = self.generic_visit(node)
        if all(isinstance(v, ast.Constant) and type(v.value) is bool for v in node.values):
            return ast.Constant(any(v.value for v in node.values) if isinstance(node.op, ast.Or) else all(v.value for v in node.values))
        return node

    def visit_If(self, node):
        test = self.visit(node.test)
        if isinstance(test, ast.Constant) and type(test.value) is bool:
            result = []
            for item in node.body if test.value else node.orelse:
                reduced = self.visit(item)
                result.extend(reduced if isinstance(reduced, list) else [reduced])
            return result
        node.test = test
        node.body = [self.visit(x) for x in node.body]
        node.orelse = [self.visit(x) for x in node.orelse]
        return node

    def visit_IfExp(self, node):
        node = self.generic_visit(node)
        if isinstance(node.test, ast.Constant) and type(node.test.value) is bool:
            return node.body if node.test.value else node.orelse
        return node

    def visit_Subscript(self, node):
        node = self.generic_visit(node)
        if isinstance(node.value, (ast.List, ast.Tuple)) and isinstance(node.slice, ast.Constant):
            return copy.deepcopy(node.value.elts[node.slice.value])
        return node

    def visit_BinOp(self, node):
        node = self.generic_visit(node)
        if isinstance(node.op, ast.Add):
            if isinstance(node.left, ast.Constant) and node.left.value == 0:
                return node.right
            if isinstance(node.right, ast.Constant) and node.right.value == 0:
                return node.left
        return node


def program(body):
    return ast.Module(body=[x for x in body if not (isinstance(x, ast.Expr) and isinstance(x.value, ast.Constant) and isinstance(x.value.value, str))], type_ignores=[])


def compare(suite):
    task = 'cube_lift' if suite == 'robosuite' else 'counter_to_sink'
    new_path = PACKAGE / f'frozen/final_policies/{suite}_{task}.py'
    old_path = PACKAGE / f'frozen/inputs/{suite}_old_policy.py'
    new = ast.parse(new_path.read_text())
    old = ast.parse(old_path.read_text())
    index = next(i for i, n in enumerate(new.body) if isinstance(n, ast.Assign) and
                 any(isinstance(t, ast.Name) and t.id == 'adoption_parameters' for t in n.targets))
    functions = [n for n in new.body[:index] if isinstance(n, ast.FunctionDef)]
    if any(not (isinstance(n, (ast.FunctionDef, ast.ImportFrom)) or
                (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)))
           for n in new.body[:index]):
        raise ValueError('unproved executable statement in compiler prelude')
    forbidden = {'sensors', 'base', 'arm', 'gripper', 'context', 'robot_sdk'}
    if any(isinstance(n, ast.Name) and n.id in forbidden for f in functions for n in ast.walk(f)):
        raise ValueError('compiler prelude can access robot/context')
    # Only definitions are evaluated; the rest of the approved policy is AST data.
    namespace = {}
    exec(compile(ast.fix_missing_locations(program(functions)), '<approved compiler definitions>', 'exec'), namespace)
    empty = namespace['compile_attention']({}, suite)
    if empty != {}:
        raise ValueError('empty guidance does not produce empty parameters')
    body = [n for n in new.body[:index] if isinstance(n, ast.ImportFrom)] + new.body[index + 1:]
    reduced = EmptyInput().visit(program(body))
    if suite == 'robosuite':
        points = next(n for n in reduced.body if isinstance(n, ast.Assign) and n.targets[0].id == 'points')
        guided = next(n for n in reduced.body if isinstance(n, ast.FunctionDef) and n.name == 'guided_approach')
        if len(guided.body) != 5 or not all(isinstance(n, ast.Assign) for n in guided.body[:-1]) or not isinstance(guided.body[-1], ast.Expr):
            raise ValueError('unproved guided helper shape')
        output = []
        for node in reduced.body:
            if node is points or node is guided:
                continue
            if isinstance(node, ast.FunctionDef) and node.name == 'approach':
                if [a.arg for a in node.args.args] != ['x', 'y', 'z', 'tolerance', 'max_steps', 'stop_distance'] or [value(v) for v in node.args.defaults] != [.018, 80, .025]:
                    raise ValueError('unproved approach signature/defaults')
                node.body = [EmptyInput({'tolerance': literal(.018), 'max_steps': literal(80), 'stop_distance': literal(.025)}).visit(n) for n in node.body]
                node.args.args = node.args.args[:3]
                node.args.defaults = []
            if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and
                    isinstance(node.value.func, ast.Name) and node.value.func.id == 'guided_approach'):
                if len(node.value.args) != 1 or node.value.keywords or type(value(node.value.args[0])) is not int:
                    raise ValueError('unproved guided call')
                bindings = {'points': points.value, 'index': node.value.args[0]}
                for assignment in guided.body[:-1]:
                    if len(assignment.targets) != 1 or not isinstance(assignment.targets[0], ast.Name):
                        raise ValueError('unproved guided assignment')
                    bindings[assignment.targets[0].id] = EmptyInput(bindings).visit(copy.deepcopy(assignment.value))
                node = EmptyInput(bindings).visit(copy.deepcopy(guided.body[-1]))
            output.append(node)
        reduced.body = output
    left = ast.dump(program(old.body), include_attributes=False)
    right = ast.dump(reduced, include_attributes=False)
    return {'suite': suite, 'old_source': str(old_path), 'new_source': str(new_path),
            'old_sha256': hashlib.sha256(old_path.read_bytes()).hexdigest(),
            'new_sha256': hashlib.sha256(new_path.read_bytes()).hexdigest(),
            'empty_guidance_compiler_parameters': empty, 'whole_autonomous_robot_program_AST_equal': left == right,
            'old_normalized_AST_sha256': hashlib.sha256(left.encode()).hexdigest(),
            'new_normalized_AST_sha256': hashlib.sha256(right.encode()).hexdigest(),
            'normalization_rules': 'Empty guidance dictionary; default-only parameter lookups; absent demo branches; finite constant tuple indexes; zero offsets; explicit unchanged SDK defaults; Robosuite pure guided wrapper expansion and constant approach defaults.'}, left, right


if __name__ == '__main__':
    records = []
    for suite in ['robosuite', 'robocasa']:
        record, left, right = compare(suite)
        records.append(record)
        (ROOT / f'independent/{suite}_old_autonomous.ast.txt').write_text(left + '\n')
        (ROOT / f'independent/{suite}_new_autonomous.ast.txt').write_text(right + '\n')
    path = ROOT / 'independent/autonomous_AST_alignment.json'
    path.write_text(json.dumps({'passed': all(r['whole_autonomous_robot_program_AST_equal'] for r in records),
                               'records': records, 'new_robot_runs': 0, 'new_policy_worker_runs': 0,
                               'scope': 'Autonomous/no-guidance robot call and control-flow program equality for all observations; new diagnostic compiler output/CPU overhead is excluded and no execution-time identity or guided-effect invariance is claimed.'}, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    print(json.dumps(records, ensure_ascii=False))
