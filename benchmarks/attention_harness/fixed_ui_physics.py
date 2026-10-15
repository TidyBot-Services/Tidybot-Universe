"""Read-only live preview wrapper around the version-verified physics runtime."""
raise RuntimeError('V2-only: historical modified solver/executor entry retired; use authenticated main deployment. No fallback.')
from pathlib import Path
import json,os,signal,sys
import numpy as np
from PIL import Image
from openarm_bimanual.fixed_service import Runtime as AcceptedRuntime,foundation

out=Path(sys.argv[1]);scenario=json.loads(Path(sys.argv[2]).read_text())
class PreviewRuntime(AcceptedRuntime):
    def physics_step(self):
        super().physics_step()
        if self.video is not None and self.total_steps%24==0:
            rgb=np.clip(np.array(self.camera.get_picture('Color'))[...,:3]*255,0,255).astype(np.uint8)
            temp=out/'preview.tmp';Image.fromarray(rgb).save(temp,format='JPEG',quality=88);os.replace(temp,out/'preview.jpg')
            state=json.loads(foundation.dump(self.state()));temp=out/'measured.tmp';temp.write_text(json.dumps(state));os.replace(temp,out/'measured.json')
            with (out/'frames.jsonl').open('a') as f:f.write(json.dumps({'sequence':self.total_steps//24,'physical_step':self.total_steps,'epoch':self.epoch})+'\n')

foundation.verify_freeze();physical=out/'physics';physical.mkdir()
(physical/'scenario.json').write_text(json.dumps(scenario,indent=2))
rt=PreviewRuntime(physical,'http://127.0.0.1:17553',scenario)
server=foundation.Server(('127.0.0.1',17554),foundation.Handler);server.runtime=rt;server.timeout=.1
signal.signal(signal.SIGTERM,lambda *_:rt.closing.set());signal.signal(signal.SIGINT,lambda *_:rt.closing.set());rt.thread.start()
try:
    while not rt.closing.is_set():server.handle_request()
finally:rt.closing.set();server.server_close();rt.thread.join(15)
raise SystemExit(1 if rt.error else 0)
