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

# Count CJK characters individually and non-CJK whitespace-delimited runs as
# words. Punctuation attached to a run (0.0357, -0.05, move_delta()
# stays in it; standalone punctuation is not a word.
_CJK=r'\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af\U00020000-\U0002ebef'
_CJK_CHAR=re.compile(rf'[{_CJK}]')
_WORD_PARTS=re.compile(rf'[{_CJK}]|[^\s{_CJK}]+')


def count_words(text):
    """Return words: each CJK character is one; other text splits on whitespace."""
    return sum(1 for part in _WORD_PARTS.findall(text)
               if _CJK_CHAR.fullmatch(part)
               or any(char.isalnum() or char=='_' for char in part))


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
    """Count displayed words, including the expandable What changed diff."""
    parts=[view['question'],view['reasoning'],view['diff']]
    parts += [step['stop_reason'] for step in view['steps']]
    review=view['eval_review'];parts += [str(review['passed']),review['reason'],review['reference_suggestion']]
    return sum(count_words(part) for part in parts)


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
    """Estimate seconds from displayed video and read/write rates in words/s."""
    words=shown_words(view);answer_words=count_words(answer)
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
                value={key:item for key,item in value.items() if key not in ('raw_answer_excerpt','finish_reason')}
                review=visible_review(value)
                display=review['reason']+('\nReference suggestion: '+review['reference_suggestion'] if review['reference_suggestion'] else '')
                return {**value,**review,'feedback':display,'legacy_review':'reason' not in value}
    if legacy_path and Path(legacy_path).is_file():
        value=json.loads(Path(legacy_path).read_bytes())
        value={key:item for key,item in value.items() if key not in ('raw_answer_excerpt','finish_reason')}
        return {**value,'legacy_review':'reason' not in value}
    return None


def display_request_view(view):
    """Small, read-only checklist from the exact artifact sent to AdvisorProxy."""
    if not view:return None
    if view.get('schema_version')!='attentionbench.request-view.v1':raise ValueError('Unknown advisor request view')
    task=view['task'];frames=view['frames'];steps=view['steps'];review=view['eval_review']
    counts={name:sum(frame['view']==name for frame in frames) for name in VIEWS}
    items=[{'label':'Task','value':task['task_prompt']},
           {'label':'Question','value':view['question']},
           {'label':'Request','value':'Targeted request' if view['request_kind']=='targeted' else 'Generic request'}]
    if view['reasoning']:items.append({'label':'Dev reasoning','value':view['reasoning']})
    names={'overview':'Overview','side':'Side','top':'Top-down','left_wrist':'Left wrist','right_wrist':'Right wrist'}
    items += [{'label':names[name]+' frames','value':str(counts[name])} for name in VIEWS]
    items.append({'label':'Shown video duration','value':f"{view['shown_video_seconds']:g} s"})
    items.append({'label':'Steps','value':str(len(steps))})
    for step in steps:
        if step['stop_reason']:items.append({'label':'Step failure','value':step['label']+' — '+step['stop_reason']})
    items.append({'label':'Diff included','value':'Yes' if view['diff'] else 'No'})
    verdict='Completed' if review['passed'] is True else 'Not completed' if review['passed'] is False else 'Not reviewed'
    items.append({'label':'Eval verdict','value':verdict})
    if review['reason']:items.append({'label':'Eval objective reason','value':review['reason']})
    if review['reference_suggestion']:items.append({'label':'Eval reference suggestion','value':review['reference_suggestion']})
    return {'items':items,'never_shown':'Never shown: simulator ground truth and the native success score'}


def request_view_ref(help_content, read):
    """Resolve the archived AdvisorProxy input through verified evidence refs."""
    if not help_content:return None
    if help_content.get('request_view'):
        return help_content['request_view']  # Older display fixtures.
    advisor_ref=help_content.get('advisor_proxy')
    if not advisor_ref:return None
    advisor_record=read(advisor_ref)
    return (advisor_record or {}).get('request_view')
