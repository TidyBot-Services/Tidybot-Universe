"""Validate both suites' dedicated Service process-group cleanup receipts."""

from __future__ import annotations

from typing import Any


def service_stop_confirmed(stop: Any, suite: str) -> bool:
    if not isinstance(stop, dict):
        return False
    if suite in {"robosuite", "robocasa365"}:
        return stop.get("leader_reaped") is True and stop.get("process_group_gone") is True
    if suite == "robocasa":
        if stop.get('mechanism') == 'authenticated_v2_native_process_group':
            if (stop.get('robot')or{}).get('execution_arm')=='both':
                from openarm_bimanual.lifecycle import stopped_components
                barrier=stop.get('stop_barrier')or{}
                state=barrier.get('stopped_state')or{}
                planners=stop.get('planner_process_groups')
                return bool(stop.get('physical_rest_confirmed')is True
                    and barrier.get('physical_rest_confirmed')is True
                    and state.get('active_action')is None
                    and all(stopped_components(state).values())
                    and stop.get('reaped')is True and stop.get('process_group_gone')is True
                    and isinstance(planners,list) and planners
                    and all(p.get('confirmed')is True and p.get('reaped')is True and p.get('process_group_gone')is True for p in planners))
            import math
            barrier=stop.get('stop_barrier') or {}
            q=barrier.get('qpos');v=barrier.get('qvel');goal=barrier.get('drive_target')
            names=barrier.get('joint_order');robot=stop.get('robot') or {}
            planners=stop.get('planner_process_groups')
            if (stop.get('reaped') is not True or stop.get('process_group_gone') is not True
                    or not isinstance(planners,list) or not planners
                    or not all(isinstance(p,dict) and p.get('confirmed') is True
                        and p.get('reaped') is True and p.get('process_group_gone') is True for p in planners)
                    or barrier.get('active_action') is not None
                    or robot.get('model')!='OpenArm' or robot.get('execution_arm') not in {'left','right'}
                    or not isinstance(names,list) or set(names)!={f'openarm_{a}_joint{i}' for a in ['left','right'] for i in range(1,8)}|{f'openarm_{a}_finger_joint{i}' for a in ['left','right'] for i in [1,2]} or len(names)!=18
                    or not all(isinstance(a,list) and len(a)==18 for a in [q,v,goal])):return False
            try:
                if not all(math.isfinite(float(x)) for a in [q,v,goal] for x in a):return False
                return all(abs(v[i]) <= (.005 if 'finger' in n else .05)
                           and abs(q[i]-goal[i]) <= (.0005 if 'finger' in n else .01)
                           for i,n in enumerate(names))
            except (ValueError,TypeError):return False
        services = stop.get("services")
        if not isinstance(services, dict) or not services:
            return False
        expected = {"simulator", "agent"}
        if stop.get("mechanism") == "dedicated_openarm_kitchen_process_group":
            robot = stop.get("robot", {})
            if robot.get("model") != "OpenArm" or robot.get("execution_arm") not in {"left", "right", "both"}:
                return False
            if robot.get('execution_arm')=='both':
                from openarm_bimanual.lifecycle import stopped_components
                barrier=stop.get('stop_barrier') or {}
                measured=stopped_components(barrier.get('stopped_state') or {})
                if (barrier.get('physical_rest_confirmed') is not True
                    or barrier.get('stopped_components')!={'left':True,'right':True,'base':True}
                    or not all(measured.values())):return False
            expected = {"openarm"}
        if set(services) != expected and not (
                stop.get("reason") == "operator_cancel" and set(services).issubset(expected)):
            return False
        return all(
            isinstance(services.get(name), dict)
            and services[name].get("leader_reaped") is True
            and services[name].get("process_group_gone") is True
            for name in services
        )
    return False
