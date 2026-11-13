"""Memory applicability identity; never a deployment or validation authority.

Start at the planner owners and executor runners (public runtime entry points), walk
local imports, package initializers, literal dynamic imports and child Python
launches. Walk all branches conservatively without importing GPU/simulator code.
Resources come from lock-pinned robot/config/assets and literal runtime loads;
resource references are followed transitively. The whole-repo deployment lock
and its verification are deliberately unchanged.
"""
from __future__ import annotations

import ast
import hashlib
import json
import posixpath
from pathlib import Path
import re

# Entry points, not a manually maintained dependency list. Owners launch workers
# by path or -m; those edges are discovered below like ordinary imports.
ENTRYPOINTS = ('curobo_v2/session.py', 'curobo_v2/native_session.py',
               'openarm365_dev/runner.py', 'openarm365_dev_v2/runner.py')
ASSET_SUFFIXES = {'.json', '.yaml', '.yml', '.xml', '.urdf', '.obj', '.stl',
                  '.dae', '.ply', '.npy', '.npz', '.pt', '.bin', '.so', '.png'}
EXCLUDED_SUFFIXES = {'.md', '.html', '.css', '.js', '.bak', '.pyc'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def excluded(name):
    p = Path(name)
    return (any(x in {'tests', 'test', 'docs', 'ui', '__pycache__'} for x in p.parts)
            or name.startswith('benchmarks/attention_harness/ui') or p.suffix.lower() in EXCLUDED_SUFFIXES)


def _closure(root, inventory, entrypoints, *, local_imports=False):
    """Return import/launch closure and resource literals from locked inventory."""
    inventory = set(inventory)
    selected, literals, pending = set(), set(), list(entrypoints)

    def module(name):
        for prefix in ('', 'curobo_v2/service/'):
            stem = prefix + name.replace('.', '/')
            for candidate in (stem + '.py', stem + '/__init__.py'):
                if candidate in inventory or local_imports and (root / candidate).is_file():
                    inventory.add(candidate)
                    pending.append(candidate)

    while pending:
        name = pending.pop()
        if name in selected or excluded(name):
            continue
        if name not in inventory:
            raise ValueError('Planner behavior entry point is not locked: ' + name)
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Unsafe planner behavior source: ' + name)
        selected.add(name)
        for parent in Path(name).parents:
            init = str(parent / '__init__.py')
            if init != name and (init in inventory or local_imports and (root / init).is_file()):
                inventory.add(init)
                pending.append(init)
        tree = ast.parse(path.read_bytes(), filename=name)
        package = name.removesuffix('.py').split('/')[:-1]
        if package[:2] == ['curobo_v2', 'service']:
            package = package[2:]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module(alias.name)
            elif isinstance(node, ast.ImportFrom):
                parts = package[:len(package) - node.level + 1] if node.level else []
                base = '.'.join([*parts, *([node.module] if node.module else [])])
                module(base)
                for alias in node.names:
                    module(base + '.' + alias.name)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                literal = node.value
                literals.add((str(Path(name).parent), literal))
            elif isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Name) and node.func.id == '__import__'
                    or isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module'):
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    module(node.args[0].value)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == 'with_name' and node.args
                  and isinstance(node.args[0], ast.Constant)
                  and isinstance(node.args[0].value, str) and node.args[0].value.endswith('.py')):
                candidate = str(Path(name).parent / node.args[0].value)
                if candidate in inventory:
                    pending.append(candidate)
            elif isinstance(node, (ast.List, ast.Tuple)):
                strings = [child.value for child in ast.walk(node)
                           if isinstance(child, ast.Constant) and isinstance(child.value, str)]
                if '-m' in strings:
                    index = strings.index('-m')
                    if index + 1 < len(strings):
                        module(strings[index + 1])
                if any(flag in strings for flag in ('-B', '-I', '-P', '-u', '--lock')):
                    for literal in strings:
                        if literal.endswith('.py') and literal in inventory:
                            pending.append(literal)
    return selected, literals


def derived_files(lock, *, source_root=None, source_files=None, entrypoints=ENTRYPOINTS):
    """Derive a closed runtime manifest from imports plus lock-pinned resources.

    source_root/source_files describe client code actually loaded outside the
    frozen planner service. Service code and resources must be locked; local
    client imports also follow modules under its source root (Dev allows source
    drift). Scripts enter only through runtime imports/launches.
    """
    root = Path(source_root or lock['project_root'])
    project = Path(lock['project_root'])
    inventory = ({str(Path(p).relative_to(project)): h for p, h in lock['files'].items()
                  if Path(p).is_relative_to(project)} if source_files is None else dict(source_files))
    if any(Path(n).is_absolute() or '..' in Path(n).parts for n in inventory):
        raise ValueError('Unsafe planner behavior inventory')
    selected, literals = _closure(root, inventory, entrypoints, local_imports=source_root is not None)
    resources = set()
    # Robot files and assets are a closed pinned resource set, including meshes
    # referenced through dynamically constructed names in URDF/YAML loaders.
    pinned = [*lock.get('robot_configs', {}), lock.get('assets_root', '')]
    for name in inventory:
        path = root / name
        if excluded(name) or 'scripts' in Path(name).parts or path.suffix == '.py':
            continue
        frozen = project / name
        if any(p and (frozen == Path(p) or Path(p) in frozen.parents) for p in pinned):
            resources.add(name)
    # Literal file loads in reached Python, then references inside resources.
    # Resolve against project/module/resource roots; never sweep unrelated
    # files just because they share a basename such as config.json.
    remaining = {n for n in inventory if not excluded(n) and 'scripts' not in Path(n).parts
                 and Path(n).suffix.lower() in ASSET_SUFFIXES}
    while True:
        found = set(resources)
        for parent, text in literals:
            if not text or '\n' in text:
                continue
            for candidate in (posixpath.normpath(text), posixpath.normpath(str(Path(parent) / text))):
                if candidate in remaining:
                    found.add(candidate)
            if text.startswith('/'):
                found.update(n for n in remaining if text.endswith('/' + n))
        new = found - selected
        if not new:
            break
        selected.update(new)
        remaining.difference_update(new)
        resources.clear()
        for name in new:
            path = root / name
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError('Unsafe planner behavior resource: ' + name)
            if Path(name).suffix.lower() in {'.json', '.yaml', '.yml', '.xml', '.urdf'}:
                literals.update((str(Path(name).parent), text) for text in
                    re.findall(r'[\w./-]+\.[A-Za-z0-9]+', (root / name).read_text()))
    return sorted(selected)


def manifest(lock, execution_settings, *, client_root=None, client_files=None, model_sources=None):
    project = Path(lock['project_root'])
    deployment_root = project.parent.parent
    files = {}

    def collect(prefix, root, names, expected=None):
        for name in names:
            path = root / name
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError('Unsafe planner behavior source: ' + name)
            raw = path.read_bytes()
            if expected is not None and hashlib.sha256(raw).hexdigest() != expected[str(path)]:
                raise ValueError('Planner behavior source drift: ' + str(path))
            # Snapshot relocation changes these absolute prefixes in YAML only.
            # Bind every other byte, and bind the original base lock separately.
            if path.suffix in {'.yml', '.yaml', '.json', '.xml', '.urdf'}:
                raw = raw.replace(str(deployment_root).encode(), b'$DEPLOYMENT')
            files[prefix + name] = hashlib.sha256(raw).hexdigest()

    collect('service/', project, derived_files(lock, entrypoints=ENTRYPOINTS[:2]), lock['files'])
    if client_root is not None:
        root = Path(client_root)
        collect('client/', root, derived_files(lock, source_root=root, source_files=client_files))
    for name, digest in (model_sources or {}).items():
        path = Path(name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Planner behavior model drift: ' + name)
        relative = path.relative_to(client_root).as_posix()
        files['client/' + relative] = digest
    settings = {k: v for k, v in execution_settings.items() if k != 'planner_deployment'}
    return dict(schema='attentionbench.planner-behavior.v1',
                base_deployment_lock_sha256=lock['provenance']['base_deployment_lock_sha256'],
                execution_settings=settings, files=dict(sorted(files.items())))


def from_config(config):
    """Measure the authenticated snapshot and the current client runtime bytes."""
    from .identity import load_lock
    pin = config['execution_settings']['planner_deployment']
    lock, actual = load_lock(pin['lock_path'], tier='dev', boundary='dev_loop')
    if actual != pin['sha256']:
        raise ValueError('Planner behavior deployment lock mismatch')
    ref = config['dev_lock']
    raw = Path(ref['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('Planner behavior client lock drift')
    client = json.loads(raw)
    root = Path(client['source_root'])
    inventory = {str(Path(n).relative_to(root)): h for n, h in client['files'].items()
                 if Path(n).is_relative_to(root)}
    value = manifest(lock, config['execution_settings'], client_root=root,
                     client_files=inventory, model_sources=client['model_sources'])
    return hashlib.sha256(canonical(value)).hexdigest()
