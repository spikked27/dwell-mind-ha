'use strict';
((root) => {
  const reasons={upstream_authentication_failed:'Influx rejected the sign-in. Check the read-only account.',
    upstream_connection_error:'The history server could not be reached.',upstream_timeout:'The history server took too long to respond.',
    upstream_http_error:'The history server refused a request.',upstream_query_error:'The history server could not answer a query.',
    query_budget:'This import reached its query limit. Saved ranges can be reused on the next attempt.',
    source_disk_budget:'The history storage limit was reached. A retry alone will not increase it.',
    scope_or_campaign_changed:'The selected devices or test campaign changed during the import.',
    ambiguous_series:'More than one historical series matched a device. Its mapping needs investigation.',
    worker_interrupted:'The worker restarted before the import finished.'};

  const definitions=[['Lighting','light','Lighting choices'],['Climate','climate','Temperature and modes'],['Curtains','cover','Open and close positions'],['Ventilation','fan','Fan use and speed']];
  const checkNames={improves_best_baseline_by_five_percent:'Makes fewer forecast errors than all simple alternatives',
    recognizes_states_as_well_as_persistence:'Recognizes both states at least as well as leaving things unchanged',
    holdout_has_multiple_states:'Test history includes different states',history_spans_seven_days:'Usable history spans at least seven days',
    holdout_spans_one_day:'Later test history spans at least one day'};
  const failedChecks={improves_best_baseline_by_five_percent:'forecast errors are not at least 5% lower than the best simple alternative',
    recognizes_states_as_well_as_persistence:'it misses states more often than assuming no change',holdout_has_multiple_states:'the test includes only one state',
    history_spans_seven_days:'less than seven days of usable history',holdout_spans_one_day:'the later test covers less than one day'};
  function explainReport(id,r){
    const checks=r.quality_checks||{},loss=r.held_out?.learned?.brier;
    const baselines=['persistence','prevalence','hourly'].map(k=>r.held_out?.[k]?.brier).filter(Number.isFinite);
    const best=baselines.length===3?Math.min(...baselines):null;
    const comparison=Number.isFinite(loss)&&best!==null&&best>0?100*(best-loss)/best:null;
    let reason='Not enough usable, continuous history to fit this task.';
    if(r.state==='insufficient_current_home_history')reason=(r.current_home_examples||0)+' usable examples from this home; '+(r.required_current_home_examples||300)+' are required. Imported readings are not all usable examples.';
    else if(r.state==='insufficient_variation')reason='Too little variation to learn when a different state is appropriate.';
    else if(r.state==='unseen_outcome')reason='The later test contains a state missing from the training period.';
    else if(r.state==='incompatible_temperature_units')reason='Historical temperature units do not agree.';
    else if(r.state==='excluded_lineage')reason='This older model uses inputs outside your current allowed scope. It is blocked.';
    else if(r.state==='beats_baselines')reason='Passed behavior checks. It predicts reported states; it still does not establish what you want.';
    else if(r.state==='experimental'){
      const failed=Object.entries(checks).filter(([,pass])=>pass===false).map(([key])=>failedChecks[key]||key);
      reason=failed.length?'Not ready: '+failed.join('; ')+'.':comparison!==null&&comparison<5?'It does not reduce forecast error enough compared with the best simple alternative.':'It has not passed every quality check. More data alone may not fix this.';
      if(r.held_out?.learned?.balanced_accuracy<r.held_out?.persistence?.balanced_accuracy)reason+=' It is worse at recognizing the different states than assuming no change.';
    }
    return {id,state:r.state,reason,comparison,examples:r.current_home_examples??r.examples??null,
      checks:Object.entries(checkNames).map(([key,name])=>({name,passed:typeof checks[key]==='boolean'?checks[key]:null})),scores:r.held_out||{}};
  }
  function describe(data){
    const s=data.shadow||{},a=data.archive||{},status=data.status||{},reports=s.reports||{},prefs=s.preference_reports||{};
    const passed=Object.values(reports).filter(r=>r.state==='beats_baselines').length;
    const diagnostics=Object.entries(reports).map(([id,r])=>explainReport(id,r));
    const roomViews=(data.scope||[]).map(room=>{
      const capabilities=definitions.map(([name,domain,purpose])=>{
        const selected=room.entities.filter(e=>e.startsWith(domain+'.'));
        const records=Object.entries(reports).filter(([id])=>selected.includes(id.split('|')[0]));
        const tested=records.filter(([,r])=>r.state==='beats_baselines').length;
        const preferences=Object.entries(prefs).filter(([id,r])=>selected.includes(id.split('|')[0])&&r.state==='validated_preference_candidate').length;
        let stage='Not selected',explanation='Select allowed devices in Home Assistant to include this capability.';
        if(selected.length){
          stage='Collecting useful history';explanation='Devices are selected, but there is not enough usable evidence for a validated pattern.';
          if(records.some(([,r])=>r.state==='experimental')){stage='Not yet reliable';explanation='A candidate was trained, but it has not passed all comparisons with simpler predictions.';}
          if(tested){stage='Behavior pattern validated';explanation='A reported-device behavior pattern passed historical checks. Your preferred action is not established.';}
          if(preferences){stage='Preference candidate';explanation='Explicit reviews support a preference candidate. It still does not control devices.';}
        }
        return {name,purpose,stage,explanation,selected:selected.length,tested,preferences};
      });
      return {name:room.name,devices:room.entities.length,unavailable:room.entities.filter(e=>data.observations?.[e]?.availability!=='reported').length,
        targets:Object.entries(reports).filter(([id])=>room.entities.includes(id.split('|')[0])).length,capabilities};
    });
    const reviewable=[],teachable=[],seen=new Set();
    for(const d of s.decisions||[]){
      const identity=d.target+'|'+d.channel;
      if(seen.has(identity))continue;
      seen.add(identity);
      // Learning questions are explicitly separate from validated action candidates.
      // No sensor, unavailable, assumed or off-limit target can become a question.
      const allowed=(data.scope||[]).some(room=>room.entities.includes(d.target));
      const observation=data.observations?.[d.target];
      if(allowed&&definitions.some(([,domain])=>d.target.startsWith(domain+'.'))&&observation?.availability==='reported'&&!observation.assumed_state&&
        !d.human_review&&teachable.length<6&&!(d.blocked_by||[]).some(v=>!['model_has_not_beaten_baselines','low_forecast_probability','preference_not_established','execution_not_implemented','manual_priority_hold','manual_or_unattributed_change_hold','setting_requires_a_separately_justified_light_on_action'].includes(v)))teachable.push(d.decision_id);
      if(!allowed||!d.would_change||!definitions.some(([,domain])=>d.target.startsWith(domain+'.'))||d.current===d.predicted||d.model_state!=='beats_baselines'||reports[identity]?.state!=='beats_baselines')continue;
      if((d.blocked_by||[]).some(reason=>!['preference_not_established','execution_not_implemented'].includes(reason)))continue;
      reviewable.push(d.decision_id);
    }
    let title='Not ready for automatic decisions',message='DwellMind can observe and test reported device behavior. It cannot control your home yet.';
    if(!data.scope?.length){title='Choose the rooms you want to include';message='Use Home Assistant’s DwellMind options to select allowed rooms and devices. Off-limit items remain excluded.';}
    else if(s.state==='stopped'||s.state==='completed'){title='Learning test '+(s.state==='stopped'?'stopped':'finished');message='Saved history and results are preserved. Review them before starting another test.';}
    else if(s.training){title='Training is in progress';message='The worker is testing candidates. This is not evidence that it has learned the actions you want.';}
    else if(reviewable.length){title='Changes are ready for your review';message='These candidates passed behavior checks. Review whether you would want them; nothing is sent to your devices.';}
    else if(!passed){message='No candidate has passed every quality check. More collected data is not, by itself, proof of better decisions.';}
    if(s.error){title='Learning needs attention';message=s.error;}
    let next='Inspect your rooms to see what is learning and what is blocking useful decisions.',action='Review my rooms',destination='rooms';
    if(a.state==='partial'||a.state==='interrupted'){next=(reasons[a.error_code]||'The history import did not finish.')+' Saved pages are preserved. Resume unfinished ranges.';action='Resume history';destination='history';}
    else if(a.state==='importing'){next='History import is running. Keep the worker running until it finishes.';action='View history progress';destination='history';}
    else if(reviewable.length){next='Review a proposed change with its recorded context. Your answer teaches a preference; it does not execute an action.';action='Review changes';destination='review';}
    else if(a.state==='not_configured'){next='No import is active. Previously saved history may already be in use; its completeness is not confirmed here.';action='Check history connection';destination='history';}
    const pipeline=[{name:'Observe',value:status.state==='capturing'?'Receiving live data':'Not capturing',detail:'Selected devices only. Gaps remain unknown.'},
      {name:'Use history',value:a.state==='completed'?'Download complete':(a.state||'Not connected').replaceAll('_',' '),detail:'Saved source readings and the bounded training sample are different.'},
      {name:'Test patterns',value:passed+' passed / '+Object.keys(reports).length+' tasks',detail:'A pattern must improve on simpler alternatives in later history.'},
      {name:'Learn preferences',value:(s.human_reviews||0)+' reviewed moments',detail:(s.preference_labels||0)+' wanted-state labels. Automation behavior is never a preference label.'},
      {name:'Control devices',value:'Not available',detail:'The execution component is not implemented. No actions are sent.'}];
    return {title,message,next,action,destination,passed,rooms:roomViews,reviewable,teachable,diagnostics,pipeline,
      progress:passed?passed+' behavior patterns passed historical checks. This is separate from learning your preferences.':
        'Useful patterns confirmed: none. Routine device-state matches are available only in technical details.',
      importMessage:a.state==='completed'?'History download finished. This does not mean every device has enough useful training data.':
        a.state==='importing'?'Importing saved history; new and reused row counts describe download progress.':
        a.state==='partial'||a.state==='interrupted'?(reasons[a.error_code]||'History import paused.')+' Existing pages remain saved.':
        'No active history import. Existing saved data is retained.'};
  }
  if(typeof module==='object'&&module.exports)module.exports=describe;
  else root.DwellMindOverview=describe;
})(globalThis);
