"""Content addressed public cuts. Replay is file rehydration, never a session fork."""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import re
import stat
from .dev_agent_workspace import Workspace, atomic_write, encode, read_beneath, share, shared_name

TREE_LIMIT=32*1024*1024  # Untrusted authored work/scratch only.
PUBLIC_FILE_LIMIT=512*1024*1024


def freeze_origin(workspace, cut, *, metadata=None, quiesce=nullcontext):
    if not re.fullmatch(r'(pre-submit|post-exec|post-feedback)-v[1-9][0-9]*(-pre-help)?',cut):
        raise ValueError('invalid_snapshot_cut')
    store=workspace.private/'snapshots';store.mkdir(exist_ok=True)
    with quiesce():
        # Only authored work/scratch comes from hostile ws. All public history,
        # docs and responses are regenerated from the controller's sealed copies.
        workspace.regenerate()
        files={};total=0;authored_total=0;authored_count=0
        for root in (workspace.canonical,workspace.ws/'work',workspace.ws/'scratch'):
            if not root.exists():continue
            if not stat.S_ISDIR(root.lstat().st_mode):raise ValueError('unsafe_snapshot_root')
            for path in sorted(root.rglob('*')):
                mode=path.lstat().st_mode
                if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    raise ValueError('unsafe_snapshot_file')
                if path.is_dir():continue
                name=str(path.relative_to(workspace.canonical)) if root==workspace.canonical else str(path.relative_to(workspace.ws))
                if name.startswith('rpc/'):continue # never replay request ids/responses
                public=root==workspace.canonical
                linked=public and shared_name(name) and not mode&0o222
                raw=read_beneath(root,str(path.relative_to(root)),PUBLIC_FILE_LIMIT if public else TREE_LIMIT,shared=linked)
                if not public:
                    authored_count+=1;authored_total+=len(raw)
                    if authored_count>65536:raise ValueError('snapshot_file_limit')
                    if authored_total>TREE_LIMIT:raise ValueError('snapshot_oversize')
                total+=len(raw)
                digest=hashlib.sha256(raw).hexdigest()
                if not (store/'objects'/digest).exists():
                    # A frame object is the same read-only inode as the canonical frame.
                    if linked:share(root,name,store,'objects/'+digest)
                    else:atomic_write(store,'objects/'+digest,raw)
                files[name]=dict(sha256=digest,bytes=len(raw),mode=0o600)
        manifest=dict(schema='attentionbench.agent-cut.v1',cut=cut,files=files,bytes=total,
                      metadata=metadata or {},replay='file_rehydration')
        data=encode(manifest); digest=hashlib.sha256(data).hexdigest()
        pointer=store/(cut+'.json')
        if pointer.exists() and pointer.read_bytes()!=data:raise ValueError('immutable_snapshot_conflict')
        atomic_write(store,'manifests/'+digest+'.json',data)
        atomic_write(store,cut+'.json',data)
        return dict(path=str(store/'manifests'/ (digest+'.json')),sha256=digest)


def provision_replay(cut, destination):
    path=Path(cut['path']);raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=cut['sha256']:raise ValueError('snapshot_digest_mismatch')
    manifest=json.loads(raw);destination=Path(destination)
    if destination.exists():raise ValueError('replay_requires_fresh_workspace')
    workspace=Workspace(destination)
    from .frame_cleanup import is_frame_image, receipt_for, cleaned_files
    cleanup_root, receipt = receipt_for(path)
    removed_objects = {cleanup_root/n for n in cleaned_files(cleanup_root, receipt)} if receipt else set()
    frames_cleaned = 0
    for name,entry in manifest['files'].items():
        digest=entry['sha256']
        if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('invalid_object_id')
        obj = path.parent.parent/'objects'/digest
        if is_frame_image(name) and not obj.exists() and obj in removed_objects:
            frames_cleaned += 1
            continue
        data=read_beneath(path.parent.parent,'objects/'+digest,TREE_LIMIT if name.startswith(('work/','scratch/')) else PUBLIC_FILE_LIMIT,shared=shared_name(name))
        if len(data)!=entry['bytes'] or hashlib.sha256(data).hexdigest()!=digest:raise ValueError('snapshot_object_mismatch')
        if name.startswith(('work/','scratch/')):atomic_write(workspace.ws,name,data)
        else:workspace.publish(name,data)
    for name in ('rpc/requests','rpc/responses','work','scratch'):(workspace.ws/name).mkdir(parents=True,exist_ok=True)
    atomic_write(workspace.private,'replay-origin.json',encode(dict(cut, frames_cleaned=frames_cleaned)))
    return workspace
