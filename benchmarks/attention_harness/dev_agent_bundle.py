"""Deterministic, deliberately small Python helper-flattening language."""
import ast
from dataclasses import dataclass
import hashlib
import json
import re
import symtable
from .dev_agent_workspace import read_beneath, SOURCE_LIMIT


@dataclass(frozen=True)
class Bundle:
    script: bytes
    sources: dict
    source_map: dict


def bundle(workspace, entry=None):
    try:
        manifest_bytes=read_beneath(workspace,'work/submission.json',16384)
        manifest=json.loads(manifest_bytes)
    except FileNotFoundError:
        manifest={'entry': (entry or 'work/main.py').removeprefix('work/'), 'modules': []}
        manifest_bytes=json.dumps(manifest,sort_keys=True).encode()
    if not isinstance(manifest,dict) or set(manifest)-{'entry','modules'}: raise ValueError('invalid_submission_manifest')
    main=manifest.get('entry','main.py'); modules=manifest.get('modules',[])
    if not isinstance(modules,list) or not isinstance(main,str): raise ValueError('invalid_submission_manifest')
    names=[*modules, main]
    if any(not isinstance(n,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*\.py',n) for n in names):
        raise ValueError('only_flat_python_modules_supported')
    if len(set(names))!=len(names): raise ValueError('duplicate_module')
    if entry is not None and entry != 'work/'+main: raise ValueError('entry_manifest_conflict')
    sources={n:read_beneath(workspace,'work/'+n,SOURCE_LIMIT) for n in names}
    if sum(map(len,sources.values()))>SOURCE_LIMIT: raise ValueError('source_oversize')
    trees={n:ast.parse(s.decode('utf-8'),filename=n) for n,s in sources.items()}
    exports={}; owners={}; dependencies={n:[] for n in names}; removed={n:set() for n in names}
    # Reject shared globals that would change semantics when concatenated. Local
    # helpers are pure definitions/constants; no helper module side effects.
    for name,tree in trees.items():
        bindings=set()
        for node in tree.body:
            if name!=main and not isinstance(node,(ast.FunctionDef,ast.Assign,ast.AnnAssign,ast.ImportFrom)):
                raise ValueError('helper_definitions_only')
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)): bindings.add(node.name)
            if isinstance(node,(ast.Assign,ast.AnnAssign)):
                targets=node.targets if isinstance(node,ast.Assign) else [node.target]
                if any(not isinstance(t,ast.Name) for t in targets): raise ValueError('simple_globals_required')
                bindings.update(t.id for t in targets)
                if name!=main:
                    try: ast.literal_eval(node.value)
                    except (ValueError,TypeError): raise ValueError('helper_constants_must_be_literals') from None
        for binding in bindings:
            if binding in owners: raise ValueError('conflicting_name:'+binding)
            owners[binding]=name
        exports[name]=bindings
        if len(names)>1:
            for node in tree.body:
                if isinstance(node,(ast.For,ast.While,ast.If,ast.Try,ast.With,ast.AugAssign,ast.Delete)):
                    raise ValueError('top_level_control_flow_unsupported_in_bundle')
    sdk_names=set()
    for name,tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node,(ast.Global,ast.Nonlocal)): raise ValueError('shared_namespace_mutation')
            if isinstance(node,ast.ImportFrom) and node.module!='robot_sdk':
                dep=(node.module or '')+'.py'
                if node not in tree.body or node.level or dep not in sources or dep==main:
                    raise ValueError('unsupported_local_import')
                for alias in node.names:
                    if alias.asname or alias.name not in exports[dep]: raise ValueError('unsupported_import_name')
                dependencies[name].append(dep)
                removed[name].update(range(node.lineno,node.end_lineno+1))
            elif isinstance(node,ast.ImportFrom): sdk_names.update(a.name for a in node.names)
        # An unimported cross-module global would acquire a different meaning.
        imported={a.name for n in tree.body if isinstance(n,ast.ImportFrom) and n.module!='robot_sdk' for a in n.names}
        for node in tree.body:
            if isinstance(node,ast.FunctionDef) and name!=main and (node.decorator_list or node.args.defaults or any(node.args.kw_defaults)):
                raise ValueError('helper_defaults_or_decorators_unsupported')
        # Binding collisions include SDK imports, which remain in the bundle.
        if set(owners)&sdk_names: raise ValueError('conflicting_sdk_name')
        table=symtable.symtable(sources[name].decode(),name,'exec')
        def globals_used(scope):
            for symbol in scope.get_symbols():
                if symbol.is_referenced() and symbol.is_global():yield symbol.get_name()
            for child in scope.get_children():yield from globals_used(child)
        for symbol in globals_used(table):
            if symbol in owners and owners[symbol]!=name and symbol not in imported:
                raise ValueError('unimported_shared_name:'+symbol)
    order=[]; visiting=set()
    def visit(name):
        if name in visiting: raise ValueError('module_cycle')
        if name in order:return
        visiting.add(name)
        for dep in sorted(dependencies[name]):visit(dep)
        visiting.remove(name);order.append(name)
    for name in sorted(modules):visit(name)
    visit(main)
    output=[]; mapping=[]
    for name in order:
        lines=sources[name].decode().splitlines(keepends=True)
        # Single-file bundles retain bytes exactly, including boundary-sized inputs.
        if len(names)>1:
            output.append('# source: '+name+'\n'); mapping.append(dict(file=name,line=None))
        for number,line in enumerate(lines,1):
            if number not in removed[name]:
                output.append(line);mapping.append(dict(file=name,line=number))
        if output and not output[-1].endswith('\n') and name!=order[-1]: output[-1]+='\n'
    script=''.join(output).encode()
    if len(script)>SOURCE_LIMIT: raise ValueError('bundle_oversize')
    from openarm365_dev_v2.agent_policy import validate_source
    from benchmarks.attention_harness.robocasa_native.policy_sandbox import GeneratedPolicyError
    try:validate_source(script.decode())
    except GeneratedPolicyError as exc:raise ValueError('invalid_policy:'+str(exc)) from exc
    return Bundle(script,sources,dict(entry=main,order=order,lines=mapping,
        manifest=manifest,manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        originals={n:hashlib.sha256(s).hexdigest() for n,s in sorted(sources.items())},
        sha256=hashlib.sha256(script).hexdigest()))
