'use strict';
// Pure presentation logic: no credentials, network requests, or device actions.
((root) => {
  const reasons={upstream_authentication_failed:'Influx rejected the sign-in. Check the read-only account.',
    upstream_connection_error:'The history server could not be reached.',upstream_timeout:'The history server took too long to respond.',
    upstream_http_error:'The history server refused a request.',upstream_query_error:'The history server could not answer a query.',
    query_budget:'This import reached its query limit. Saved ranges can be reused on the next attempt.',
    source_disk_budget:'The history storage limit was reached. A retry alone will not increase it.',
    scope_or_campaign_changed:'The selected devices or test campaign changed during the import.',
    ambiguous_series:'More than one historical series matched a device. Its mapping needs investigation.',
    worker_interrupted:'The worker restarted before the import finished.'};
  function describe(data){
    const s=data.shadow||{}, a=data.archive||{}, reports=Object.values(s.reports||{}), status=data.status||{};
    const passed=reports.filter(r=>r.state==='beats_baselines').length;
    let title='Waiting for a learning test',message='Connect and start a test to collect observations and compare predictions.',action='Test settings';
    if(s.state==='running'){
      title=s.model_targets?'Testing predictions':'Collecting examples';
      message=passed?`${passed} prediction ${passed===1?'model':'models'} passed the historical checks. Preferences still need review; device control stays off.`:
        'The system is learning and testing. No model has passed every quality check yet. It is not controlling your home.';
      action='Review test settings';
    }else if(s.state==='completed'){
      title='Learning test finished';message='Your history and model results are saved. Review the results before starting another test.';
    }else if(s.state==='stopped'){
      title='Learning test stopped';message='The test is stopped. Saved history and results are preserved.';
    }
    let next='No setup action is required for the running test. You can review forecasts below.';
    let destination='settings';
    if(a.state==='partial'||a.state==='interrupted'){
      next=(reasons[a.error_code]||'The history import did not finish.')+' Saved pages are preserved. Re-enter your history connection to resume.';
      action='Resume history import';destination='history';
    }else if(a.state==='importing'){
      next='History is being imported. Keep the worker running; an update interrupts this import.';
      action='View import progress';destination='history';
    }else if(a.state==='not_configured'){
      next='No history import is active. Previously saved history may already be in use. Connect the archive to check and import unfinished ranges.';
      action='Connect / resume history';destination='history';
    }
    if(status.state==='paused'||!data.scope?.length){title='No rooms selected';message='Choose allowed rooms and devices in Home Assistant’s DwellMind options. Saved history is preserved.';}
    if(s.error){title='Learning needs attention';message=s.error;}
    const rooms=(data.scope||[]).map(room=>({name:room.name,devices:room.entities.length,
      unavailable:room.entities.filter(e=>data.observations?.[e]?.availability!=='reported').length,
      targets:Object.entries(s.reports||{}).filter(([id])=>room.entities.includes(id.split('|')[0])).length}));
    return {title,message,next,action,destination,passed,rooms,
      progress:s.evaluated?`${s.evaluated} forecasts checked; ${s.matched_reported_outcomes} agreed with reported device states. Unchanged states are easy to predict; this does not prove useful control.`:
        'No forecast outcomes checked yet. Results appear after the next observation window.',
      importMessage:a.state==='completed'?'History download finished. This does not mean every device has enough useful training data.':
        a.state==='importing'?'Importing saved history. New and reused row counts below show download progress.':
        a.state==='partial'||a.state==='interrupted'?(reasons[a.error_code]||'History import paused.')+' Existing pages remain saved.':
        'No active history import. Existing saved data is retained.'};
  }
  if(typeof module==='object'&&module.exports)module.exports=describe;
  else root.DwellMindOverview=describe;
})(globalThis);
