"""Agent single-file policy validation; frozen old validator is untouched."""
import ast
from openarm365_dev.policy_validation import Validator


def validate_source(code, *, config=None):
    if len(code.encode()) > 128 * 1024: raise ValueError('source_oversize')
    tree = ast.parse(code)
    Validator(config).visit(tree)
    if not any(isinstance(n, ast.ImportFrom) for n in tree.body):
        raise ValueError('SDK_import_required')
