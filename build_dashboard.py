#!/usr/bin/env python3
"""
Build the Flowlamina CRM dashboard as a single self-contained index.html.

Reads data/daily_stats.json (per-day rows + a per-entry time-log list) and renders an
interactive Chart.js page into dist/index.html. The page re-buckets everything in the
browser: Day / Week / Month + any custom from-to range. Sections:
  Total  — total jobs won (all sources) + total sales calls, and the jobs-won mix.
  Upwork — applications, view rate, jobs won, connects spent, connect cost.
  Cold email — emails sent, reply rate, positive replies, jobs won.
  Other  — jobs won (referral/inbound/anything not Upwork or cold email) + sales calls.
  Delivery — total hours + hours per project.
Jobs won are attributed by the Notion Projects "Lead Source". Ratios are recomputed per
bucket from underlying counts so they stay correct at any grouping.

No build deps: Chart.js from CDN, data inlined. Encryption + publish is deploy.sh.

Usage:  python3 build_dashboard.py     # data/daily_stats.json -> dist/index.html
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "data", "daily_stats.json")
OUT_DIR = os.path.join(HERE, "dist")
OUT_PATH = os.path.join(OUT_DIR, "index.html")

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Flowlamina — Business Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  :root{
    --bg:#0f1220; --panel:#181c2e; --panel2:#1f2440; --ink:#eceefb; --muted:#9aa2c7;
    --line:#2b3157; --up:#f0932b; --view:#4a90e2; --won:#27ae60; --apps:#4a90e2;
    --mail:#8e5cf7; --reply:#e74c6a; --hours:#7f8bb5; --proj:#16a3b8; --other:#e0729e;
    --tot:#eceefb; --good:#27ae60; --bad:#e74c6a;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
  .wrap{max-width:1180px;margin:0 auto;padding:28px 20px 64px}
  header{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:8px}
  h1{font-size:26px;margin:0;letter-spacing:.2px}
  h1 .fl{color:var(--up)}
  .sub{color:var(--muted);font-size:13px}
  h2{font-size:15px;text-transform:uppercase;letter-spacing:.12em;margin:34px 0 12px;font-weight:700;
    padding-bottom:7px;border-bottom:2px solid var(--line)}
  h2.tot{color:var(--tot)} h2.up{color:var(--up)} h2.ce{color:var(--mail)}
  h2.ot{color:var(--other)} h2.dl{color:var(--proj)} h2.fin{color:#3ecf8e}
  .controls{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;
    background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px 16px;margin-top:16px}
  .controls label{font-size:12px;color:var(--muted);display:flex;gap:6px;align-items:center}
  .controls input[type=date]{background:var(--panel2);color:var(--ink);border:1px solid var(--line);
    border-radius:8px;padding:5px 8px;font-size:13px}
  .seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden}
  .seg button,.presets button{background:transparent;color:var(--muted);border:0;padding:6px 11px;font-size:12px;cursor:pointer}
  .seg button.on{background:var(--up);color:#1a1205;font-weight:600}
  .presets{display:flex;flex-wrap:wrap;gap:6px;margin-left:auto}
  .presets button{border:1px solid var(--line);border-radius:8px}
  .presets button.on{background:var(--panel2);color:var(--ink);border-color:var(--up)}
  .rangelabel{font-size:12px;color:var(--muted);margin:10px 2px 18px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:13px;margin-bottom:16px}
  .cards.big .card .val{font-size:32px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
  .card .lab{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em}
  .card .val{font-size:25px;font-weight:700;margin-top:6px}
  .card .delta{font-size:12px;margin-top:4px;color:var(--muted)}
  .delta.up{color:var(--good)} .delta.down{color:var(--bad)}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:16px}
  .chart{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
  .chart h3{margin:0 0 10px;font-size:14px;font-weight:600}
  .chart .cv{position:relative;height:220px}
  table{width:100%;border-collapse:collapse;font-size:13px;background:var(--panel);
    border:1px solid var(--line);border-radius:14px;overflow:hidden}
  th,td{padding:8px 11px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
  th:first-child,td:first-child{text-align:left}
  thead th{color:var(--muted);font-weight:600;background:var(--panel2)}
  th.grp{text-align:center;letter-spacing:.05em;text-transform:uppercase;font-size:11px}
  th.grp-up{color:var(--up)} th.grp-ce{color:var(--mail)} th.grp-ot{color:var(--other)} th.grp-fin{color:#3ecf8e}
  thead tr:nth-child(2) th{text-align:right}
  .vsep{border-right:3px solid #9aa4e0}
  tbody tr:last-child td{border-bottom:none}
  footer{margin-top:40px;color:var(--muted);font-size:12px;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1><span class="fl">Flowlamina</span> — Business Dashboard</h1>
    <div class="sub" id="asof"></div>
    <button id="refreshBtn" onclick="location.reload()"
      title="Reload the latest data (the dashboard auto-refreshes from the CRM every 15 minutes)"
      style="position:fixed;top:14px;right:16px;z-index:1000;padding:8px 15px;border-radius:9px;border:none;background:#f0932b;color:#fff;font-weight:600;font-size:13px;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.18)">🔄 Refresh</button>
  </header>

  <div class="controls">
    <div class="seg" id="gran">
      <button data-g="day">Day</button><button data-g="week">Week</button><button data-g="month" class="on">Month</button>
    </div>
    <label>From <input type="date" id="from"></label>
    <label>To <input type="date" id="to"></label>
    <div class="presets" id="presets">
      <button data-p="month">This month</button><button data-p="30">Last 30d</button>
      <button data-p="90">Last 90d</button><button data-p="ytd">YTD</button><button data-p="all" class="on">All</button>
    </div>
  </div>
  <div class="rangelabel" id="rangelabel"></div>

  <h2 class="tot">Total</h2>
  <div class="cards big" id="cards_tot"></div>
  <div class="grid">
    <div class="chart"><h3>Jobs won by source</h3><div class="cv"><canvas id="c_wonstack"></canvas></div></div>
    <div class="chart"><h3>Sales calls</h3><div class="cv"><canvas id="c_calls"></canvas></div></div>
  </div>

  <h2 class="up">Upwork</h2>
  <div class="cards" id="cards_up"></div>
  <div class="grid">
    <div class="chart"><h3>Applications</h3><div class="cv"><canvas id="c_apps"></canvas></div></div>
    <div class="chart"><h3>Connects spent</h3><div class="cv"><canvas id="c_connects"></canvas></div></div>
    <div class="chart"><h3>Proposal view rate %</h3><div class="cv"><canvas id="c_view"></canvas></div></div>
    <div class="chart"><h3>Jobs won (Upwork)</h3><div class="cv"><canvas id="c_won"></canvas></div></div>
  </div>

  <h2 class="ce">Cold email (Instantly)</h2>
  <div class="cards" id="cards_ce"></div>
  <div class="grid">
    <div class="chart"><h3>Emails sent</h3><div class="cv"><canvas id="c_emails"></canvas></div></div>
    <div class="chart"><h3>Reply rate %</h3><div class="cv"><canvas id="c_reply"></canvas></div></div>
    <div class="chart"><h3>Positive replies</h3><div class="cv"><canvas id="c_pos"></canvas></div></div>
    <div class="chart"><h3>Jobs won (cold email)</h3><div class="cv"><canvas id="c_wonce"></canvas></div></div>
  </div>

  <h2 class="ot">Other (referral / inbound / etc.)</h2>
  <div class="cards" id="cards_ot"></div>
  <div class="grid">
    <div class="chart"><h3>Jobs won (other)</h3><div class="cv"><canvas id="c_wono"></canvas></div></div>
  </div>

  <h2 class="dl">Delivery</h2>
  <div class="cards" id="cards_dl"></div>
  <div class="grid">
    <div class="chart"><h3>Hours by project</h3><div class="cv" id="cv_hproj"><canvas id="c_hproj"></canvas></div></div>
    <div class="chart"><h3>Hours logged over time</h3><div class="cv"><canvas id="c_hours"></canvas></div></div>
    <div class="chart"><h3>Hours worked over time</h3><div class="cv"><canvas id="c_worked"></canvas></div></div>
  </div>

  <h2 class="fin">Finance (business)</h2>
  <div class="cards" id="cards_fin"></div>
  <div class="grid">
    <div class="chart" style="grid-column:1/-1"><h3>Revenue vs Expenses</h3><div class="cv"><canvas id="c_finance"></canvas></div></div>
  </div>

  <h2>Breakdown by <span id="granword">month</span></h2>
  <div style="overflow-x:auto"><table id="tbl"></table></div>

  <footer>Generated __GENERATED__ · daily data, re-bucketed live · jobs won attributed by Notion Lead Source · figures are real.</footer>
</div>

<script>
const DATA = __DATA_JSON__;
const DAYS = DATA.days, TL = DATA.timelog || [];
const css = k => getComputedStyle(document.documentElement).getPropertyValue(k).trim();
const fmt = (n,d=0) => (n===null||n===undefined||isNaN(n)) ? "—"
  : Number(n).toLocaleString(undefined,{maximumFractionDigits:d});
const MIN = DAYS[0].date, MAX = DAYS[DAYS.length-1].date;
document.getElementById('asof').textContent = "Data through " + MAX + " · updated " + DATA.generated;

const state = {from: MIN, to: MAX, gran: 'month'};
for(const id of ['from','to']){ const e=document.getElementById(id); e.min=MIN; e.max=MAX; }
document.getElementById('from').value = MIN; document.getElementById('to').value = MAX;

// ---- bucketing ----
function mondayOf(iso){ const [y,m,d]=iso.split('-').map(Number); const dt=new Date(y,m-1,d);
  dt.setDate(dt.getDate()-((dt.getDay()+6)%7)); return dt.toISOString().slice(0,10); }
function bucketKey(iso){ return state.gran==='day'?iso : state.gran==='week'?mondayOf(iso) : iso.slice(0,7); }
const SUM_FIELDS = ['applications','viewed','connects','connect_cost','proposals_drafted','proposals_signed',
  'value_signed','won_upwork','won_coldemail','won_other','sales_calls','hours','worked','uw_replies','emails','new_leads','replies','positive',
  'revenue','expenses'];
function withRatios(o){
  o.view_rate  = o.applications ? o.viewed*100/o.applications : 0;
  o.uw_reply_rate = o.applications ? o.uw_replies*100/o.applications : 0;
  o.reply_rate = o.emails ? o.replies*100/o.emails : 0;
  o.total_won  = (o.won_upwork||0)+(o.won_coldemail||0)+(o.won_other||0);
  o.net        = (o.revenue||0)-(o.expenses||0);
  return o;
}
function aggregate(fromISO,toISO){
  const buckets=new Map();
  for(const row of DAYS){
    if(row.date<fromISO||row.date>toISO) continue;
    const k=bucketKey(row.date);
    if(!buckets.has(k)){ const z={key:k}; SUM_FIELDS.forEach(f=>z[f]=0); buckets.set(k,z); }
    const b=buckets.get(k); SUM_FIELDS.forEach(f=>b[f]+=(row[f]||0));
  }
  const arr=[...buckets.values()].sort((a,b)=>a.key<b.key?-1:1); arr.forEach(withRatios); return arr;
}
function totals(arr){ const t={}; SUM_FIELDS.forEach(f=>t[f]=arr.reduce((s,b)=>s+b[f],0)); return withRatios(t); }
function hoursByProject(fromISO,toISO){
  const m={}; for(const t of TL){ if(t.date<fromISO||t.date>toISO) continue; m[t.project]=(m[t.project]||0)+t.hours; }
  const arr=Object.entries(m).sort((a,b)=>b[1]-a[1]);
  return {labels:arr.map(x=>x[0]), data:arr.map(x=>Math.round(x[1]*10)/10)};
}
const MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function labelFor(key){
  if(state.gran==='month'){ const [y,m]=key.split('-'); return MON[+m-1]+" "+y.slice(2); }
  if(state.gran==='week'){ const [,m,d]=key.split('-'); return MON[+m-1]+" "+d; }
  return key.slice(5);
}

// ---- charts ----
Chart.defaults.color=css('--muted'); Chart.defaults.font.family=getComputedStyle(document.body).fontFamily;
Chart.defaults.borderColor=css('--line');
const charts={};
const XOPTS={scales:{y:{beginAtZero:true,grid:{color:css('--line')}},x:{grid:{display:false},ticks:{autoSkip:true,maxTicksLimit:12}}},plugins:{legend:{display:false}},responsive:true,maintainAspectRatio:false};
function mkBar(id,color){ charts[id]=new Chart(document.getElementById(id),{type:'bar',
  data:{labels:[],datasets:[{data:[],backgroundColor:css(color),borderRadius:6,maxBarThickness:46}]},options:XOPTS}); }
function mkLine(id,color){ charts[id]=new Chart(document.getElementById(id),{type:'line',
  data:{labels:[],datasets:[{data:[],borderColor:css(color),backgroundColor:css(color)+"33",fill:true,tension:.3,pointRadius:3,borderWidth:2}]},options:XOPTS}); }
mkBar('c_apps','--apps'); mkBar('c_connects','--up'); mkLine('c_view','--view'); mkBar('c_won','--won');
mkBar('c_emails','--mail'); mkLine('c_reply','--reply'); mkBar('c_pos','--won'); mkBar('c_wonce','--mail');
mkBar('c_wono','--other'); mkBar('c_calls','--view'); mkBar('c_hours','--hours'); mkBar('c_worked','--proj');
// Revenue vs Expenses — grouped bars in one chart
charts['c_finance']=new Chart(document.getElementById('c_finance'),{type:'bar',
  data:{labels:[],datasets:[
    {label:'Revenue',data:[],backgroundColor:css('--won'),borderRadius:4,maxBarThickness:40},
    {label:'Expenses',data:[],backgroundColor:css('--reply'),borderRadius:4,maxBarThickness:40}]},
  options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:true,position:'bottom',labels:{boxWidth:12,padding:12}}},
    scales:{x:{grid:{display:false}},y:{beginAtZero:true,grid:{color:css('--line')}}}}});
// stacked jobs won by source
charts['c_wonstack']=new Chart(document.getElementById('c_wonstack'),{type:'bar',
  data:{labels:[],datasets:[
    {label:'Upwork',data:[],backgroundColor:css('--up'),stack:'s',borderRadius:3,maxBarThickness:46},
    {label:'Cold email',data:[],backgroundColor:css('--mail'),stack:'s',borderRadius:3,maxBarThickness:46},
    {label:'Other',data:[],backgroundColor:css('--other'),stack:'s',borderRadius:3,maxBarThickness:46}]},
  options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:true,position:'bottom',labels:{boxWidth:12,padding:12}}},
    scales:{x:{stacked:true,grid:{display:false}},y:{stacked:true,beginAtZero:true,grid:{color:css('--line')}}}}});
// horizontal bar: hours by project
charts['c_hproj']=new Chart(document.getElementById('c_hproj'),{type:'bar',
  data:{labels:[],datasets:[{data:[],backgroundColor:css('--proj'),borderRadius:5,maxBarThickness:26}]},
  options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},
    scales:{x:{beginAtZero:true,grid:{color:css('--line')}},y:{grid:{display:false}}}}});
const CHART_FIELD={c_apps:'applications',c_connects:'connects',c_view:'view_rate',c_won:'won_upwork',
  c_emails:'emails',c_reply:'reply_rate',c_pos:'positive',c_wonce:'won_coldemail',
  c_wono:'won_other',c_calls:'sales_calls',c_hours:'hours',c_worked:'worked'};
function paint(id,labels,arr){ const c=charts[id];
  c.data.labels=labels; c.data.datasets[0].data=arr.map(b=>Math.round(b[CHART_FIELD[id]]*100)/100); c.update(); }
function paintStack(labels,arr){ const c=charts['c_wonstack']; c.data.labels=labels;
  c.data.datasets[0].data=arr.map(b=>b.won_upwork); c.data.datasets[1].data=arr.map(b=>b.won_coldemail);
  c.data.datasets[2].data=arr.map(b=>b.won_other); c.update(); }
function paintFinance(labels,arr){ const c=charts['c_finance']; c.data.labels=labels;
  c.data.datasets[0].data=arr.map(b=>Math.round(b.revenue)); c.data.datasets[1].data=arr.map(b=>Math.round(b.expenses)); c.update(); }

// ---- KPI cards ----
const KPI_TOT=[{lab:'Total jobs won',k:'total_won',d:0},{lab:'Total sales calls',k:'sales_calls',d:0}];
const KPI_UP=[{lab:'Applications',k:'applications',d:0},{lab:'View rate',k:'view_rate',d:1,suf:'%'},
  {lab:'Replies',k:'uw_replies',d:0},{lab:'Reply rate',k:'uw_reply_rate',d:1,suf:'%'},
  {lab:'Jobs won',k:'won_upwork',d:0},{lab:'Connects spent',k:'connects',d:0},{lab:'Connect cost',k:'connect_cost',d:0,pre:'$'}];
const KPI_CE=[{lab:'Emails sent',k:'emails',d:0},{lab:'Reply rate',k:'reply_rate',d:2,suf:'%'},
  {lab:'Positive replies',k:'positive',d:0},{lab:'Jobs won',k:'won_coldemail',d:0}];
const KPI_OT=[{lab:'Jobs won',k:'won_other',d:0}];
const KPI_DL=[{lab:'Hours logged (projects)',k:'hours',d:1},{lab:'Hours worked (total)',k:'worked',d:1}];
const KPI_FIN=[{lab:'Revenue',k:'revenue',d:0,pre:'$'},{lab:'Expenses',k:'expenses',d:0,pre:'$'},{lab:'Net',k:'net',d:0,pre:'$'}];
function daysBetween(a,b){ return Math.round((new Date(b)-new Date(a))/86400000); }
function shiftISO(iso,n){ const d=new Date(iso); d.setDate(d.getDate()+n); return d.toISOString().slice(0,10); }
function renderCards(elId,kpis,cur,prev){
  const el=document.getElementById(elId); el.innerHTML='';
  kpis.forEach(k=>{ const v=cur[k.k]; let delta='';
    if(prev && prev[k.k]!==undefined){ const diff=v-prev[k.k];
      if(Math.abs(diff)>(k.suf==='%'?0.05:0.5))
        delta=`<div class="delta ${diff>0?'up':'down'}">${diff>0?'▲':'▼'} ${fmt(Math.abs(diff),k.d)}${k.suf||''} vs prev</div>`; }
    el.insertAdjacentHTML('beforeend',
      `<div class="card"><div class="lab">${k.lab}</div><div class="val">${(k.pre||'')}${fmt(v,k.d)}${(k.suf||'')}</div>${delta}</div>`);
  });
}

// ---- table ----
const GROUPS=[
  {label:'', cols:[['Period','_label']]},
  {label:'Upwork', cls:'grp-up', cols:[['Apps','applications'],['View %','view_rate'],['Reply %','uw_reply_rate'],
    ['Won','won_upwork'],['Connects','connects'],['Cost $','connect_cost']]},
  {label:'Cold email', cls:'grp-ce', cols:[['Won','won_coldemail'],['Emails','emails'],['Reply %','reply_rate'],
    ['Positive','positive']]},
  {label:'Other', cls:'grp-ot', cols:[['Won','won_other'],['Calls','sales_calls']]},
  {label:'Finance', cls:'grp-fin', cols:[['Rev $','revenue'],['Exp $','expenses']]},
  {label:'', cols:[['Hours Logged','hours'],['Hours Worked','worked']]},
];
const FLAT=GROUPS.flatMap(g=>g.cols);
// column indices that get a right divider (last col of each group except the final one)
const SEP=new Set(); { let acc=0; GROUPS.forEach((g,gi)=>{ acc+=g.cols.length; if(gi<GROUPS.length-1) SEP.add(acc-1); }); }
function renderTable(arr){
  const t=document.getElementById('tbl');
  let r1='', r2='';
  GROUPS.forEach((g,gi)=>{
    const sep = gi<GROUPS.length-1 ? ' vsep' : '';
    if(!g.label){ g.cols.forEach(c=> r1+=`<th rowspan="2" class="${sep.trim()}">${c[0]}</th>`); }
    else { r1+=`<th class="grp ${g.cls}${sep}" colspan="${g.cols.length}">${g.label}</th>`;
           g.cols.forEach((c,ci)=> r2+=`<th class="${ci===g.cols.length-1?'vsep':''}">${c[0]}</th>`); }
  });
  t.innerHTML=`<thead><tr>${r1}</tr><tr>${r2}</tr></thead>`;
  const tb=document.createElement('tbody');
  [...arr].reverse().forEach(b=>{ tb.insertAdjacentHTML('beforeend',"<tr>"+FLAT.map((c,i)=>{
    const sc=SEP.has(i)?' class="vsep"':'';
    if(c[1]==='_label') return `<td${sc}>${labelFor(b.key)}</td>`;
    const dec=c[0].includes('%')?2:(c[1]==='hours'||c[1]==='worked'?1:0);
    return `<td${sc}>${fmt(b[c[1]],dec)}</td>`;}).join('')+"</tr>"); });
  t.appendChild(tb);
}

// ---- main render ----
function render(){
  if(state.from>state.to){ const s=state.from; state.from=state.to; state.to=s;
    document.getElementById('from').value=state.from; document.getElementById('to').value=state.to; }
  const arr=aggregate(state.from,state.to), labels=arr.map(b=>labelFor(b.key));
  ['c_apps','c_connects','c_view','c_won','c_emails','c_reply','c_pos','c_wonce','c_wono','c_calls','c_hours','c_worked']
    .forEach(id=>paint(id,labels,arr));
  paintStack(labels,arr); paintFinance(labels,arr);
  const hp=hoursByProject(state.from,state.to);
  document.getElementById('cv_hproj').style.height=Math.max(200,hp.labels.length*28+30)+'px';
  charts['c_hproj'].data.labels=hp.labels; charts['c_hproj'].data.datasets[0].data=hp.data; charts['c_hproj'].update();
  const span=daysBetween(state.from,state.to), prevTo=shiftISO(state.from,-1), prevFrom=shiftISO(prevTo,-span);
  const prev=(prevTo>=MIN)?totals(aggregate(prevFrom>MIN?prevFrom:MIN,prevTo)):null;
  const cur=totals(arr);
  renderCards('cards_tot',KPI_TOT,cur,prev); renderCards('cards_up',KPI_UP,cur,prev);
  renderCards('cards_ce',KPI_CE,cur,prev); renderCards('cards_ot',KPI_OT,cur,prev); renderCards('cards_dl',KPI_DL,cur,prev);
  renderCards('cards_fin',KPI_FIN,cur,prev);
  renderTable(arr);
  document.getElementById('granword').textContent=state.gran;
  document.getElementById('rangelabel').textContent=
    `Showing ${state.from} → ${state.to} · grouped by ${state.gran} · ${arr.length} ${state.gran==='day'?'days':state.gran+'s'}`;
}

// ---- controls ----
document.getElementById('gran').addEventListener('click',e=>{ const g=e.target.dataset.g; if(!g)return;
  state.gran=g; [...e.currentTarget.children].forEach(b=>b.classList.toggle('on',b.dataset.g===g)); render(); });
function setRange(from,to){ state.from=from; state.to=to;
  document.getElementById('from').value=from; document.getElementById('to').value=to; render(); }
function clearPreset(){ [...document.getElementById('presets').children].forEach(b=>b.classList.remove('on')); }
['change','input'].forEach(ev=>{
  document.getElementById('from').addEventListener(ev,e=>{if(!e.target.value)return;state.from=e.target.value;clearPreset();render();});
  document.getElementById('to').addEventListener(ev,e=>{if(!e.target.value)return;state.to=e.target.value;clearPreset();render();});
});
document.getElementById('presets').addEventListener('click',e=>{ const p=e.target.dataset.p; if(!p)return;
  clearPreset(); e.target.classList.add('on');
  if(p==='all') setRange(MIN,MAX);
  else if(p==='month') setRange(MAX.slice(0,7)+'-01',MAX);
  else if(p==='ytd') setRange(MAX.slice(0,4)+'-01-01',MAX);
  else setRange(shiftISO(MAX,-(+p-1)),MAX); });

render();
</script>
</body>
</html>
"""


def main():
    with open(DATA_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    html = TEMPLATE.replace("__DATA_JSON__", json.dumps(data)) \
                   .replace("__GENERATED__", data.get("generated", ""))
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"Wrote {OUT_PATH} ({len(html):,} bytes, {len(data['days'])} days, {len(data.get('timelog',[]))} time-log entries)")


if __name__ == "__main__":
    main()
