"""V2 owner start override; RPC/lifetime/cleanup remain inherited unchanged."""
import json,os,selectors,subprocess
from pathlib import Path
from openarm365_dev.owner import NativeOwner
class NativeOwnerV2(NativeOwner):
    def __init__(self,*args,source_root=None,agent_mode=False,**kwargs):
        super().__init__(*args,**kwargs);self.source_root=Path(source_root) if source_root is not None else None
        self.startup_response=None;self.agent_mode=agent_mode
    def stop(self,reason='normal_cleanup'):
        from .live_retention import prune_live
        try:return super().stop(reason)
        finally:
            pruning=prune_live(self.evidence)
            if self.cleanup is not None:self.cleanup['live_pruning']=pruning
    def start(self):
        env={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','CUROBO_'))
            and not k.startswith('ATTENTION_MEMORY_')
            and k not in {'LD_PRELOAD','LD_LIBRARY_PATH'}
            and not any(s in k.upper() for s in ('KEY','TOKEN','SECRET','PASSWORD','IMPORTER','OPERATOR','HUMAN','CAPABILITY'))}
        project=self.source_root or Path(__file__).resolve().parents[1]
        env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONHASHSEED='0',MUJOCO_GL='egl',
            PYTHONPATH=str(project),
            XDG_CACHE_HOME=str(self.evidence/'cache'),NUMBA_CACHE_DIR=str(self.evidence/'numba-cache'))
        self.log=(self.evidence/'native-service.log').open('ab')
        self.process=subprocess.Popen([self.python,'-B','-u','-m','openarm365_dev_v2.worker',
            '--config',str(self.config),'--evidence',str(self.evidence),'--deadline',str(self.deadline),
            '--owner-pid',str(os.getpid()),*(['--agent-mode'] if self.agent_mode else [])],env=env,cwd=project,stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=self.log,start_new_session=True)
        from benchmarks.attention_harness.dev_loop import process_identity
        launch={'argv':self.process.args,'cwd':str(project),'PYTHONPATH':env['PYTHONPATH'],
                'owner_identity':process_identity(self.process.pid)}
        (self.evidence/'worker-launch.json').write_text(json.dumps(launch,indent=2)+'\n')
        if self.process_observer:self.process_observer(self.process.pid)
        self.selector=selectors.DefaultSelector();self.selector.register(self.process.stdout,selectors.EVENT_READ)
        while True:
            message=self.read()
            self.startup_response=message
            if message.get('kind')=='ready':self.ready=message;return message
            if message.get('kind')=='fatal':raise RuntimeError('Native startup failed: '+message['error'])
            raise RuntimeError('Unexpected native startup message')
