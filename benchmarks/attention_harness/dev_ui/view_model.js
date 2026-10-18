'use strict';
// Pure projections shared by the page and its browser-free tests.
const DevUiView = (() => {
  const resourceLabels = {
    dev_calls:'Dev 调用', advisor_calls:'Advisor 调用', eval_calls:'Eval 调用',
    provider_tokens:'模型 tokens', input_tokens:'输入 tokens', output_tokens:'输出 tokens',
    executions:'执行次数', sdk_calls:'SDK 调用', control_steps:'控制步数',
    help_credits:'帮助额度', help_count:'求助次数',
    help_wait_seconds:'等待人工/AdvisorProxy 回答的时间（秒）',
    model_seconds:'GPU/模型时间（秒）', execution_seconds:'执行时间（秒）',
    hint_human_seconds:'人工提示时间（秒）'
  };
  function resourceRows(goal) {
    const keys = [...new Set([...Object.keys(resourceLabels), ...Object.keys(goal?.used || {}),
      ...Object.keys(goal?.reserved || {}), ...Object.keys(goal?.remaining || {})])];
    return keys.map(key => ({key, label:resourceLabels[key] || key,
      used:goal?.used?.[key] ?? 0, reserved:goal?.reserved?.[key] ?? 0,
      remaining:goal?.remaining?.[key] ?? 0}));
  }
  function verificationResult(detail) {
    const value=detail.verification?.passed ?? detail.validation?.passed ??
      detail.verification_result ?? detail.validation_result ?? detail.verified ?? detail.passed;
    if (value && typeof value==='object' && typeof value.passed==='boolean') return value.passed;
    if (typeof value==='boolean') return value;
    if (value==='passed') return true;
    if (value==='failed') return false;
    return null;
  }
  function memoryRecords(events) {
    const records=[];
    for (const event of events || []) {
      const detail=event.detail || {};
      if (event.kind==='planner_memory_query_started') {
        records.push({cell:detail.cell, version:detail.version, startedAt:detail.started_at ?? event.at,
          status:'started', sources:[], authority:null, namespace:null, packetSha:null,
          seconds:null, verification:null});
        continue;
      }
      if (!['planner_memory_retrieved','planner_memory_query_finished'].includes(event.kind)) continue;
      let record=[...records].reverse().find(row=>row.cell===detail.cell && row.version===detail.version && row.status!=='finished' && row.status!=='failed');
      if (!record) {
        record={cell:detail.cell, version:detail.version, startedAt:detail.started_at ?? event.at,
          status:'started', sources:[], authority:null, namespace:null, packetSha:null,
          seconds:null, verification:null};
        records.push(record);
      }
      if (event.kind==='planner_memory_retrieved') {
        record.status='retrieved'; record.retrievedAt=event.at;
        record.sources=(detail.versions || []).map(source=>({memory_id:source.memory_id,
          version:source.version, grant_id:source.grant_id}));
        record.authority=detail.authority ?? null;record.namespace=detail.namespace ?? null;
        record.packetSha=detail.packet?.sha256?.slice(0,12) ?? null;
        const check=verificationResult(detail);
        if (check!==null) record.verification=check;
      } else {
        record.status=detail.status==='completed'?'finished':'failed';
        record.seconds=detail.actual_seconds ?? null;
        record.error=detail.error ?? null;
        const check=verificationResult(detail);
        if (check!==null) record.verification=check;
      }
    }
    return records;
  }
  function experimentFromHash(hash) {
    const match=/^#exp=(devexp-[a-f0-9]{24})$/.exec(hash || '');
    return match ? match[1] : null;
  }
  function restoredExperiment(hash, items) {
    const id=experimentFromHash(hash);
    return id && items.some(item=>item.experiment_id===id) ? id : null;
  }
  function evalSetting(checked) { return {enabled:checked}; }
  return {resourceRows,memoryRecords,experimentFromHash,restoredExperiment,evalSetting};
})();
if (typeof module!=='undefined') module.exports=DevUiView;
