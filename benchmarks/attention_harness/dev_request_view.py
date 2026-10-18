"""Public request projection shared by the operator surface and AdvisorProxy.

Only named public fields cross this boundary. Frames are public RGB samples;
the data URL is transported separately from the archived view manifest.
"""
from __future__ import annotations

import re
import json
import subprocess
from pathlib import Path

from .dev_help import public_screen

VIEWS=('overview','side','top','left_wrist','right_wrist')
READ_WPS=4.0
WRITE_WPS=0.7


def _ui_step_copy(events,failure):
    """Execute the existing live.js wording functions; no parallel prose map."""
    source=(Path(__file__).parent/'ui'/'live.js').read_text()
    start='  function plainAction('
    end='  function operatorTime('
    if source.count(start)!=1 or source.count(end)!=1:raise ValueError('UI step wording boundary changed')
    functions=start+source.split(start,1)[1].split(end,1)[0]
    script="const DEBUG_VIEW=false;const fs=require('fs');"+functions+'''
const input=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(input.events.map(event=>({
 label:plainEvent(event),
 error:plainError(event.error?.message || (typeof event.error==='string' ? event.error : null) || input.failure)
}))));'''
    result=subprocess.run(['node','-e',script],input=json.dumps({'events':events,'failure':failure},ensure_ascii=False),
                          text=True,capture_output=True,timeout=5,check=True)
    return json.loads(result.stdout)


def operator_steps(events, failure=''):
    """The same public action rows and display rules as live.js operatorEvents."""
    groups=[]
    for index,(event,copy) in enumerate(zip(events,_ui_step_copy(events,failure))):
        op=str(event.get('operation') or '')
        if event.get('status')!='failed' and (event.get('status')=='started' or event.get('event_type')=='sdk.sensor_read' or re.search(r'(^|\.)get_observation$',op) or (event.get('event_type')=='sdk.bimanual_control' and op=='state')):continue
        label=copy['label']
        if groups and groups[-1]['label']==label and groups[-1]['status']==event.get('status'):groups[-1]['count']+=1;continue
        status=event.get('status','')
        reason=copy['error'] if status in ('failed','blocked') else ''
        groups.append({'index':index,'label':label,'status':status,'count':1,'stop_reason':('Blocked: ' if status=='blocked' else 'Stopped: ')+reason if reason else ''})
    return groups


def shown_words(view):
    parts=[view['question'],view['reasoning'],view['diff']]
    parts += [step['stop_reason'] for step in view['steps']]
    review=view['eval_review'];parts += [str(review['passed']),review['reason'],review['reference_suggestion']]
    return sum(len(re.sub(r'\s+','',part)) for part in parts)


def build_request_view(*,task,question,reasoning='',frames,events=(),failure='',diff='',eval_review=None,localization=None,shown_video_seconds=0):
    from .eval_agent import visible_review
    if not isinstance(task,dict) or not isinstance(question,str) or not isinstance(reasoning,str) or not isinstance(diff,str):raise ValueError('invalid request view fields')
    frame_rows=[]
    for frame in frames:
        view='overview' if frame.get('view')=='external' else frame.get('view')
        if view not in VIEWS or not isinstance(frame.get('sha256'),str) or not isinstance(frame.get('data_url'),str):raise ValueError('invalid public frame')
        frame_rows.append({'view':view,'sha256':frame['sha256'],'filename':frame['filename'],'data_url':frame['data_url']})
    if len(frame_rows)>10:raise ValueError('frame limit exceeded')
    review=visible_review(eval_review or {})
    result={'schema_version':'attentionbench.request-view.v1','task':{'task_id':task['task_id'],'task_prompt':task['task_prompt']},
            'question':question,'reasoning':reasoning,'request_kind':'targeted' if (localization or {}).get('status')=='targeted' else 'generic',
            'frames':frame_rows,'steps':operator_steps(events,failure),'diff':diff,'eval_review':review,
            'shown_video_seconds':float(shown_video_seconds)}
    public_screen(result)
    return result


def human_seconds(view,answer):
    words=shown_words(view);answer_words=len(re.sub(r'\s+','',answer))
    return {'shown_video_seconds':view['shown_video_seconds'],'shown_words':words,'answer_words':answer_words,
            'read_wps':READ_WPS,'write_wps':WRITE_WPS,
            'hint_human_seconds':view['shown_video_seconds']+words/READ_WPS+answer_words/WRITE_WPS}


def display_eval_review(cell,version,*,legacy_path=None):
    """Read the settled Eval artifact for a UI projection; leave its bytes intact."""
    from .dev_loop import sha
    from .eval_agent import visible_review
    for job in cell.get('jobs',[]):
        if job['kind']=='eval' and job['version']==version and job['state']=='settled':
            ref=(job.get('receipt') or {}).get('eval_review')
            if ref:
                path=Path(ref['uri'])
                if sha(path)!=ref['sha256']:raise ValueError('Eval display evidence digest mismatch')
                value=json.loads(path.read_bytes())
                review=visible_review(value)
                display=review['reason']+('\nReference suggestion: '+review['reference_suggestion'] if review['reference_suggestion'] else '')
                return {**value,**review,'feedback':display}
    if legacy_path and Path(legacy_path).is_file():return json.loads(Path(legacy_path).read_bytes())
    return None
