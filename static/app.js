// SPDX-License-Identifier: AGPL-3.0-or-later
import {CubeSpace,COMPARISON_BANDS,comparisonBand,vsDcaText} from './cube.js';
const $=s=>document.querySelector(s), form=$('#experiment-form');
const money=n=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(n);
const pct=n=>`${n>0?'+':''}${n.toFixed(2)}%`;
const fixed=n=>Number(n).toLocaleString('en-US',{maximumFractionDigits:4});
let result=null,requestSnapshot=null,busy=false,leveraged={};
const isSeries=kind=>kind==='us_market'||kind==='us_market_3x';
// The GitHub Pages build adds this tag: there is no API there, so runs load results saved ahead of time.
const SAVED=document.querySelector('meta[name="investbell-snapshot"]')?.content;
let manifest=null;const savedRuns=new Map();
const cube=new CubeSpace($('#cube-canvas'),cell=>selectCell(cell));cube.metric=$('#color-metric').value;
$('#end').value=new Date().toLocaleDateString('en-CA',{timeZone:'America/New_York'});
const params=['dca_pct','va_pct','capture_pct'];
function outputs(){for(const [i,k] of params.entries())$(`#${['dca','va','capture'][i]}-output`).textContent=`${fixed(form.elements[k].value)}%`;}
for(const k of params)form.elements[k].addEventListener('input',outputs);
form.addEventListener('input',()=>{if(result&&!busy)$('#status').textContent='Settings changed. Run the experiment to update results.';});
function setBusy(value){busy=value;$('#run-button').disabled=value;$('#run-button span').textContent=value?(SAVED?'Loading saved results…':'Running simulations…'):(SAVED?'Show saved results':'Run experiment');$('#export-json').disabled=value||!result;$('#export-csv').disabled=value||!result;}
function makeRequest(){const data=Object.fromEntries(new FormData(form));for(const k of ['initial_cash',...params,'slippage_bps','fee'])data[k]=Number(data[k]);data.refresh=$('#refresh-data').checked;data.no_loss_sales=$('#no_loss_sales').checked;data.grid={dca:[.5,1,2,3,5],va:[0,.05,.1,.2,.5],capture:[2,5,10,20,30]};return data;}
async function fetchExperiment(payload){
  if(SAVED)return fetchSaved(payload);
  const response=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await response.json();if(!response.ok)throw Object.assign(new Error(data.error||data.message||`Request failed (${response.status})`),{status:response.status});
  return data;
}
const savedExperiment=({source,symbol})=>manifest.experiments.find(e=>e.source===source&&(source!=='yahoo'||e.symbol===symbol));
async function savedJSON(url){const response=await fetch(url);if(!response.ok)throw new Error(`Saved results are missing (${response.status}).`);return response.json();}
// Saved files hold what /api/run returns: run.json for the whole grid, one cell file per inspected setting.
async function fetchSaved(payload){
  const experiment=savedExperiment(payload);if(!experiment)throw Object.assign(new Error('This snapshot has no saved results for that choice.'),{status:400});
  const base=`${SAVED}${experiment.id}/`;
  if(!savedRuns.has(experiment.id))savedRuns.set(experiment.id,await savedJSON(`${base}run.json`));
  const {chart,...saved}=savedRuns.get(experiment.id),cell=await savedJSON(`${base}cells/${params.map(k=>payload[k]).join('_')}.json`);
  const {equity,trades}=cell.selected,first=saved.range.start;
  return {...saved,warnings:cell.warnings||saved.warnings,selected:{...cell.selected,trades_total:trades.total,
    equity:chart.dates.map((date,i)=>({date,equity:equity.equity[i],cash:equity.cash[i],benchmark:chart.benchmark[i]})),
    trades:trades.rows.map(([date,kind,shares,price,amount,fee,cash,signal])=>({date:savedDay(first,date),side:trades.kinds[kind][0],reason:trades.kinds[kind][1],shares,price,amount,fee,cash,signal_date:savedDay(first,signal)}))}};
}
// Saved trade dates are days after the first observation.
function savedDay(first,offset){if(offset==null)return null;const day=new Date(`${first}T00:00:00Z`);day.setUTCDate(day.getUTCDate()+offset);return day.toISOString().slice(0,10);}
// Sliders move to the next saved grid value in the direction of travel, so arrow keys never stick.
function snapToGrid({target:el}){const values=manifest.grid[el.name.replace('_pct','')],value=Number(el.value),previous=Number(el.dataset.saved??el.defaultValue);
  el.value=value>previous?(values.find(v=>v>=value)??values.at(-1)):(values.findLast(v=>v<=value)??values[0]);el.dataset.saved=el.value;outputs();}
function useSaved(config){
  manifest=config;const symbols=new Set(config.experiments.map(e=>e.symbol));
  for(const option of [...$('#symbol').options])if(!symbols.has(option.value))option.remove();
  for(const option of $('#source').options)option.disabled=!config.experiments.some(e=>e.source===option.value);
  for(const id of ['initial_cash','slippage_bps','fee','start','end'])$(`#${id}`).readOnly=true;
  $('#no_loss_sales').disabled=true;$('#refresh-data').closest('label').hidden=true;
  for(const k of params)form.elements[k].addEventListener('input',snapToGrid);
  for(const id of ['paper-status-heading','model-status-heading'])$(`[aria-labelledby="${id}"]`).hidden=true;
  $('#snapshot-panel').hidden=false;$('#snapshot-heading').textContent=`Saved results from ${config.generated}`;$('.local-pill').lastChild.textContent=' Saved snapshot';
}
async function run(payload,selectionOnly=false){
  if(busy)return;setBusy(true);$('#error').hidden=true;$('#status').textContent=selectionOnly?'Inspecting the selected configuration…':SAVED?'Loading saved results…':payload.source==='yahoo'?'Loading Yahoo daily opens and simulating the parameter space…':'Running a clearly labeled synthetic demonstration…';
  try{
    let data=await fetchExperiment(payload);
    if(selectionOnly&&result&&data.source.sha256!==result.source.sha256){
      // A refreshed Yahoo snapshot must not mix a new path with an old grid.
      payload={...requestSnapshot,...Object.fromEntries(params.map(k=>[k,payload[k]])),refresh:false};
      $('#status').textContent='The data snapshot changed. Recomputing every cube on the same observations…';
      data=await fetchExperiment(payload);selectionOnly=false;
    }
    if(selectionOnly&&result){result={...result,selected:data.selected,warnings:data.warnings};requestSnapshot={...requestSnapshot,...Object.fromEntries(params.map(k=>[k,payload[k]]))};}
    else{result=data;requestSnapshot=structuredClone(payload);}
    render();
  }catch(err){$('#error').textContent=err.message;$('#error').hidden=false;$('#status').textContent=result?'Request failed. Earlier results remain displayed.':'No market results loaded.';if(!SAVED&&payload.source==='yahoo'&&err.status!==400){$('#error').append(document.createTextNode(' You can retry Yahoo or explicitly choose Synthetic demo to explore the interface.'));}}
  finally{setBusy(false);}
}
form.addEventListener('submit',e=>{e.preventDefault();run(makeRequest());});
function updateInstrument(){
  const source=$('#source').value,symbol=$('#symbol').value,note=$('#instrument-note'),fund=leveraged[symbol],series=isSeries(source);
  $('#symbol').disabled=series;
  const experiment=manifest&&savedExperiment({source,symbol});if(experiment){$('#start').value=experiment.start;$('#end').value=experiment.end;}note.classList.toggle('caution',source==='us_market_3x'||(source==='yahoo'&&Boolean(fund)));
  note.textContent=source==='us_market'?'Ken French daily US market returns from July 1926, dividends included. Idle cash earns T-bill interest. Up to 20 years per run; the index ETF choice does not apply.'
    :source==='us_market_3x'?'Simulated: 3x the daily US market return above T-bills, minus 0.95%/yr, reset daily, from July 1926. No such fund existed before 2006. Idle cash earns T-bill interest. Up to 20 years per run.'
    :fund&&source==='yahoo'?`${symbol}: ${fund.leverage}x the daily ${fund.index} return, reset daily. Yahoo data starts ${fund.first_session}, a period of fast crash recoveries. Research only; paper trading uses unleveraged funds.`:'';
  note.hidden=!note.textContent;
}
$('#source').addEventListener('change',updateInstrument);$('#symbol').addEventListener('change',updateInstrument);
function selectCell(cell){if(busy||!requestSnapshot)return;for(const k of params)form.elements[k].value=form.elements[k].dataset.saved=cell[k];outputs();run({...requestSnapshot,refresh:false,...Object.fromEntries(params.map(k=>[k,cell[k]])),grid:{dca:[cell.dca_pct],va:[cell.va_pct],capture:[cell.capture_pct]}},true);}
function render(){
  const {selected,grid}=result,m=selected.metrics,p=selected.params||requestSnapshot;
  $('#scene-empty').hidden=true;$('#cube-count').textContent=grid.length;
  const source=result.source?.kind||requestSnapshot.source,series=isSeries(source),fund=leveraged[requestSnapshot.symbol];
  const label=result.source?.symbol||requestSnapshot.symbol,observation=series?'close':'open';
  $('#source-badge').textContent=source==='demo'?'SYNTHETIC DEMO · NOT MARKET DATA':series?result.source.label.toUpperCase():`${label}${fund?` · ${fund.leverage}x LEVERAGED`:''} · YAHOO DAILY OPENS`;
  $('#status').textContent=`${result.source?.label||source} · ${result.bars_count} observations · Decisions from prior ${observation}s → next-${observation} simulated fills`;
  $('#drawdown-basis').textContent=`Observed at daily ${observation}s`;
  const unsold=m.worst_unrealized_loss_pct??0;$('#unsold-loss').textContent=unsold>0?`−${unsold.toFixed(2)}%`:'0.00%';$('#unsold-loss').className=unsold>0?'negative':'';
  $('#held-sales').textContent=p.no_loss_sales===false?`${(m.losing_sales??0).toLocaleString()} losing sales · rule off`:`${(m.held_loss_sales??0).toLocaleString()} sales held by the no-loss rule`;
  $('#final-equity').textContent=money(m.final_equity);$('#total-return').textContent=pct(m.total_return_pct);$('#total-return').className=m.total_return_pct>=0?'positive':'negative';$('#vs-dca').textContent=vsDcaText(m.vs_simple_dca_pct);$('#drawdown').textContent=`${m.max_drawdown_pct.toFixed(2)}%`;$('#captures').textContent=m.capture_count;$('#trade-count').textContent=`${m.trade_count.toLocaleString()} trades`;
  $('#selected-params').textContent=`DCA ${p.dca_pct}% / day   ·   DVA ${p.va_pct}% / day   ·   Capture ${p.capture_pct}%   ·   ${label}${p.no_loss_sales===false?'   ·   no-loss rule off':''}`;
  const oldSlice=$('#slice').value;$('#slice').replaceChildren(new Option('Show all layers','all'));[...new Set(grid.map(c=>c.capture_pct))].sort((a,b)=>a-b).forEach(v=>$('#slice').add(new Option(`${v}% capture`,v)));$('#slice').value=[...$('#slice').options].some(o=>o.value===oldSlice)?oldSlice:'all';cube.slice=$('#slice').value;cube.setData(grid,p);updateLegend();renderEquity(selected.equity||[]);renderNext(selected.next_action,observation);renderTable();
  $('#warnings').replaceChildren();for(const warning of result.warnings||[]){const li=document.createElement('li');li.textContent=warning;$('#warnings').append(li);}$('#warning-count').textContent=`(${(result.warnings||[]).length})`;
}
function updateLegend(){if(!result)return;const key=$('#color-metric').value,values=result.grid.map(c=>c[key]),scale=$('#legend-scale');$('#legend-note').textContent='';scale.style.background='';scale.removeAttribute('title');
  if(key==='vs_simple_dca_pct'){const counts=COMPARISON_BANDS.map(()=>0);for(const v of values){const b=comparisonBand(v);if(b>=0)counts[b]++;}const sum=(a,b)=>counts.slice(a,b).reduce((x,y)=>x+y,0),w=100/COMPARISON_BANDS.length;
    scale.style.background=`linear-gradient(90deg,${COMPARISON_BANDS.map((b,i)=>`rgb(${b.rgb}) ${i*w}% ${(i+1)*w}%`).join(',')})`;scale.title=COMPARISON_BANDS.map((b,i)=>`${b.label}: ${counts[i]}`).join('\n');
    $('#legend-low').textContent=`${sum(0,3)} behind simple DCA`;$('#legend-high').textContent=`${sum(4,7)} ahead`;$('#legend-note').textContent=`· ${counts[3]} within ±1%`;return;}
  $('#legend-low').textContent=`${Math.min(...values).toFixed(1)}%`;$('#legend-high').textContent=`${Math.max(...values).toFixed(1)}%`;if(key==='max_drawdown_pct'&&Math.min(...values)>=0){$('#legend-low').textContent=`${Math.max(...values).toFixed(1)}%`;$('#legend-high').textContent=`${Math.min(...values).toFixed(1)}%`;}}
$('#color-metric').addEventListener('change',()=>{cube.metric=$('#color-metric').value;cube.draw();updateLegend();});$('#slice').addEventListener('change',()=>{cube.slice=$('#slice').value;cube.draw();});$('#reset-view').addEventListener('click',()=>cube.reset());$('#rotate-left').addEventListener('click',()=>{cube.yaw-=.22;cube.draw();});$('#rotate-right').addEventListener('click',()=>{cube.yaw+=.22;cube.draw();});document.addEventListener('themechange',()=>cube.draw());
function svgEl(tag,attrs={}){const el=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))el.setAttribute(k,v);return el;}
function renderEquity(rows){
  const svg=$('#equity-chart');svg.replaceChildren();if(!rows.length)return;
  const vals=rows.flatMap(r=>[r.equity,r.benchmark]).filter(Number.isFinite);let lo=Math.min(...vals),hi=Math.max(...vals);const pad=(hi-lo)*.12||100;lo-=pad;hi+=pad;
  const x=i=>62+i/Math.max(1,rows.length-1)*826,y=v=>192-(v-lo)/(hi-lo)*168;
  for(let i=0;i<4;i++){const v=lo+(hi-lo)*i/3;svg.append(svgEl('line',{x1:62,x2:888,y1:y(v),y2:y(v),class:'chart-grid','stroke-dasharray':'3 5'}));const text=svgEl('text',{x:0,y:y(v)+4,class:'chart-axis','font-size':10,'font-family':'monospace'});text.textContent=money(v);svg.append(text);}
  for(const key of ['benchmark','equity']){const points=rows.map((r,i)=>`${x(i)},${y(r[key])}`).join(' ');svg.append(svgEl('polyline',{points,fill:'none',class:`chart-${key}`,'stroke-width':key==='equity'?2.4:1.6,'stroke-linejoin':'round'}));}
  // Native SVG titles expose exact daily values without a charting dependency.
  rows.forEach((r,i)=>{const hit=svgEl('rect',{x:x(i)-2,y:15,width:Math.max(3,826/rows.length),height:183,fill:'transparent'});const title=svgEl('title');title.textContent=`${r.date}: strategy ${money(r.equity)}, cash ${money(r.cash)}, buy & hold ${money(r.benchmark)}`;hit.append(title);svg.append(hit);});
  $('#equity-range').replaceChildren();for(const d of [rows[0].date,rows.at(-1).date]){const span=document.createElement('span');span.textContent=d;$('#equity-range').append(span);}
}
function renderNext(plan,observation='open'){
  const el=$('#next-action');el.replaceChildren();if(!plan){el.textContent='No pending action.';return;}
  const title=document.createElement('strong');
  const held=Boolean(plan.sale_held_at_reference_price);
  const exitFeeBlocked=!held&&plan.capture&&plan.buy_dollars_contingent_on_exit===false;
  title.textContent=held?`${plan.capture?'Full exit':'Trim'} held by the no-loss rule${plan.action==='buy'?' · DCA purchase continues':''}`:{capture:exitFeeBlocked?'Full exit planned · sale fee may prevent it':'Full exit, then DCA reentry',buy_and_trim:'Trim excess value, then buy',trim:'Trim excess value',buy:'Scheduled DCA purchase',hold:'Hold · no funded trade at the reference price'}[plan.action]||'Next-session plan';
  el.append(title);
  const line=text=>{const span=document.createElement('span');span.style.display='block';span.textContent=text;el.append(span);};
  if(held){
    line(`At the latest price, selling ${fixed(plan.sell_shares)} shares would bring in less than they cost (average cost ${moneyPrecise(plan.average_cost)} per share, fees included). The position is kept until a sale clears that cost.`);
    if(plan.action==='buy')line(`Purchase budget: ${moneyPrecise(plan.estimated_buy_dollars)}, capped by cash at execution.`);
  }
  else if(plan.capture){
    const funded=plan.estimated_buy_dollars??0;
    if(exitFeeBlocked){
      line(`A full sale of ${fixed(plan.sell_shares)} shares is planned, but estimated proceeds cannot cover the sale fee at the reference price.`);
      if(funded>0)line(`Estimated ordinary DCA purchase: ${moneyPrecise(funded)}, using available cash and the existing cycle budget if the exit cannot execute.`);
      else line('If the exit cannot execute, retain the position. The ordinary DCA budget also needs enough cash after trading costs.');
    }else{
      line(`Sell the entire position (${fixed(plan.sell_shares)} shares), then recalculate the DCA budget from actual cash after the exit.`);
      if(funded>0)line(`Estimated same-session DCA purchase: ${moneyPrecise(funded)}, contingent on the exit and available cash.`);
      else line('Apply the DCA rule again in the same session; a purchase needs enough cash after trading costs.');
    }
  }
  else {
    if(['trim','buy_and_trim'].includes(plan.action))line(`Sell ${fixed(plan.sell_shares)} shares above the DVA target.`);
    const funded=plan.estimated_buy_dollars??plan.buy_dollars_cash_capped;
    if(['buy','buy_and_trim'].includes(plan.action))line(`Purchase budget: ${moneyPrecise(funded)}, capped by cash at execution${plan.action==='buy_and_trim'?' including estimated sale proceeds':''}.`);
    else if(plan.buy_dollars>0)line(`The scheduled ${moneyPrecise(plan.buy_dollars)} DCA budget needs available cash to execute.`);
  }
  line(`Cash available: ${moneyPrecise(plan.available_cash)} · Cycle starting value: ${moneyPrecise(plan.cycle_start_equity)}${plan.average_cost?` · Average cost: ${moneyPrecise(plan.average_cost)}`:''}`);
  line(`Observed ${observation}: ${moneyPrecise(plan.reference_open)} on ${plan.as_of}. Any fill occurs at a later ${observation}.`);
}
function renderTable(){if(!result)return;const trades=$('#table-mode').value==='trades';const head=$('#ledger-head'),body=$('#ledger-body');head.replaceChildren();body.replaceChildren();const headers=trades?['Date','Action','Reason','Shares','Fill price','Amount','Cash']:['DCA / day','DVA / day','Capture','Total return','vs simple DCA','Max drawdown','Worst unsold loss','Final value',''];const tr=document.createElement('tr');for(const h of headers){const th=document.createElement('th');th.scope='col';th.textContent=h;tr.append(th);}head.append(tr);
  const rows=trades?result.selected.trades:result.grid;const p=result.selected.params||requestSnapshot;
  for(const row of rows){const tr=document.createElement('tr');if(!trades&&params.every(k=>Number(row[k])===Number(p[k])))tr.className='selected';const values=trades?[row.date,row.side,row.reason,fixed(row.shares),moneyPrecise(row.price),moneyPrecise(row.amount),moneyPrecise(row.cash)]:[`${row.dca_pct}%`,`${row.va_pct}%`,`${row.capture_pct}%`,pct(row.total_return_pct),row.vs_simple_dca_pct==null?'—':pct(row.vs_simple_dca_pct),`${row.max_drawdown_pct.toFixed(2)}%`,row.worst_unrealized_loss_pct==null?'—':`${row.worst_unrealized_loss_pct.toFixed(2)}%`,money(row.final_equity)];for(const v of values){const td=document.createElement('td');td.textContent=v;tr.append(td);}if(!trades){const td=document.createElement('td'),button=document.createElement('button');button.textContent='Inspect ↗';button.setAttribute('aria-label',`Inspect DCA ${row.dca_pct}, DVA ${row.va_pct}, capture ${row.capture_pct}`);button.addEventListener('click',()=>selectCell(row));td.append(button);tr.append(td);}body.append(tr);}
  if(trades&&result.selected.trades_total>rows.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=headers.length;td.textContent=`Saved snapshot: the latest ${rows.length.toLocaleString()} of ${result.selected.trades_total.toLocaleString()} trades. The local lab lists every trade.`;tr.append(td);body.prepend(tr);}
  if(!rows.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=headers.length;td.textContent='No trades in this experiment.';tr.append(td);body.append(tr);}}
function moneyPrecise(n){return new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(n);}
$('#table-mode').addEventListener('change',renderTable);
function download(name,text,type){const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('#export-json').addEventListener('click',()=>{if(result)download('investbell-experiment.json',JSON.stringify({request:requestSnapshot,result},null,2),'application/json');});
$('#export-csv').addEventListener('click',()=>{if(!result)return;const mode=$('#table-mode').value,rows=mode==='trades'?result.selected.trades:result.grid;if(!rows.length)return;const keys=Object.keys(rows[0]);const quote=v=>`"${String(v??'').replaceAll('"','""')}"`;download(`investbell-${requestSnapshot.source}-${requestSnapshot.symbol}-${mode}.csv`,[keys.map(quote).join(','),...rows.map(r=>keys.map(k=>quote(r[k])).join(','))].join('\n'),'text/csv');});
async function initialize(){
  outputs();setBusy(true);
  try{const response=await fetch(SAVED?`${SAVED}manifest.json`:'/api/config');if(response.ok){const config=await response.json();$('#end').value=config.defaults.end;
    leveraged=config.leveraged_symbols||{};
    $('#leveraged-symbols').replaceChildren(...Object.entries(leveraged).map(([symbol,fund])=>new Option(`${symbol} · ${fund.leverage}x ${fund.index}`,symbol)));
    if(SAVED)useSaved(config);}}
  catch{/* The visible form remains usable if configuration cannot be loaded. */}
  finally{setBusy(false);}
  updateInstrument();run(makeRequest());
}
initialize();

async function refreshPaperStatus(){
  const heading=$('#paper-status-heading'), message=$('#paper-status-message');
  try{
    const response=await fetch('/api/paper-status',{cache:'no-store'}), data=await response.json();
    heading.textContent=data.initialized ? (data.healthy ? 'Paper runner is responding' : 'Paper runner needs attention') : (response.ok ? 'Paper account is not connected' : 'Paper status is unavailable');
    message.textContent=data.halted || data.message || (data.healthy ? `${data.symbol} · Latest heartbeat ${Math.round(data.heartbeat_age_seconds)} seconds ago. Research controls below do not change the paper account.` : 'Heartbeat is missing or stale. Check the runner before relying on execution.');
    message.classList.toggle('negative',!response.ok || Boolean(data.halted));
  }catch{
    heading.textContent='Paper status is unavailable';message.textContent='Cannot reach the account monitor. Check the local runner.';message.classList.add('negative');
  }
}
if(!SAVED){refreshPaperStatus();setInterval(refreshPaperStatus,30000);}

function modelDate(value){return new Date(value).toLocaleDateString('en-US',{year:'numeric',month:'short',day:'numeric',timeZone:'UTC'});}
async function refreshModelStatus(){
  const heading=$('#model-status-heading'),message=$('#model-status-message'),provenance=$('#model-provenance');
  const unavailable=(text='Cannot read the saved research model. Check the latest daily research run.')=>{heading.textContent='Fitted-model status is unavailable';message.textContent=text;message.classList.add('negative');};
  provenance.hidden=true;provenance.textContent='';message.classList.remove('negative');
  try{
    const response=await fetch('/api/model-status',{cache:'no-store'}),data=await response.json();
    if(!response.ok || (data.initialized&&!data.healthy)){unavailable(data.message);return;}
    if(!data.initialized){heading.textContent='No fitted model yet';message.textContent='The first successful Yahoo research run will fit DCA, DVA, and capture and save the result. Manual experiments below do not create a fitted model.';return;}
    const model=data.latest,p=model.params,due=Date.now()>=Date.parse(model.next_retrain_at);
    heading.textContent=due?'Research fit is due for retraining':'Saved research fit';
    message.textContent=`DCA ${fixed(p.dca_pct)}% / day · DVA ${fixed(p.va_pct)}% / day · Capture ${fixed(p.capture_pct)}%. Fitted ${modelDate(model.trained_at)} · Next refit ${modelDate(model.next_retrain_at)}${due?' (due at the next successful daily research run)':''}.`;
    provenance.textContent=`Latest retained fit · Yahoo ${model.data.source.symbol} · Observations through ${model.data.observed_end} · Model ${model.model_sha256.slice(0,12)}. Later historical evaluation is recorded separately from parameter selection.`;
    provenance.hidden=false;
  }catch{unavailable();}
}
if(!SAVED){refreshModelStatus();setInterval(refreshModelStatus,30000);}
