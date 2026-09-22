"""Single-page human ATC console. Plain HTML + fetch; the domain stays authoritative."""

CONSOLE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AERIS - Human ATC console</title>
<style>
  body { font-family: ui-monospace, Menlo, Consolas, monospace; margin: 1.5rem; background: #0e1116; color: #d8dee9; }
  h1 { font-size: 1.1rem; margin: 0 0 1rem; }
  h2 { font-size: .9rem; color: #88c0d0; margin: 1rem 0 .3rem; text-transform: uppercase; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  .panel { border: 1px solid #2e3440; padding: .6rem .8rem; border-radius: 4px; }
  pre { white-space: pre-wrap; margin: 0; font-size: .78rem; }
  button { margin: .2rem; padding: .4rem .8rem; font-family: inherit; }
  button:disabled { opacity: .4; }
  .why { font-size: .75rem; color: #ebcb8b; margin-left: .4rem; }
  select, input { font-family: inherit; }
</style>
</head>
<body>
<h1>AERIS - Human ATC console <small>(UI is a view; the domain decides)</small></h1>
<div>
  flight <select id="flight"></select>
  role <select id="role"><option>OBSERVER</option><option selected>CONTROLLER</option><option>ADMIN</option></select>
  reason <input id="reason" value="human controller action" size="30">
  <button onclick="refreshList()">refresh</button>
</div>
<div class="grid">
  <div class="panel"><h2>Flight</h2><pre id="flightbox"></pre></div>
  <div class="panel"><h2>Recommended action</h2><pre id="recommend"></pre>
    <h2>Allowed human actions</h2><div id="actions"></div><pre id="result"></pre></div>
  <div class="panel"><h2>Hazards and evidence</h2><pre id="hazards"></pre></div>
  <div class="panel"><h2>Alternative routes (score, diversity)</h2><pre id="alternatives"></pre></div>
  <div class="panel"><h2>Side effects</h2><pre id="effects"></pre></div>
  <div class="panel"><h2>Signal provenance / recent telemetry</h2><pre id="telemetry"></pre></div>
</div>
<script>
const $ = (id) => document.getElementById(id);
const fmt = (v) => JSON.stringify(v, null, 2);
let view = null;
async function refreshList() {
  const flights = await (await fetch('/flights')).json();
  const sel = $('flight'); const keep = sel.value; sel.innerHTML = '';
  for (const f of flights) { const o = document.createElement('option'); o.value = f.flight_id;
    o.textContent = f.flight_id + ' ' + (f.state || ''); sel.appendChild(o); }
  if (keep) sel.value = keep; await refresh();
}
async function refresh() {
  const id = $('flight').value; if (!id) return;
  const r = await fetch('/flights/' + id + '/console');
  if (!r.ok) { $('flightbox').textContent = 'not a live flight in this process'; return; }
  view = await r.json();
  $('flightbox').textContent = fmt({mission: view.mission.objective, flight_id: view.flight_id, state: view.state,
    control_version: view.control_version, route: view.current_route, waypoint: view.current_waypoint,
    cancel_status: view.cancel_status, cancel_outcome: view.cancel_outcome});
  $('recommend').textContent = (view.recommended_action || '-') + '\\n' + (view.recommended_reason || '');
  $('hazards').textContent = fmt(view.hazards.map(h => ({type: h.type, severity: h.severity, evidence: h.evidence})));
  $('alternatives').textContent = fmt(view.alternatives);
  $('effects').textContent = fmt(view.side_effects);
  $('telemetry').textContent = fmt({provenance: view.signal_provenance,
    last: view.recent_telemetry.slice(-2).map(t => t.metrics)});
  const box = $('actions'); box.innerHTML = '';
  for (const a of view.actions) {
    const row = document.createElement('div'); const b = document.createElement('button');
    b.textContent = a.action + ' (' + a.required_role + ')'; b.disabled = !a.enabled; b.title = a.reason;
    b.onclick = () => act(a.action); row.appendChild(b);
    const why = document.createElement('span'); why.className = 'why'; why.textContent = a.reason; row.appendChild(why);
    box.appendChild(row);
  }
}
async function act(action) {
  const body = {role: $('role').value, reason: $('reason').value, operator: 'console',
    expected_version: view.control_version};
  const r = await fetch('/flights/' + view.flight_id + '/control/' + action.toLowerCase(),
    {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(body)});
  $('result').textContent = r.status + ' ' + (await r.text()).slice(0, 400);
  await refresh();
}
$('flight').onchange = refresh;
refreshList(); setInterval(refresh, 2000);
</script>
</body>
</html>
"""
