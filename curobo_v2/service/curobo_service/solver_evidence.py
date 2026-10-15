"""Observe V2 solver outcomes without changing their inputs or outputs."""
import json
import logging
import math
import torch


def summarize(result):
    if result is None:return {'returned':None}
    row={}
    for name in ['success','feasible','position_error','rotation_error','cspace_error',
                 'solve_time','total_time','position_tolerance','orientation_tolerance']:
        value=getattr(result,name,None)
        if isinstance(value,torch.Tensor):value=value.detach().cpu().flatten()[:64].tolist()
        def finite(item):
            if isinstance(item,list):return [finite(x)for x in item]
            return str(item)if isinstance(item,float)and not math.isfinite(item)else item
        if value is not None:row[name]=finite(value)
    state=getattr(result,'js_solution',None)
    if state is not None:
        row['joint_names']=state.joint_names
        row['solution_qpos']=finite(state.position.detach().cpu().tolist())
    for name in ['metrics','interpolated_metrics']:
        metrics=getattr(result,name,None)
        if metrics is not None:
            collection=metrics.costs_and_constraints
            row[name]={}
            for group in ['constraints','hybrid_costs_constraints']:
                values=getattr(collection,group)
                row[name][group]={key:finite(float(value.detach().max().cpu()))
                    for key,value in zip(values.names,values.values)}
    return row
