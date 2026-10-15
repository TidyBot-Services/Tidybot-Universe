"""Auditable live-human text adapter; frozen policy sources remain unchanged.

This is a bounded cube-lift Chinese preset, not a general natural-language
planner. Unknown, conditional, negated or malformed controls pass through.
"""
from __future__ import annotations
import re
import math
from typing import Any
from .protocol.v2.guidance_adoption_v1.control_compiler import text_parameters


def prepare_human_guidance(text: str, *, suite: str, task_id: str, robot=None, guidance_scope=None) -> dict[str, Any]:
    if not isinstance(text, str) or len(text) > 4000:
        raise ValueError('human guidance must be a bounded public string')
    if suite == 'robocasa' and (robot or {}).get('execution_arm') == 'both':
        from openarm_bimanual.hint import compile_hint
        return compile_hint(text,guidance_scope)
    if suite == 'robocasa' and (robot or {}).get('model') == 'OpenArm':
        receipt = {'original':text,'compiled':text,'parameters':{},'status':'unrecognized','rule':None}
        marker = 'AB_CONTROL_V1 '
        if text.count(marker) != 1:
            return receipt
        values = {}
        for part in text.split(marker,1)[1].split(';')[:-1]:
            match = re.fullmatch(r'\s*([a-z_]+)=([0123456789.+eE-]+)',part)
            if match is None:
                return receipt
            key, raw = match.groups()
            if key in {'base_forward_m','open_settle_steps','close_settle_steps','approach_tolerance_m'}:
                receipt.update(status='unsupported',rule='openarm_control_unavailable_v1')
                return receipt
            if key != 'grasp_offset_m' or key in values:
                return receipt
            try:
                value = float(raw)
            except ValueError:
                return receipt
            if not math.isfinite(value) or not 0 <= value <= .020:
                return receipt
            values[key] = value
        if values and text.rstrip().endswith(';'):
            receipt.update(parameters=values,status='supported',rule='openarm_grasp_control_v1')
        return receipt
    parameters = text_parameters(text, suite) if suite in {'robosuite', 'robocasa'} else {}
    receipt = {'original': text, 'compiled': text, 'parameters': parameters,
               'status': 'supported' if parameters else 'unrecognized',
               'rule': 'policy_text_parameters_v1' if parameters else None}
    # Never rescue an invalid explicit command by interpreting surrounding text.
    if parameters or 'AB_CONTROL_V1' in text or (suite, task_id) != ('robosuite', 'cube_lift'):
        return receipt
    clauses = [re.sub(r'\s+', '', clause) for clause in re.split(r'[，,。.;；\n]', text)]
    lower = any(re.fullmatch(r'(?:请|并|然后|再|建议|适当)*降低抓取高度(?:一点)?', clause) for clause in clauses)
    secure = any(re.fullmatch(r'(?:请|并|然后|再)*夹紧后确认抓牢', clause) for clause in clauses)
    # A contradicting/conditional instruction in any matching clause abstains.
    uncertain = any(any(word in clause for word in ('不', '别', '勿', '是否', '如果', '可能'))
                    for clause in clauses if '降低抓取高度' in clause or '确认抓牢' in clause)
    if lower and secure and not uncertain:
        receipt.update(status='translated', rule='cube_lift_zh_lower_grasp_v1',
                       compiled='AB_CONTROL_V1 grasp_offset_m=0.005;approach_tolerance_m=0.004;close_settle_steps=30;',
                       parameters={'grasp_offset_m': 0.005, 'approach_tolerance_m': 0.004, 'close_settle_steps': 30})
    return receipt
