/* All page views. Each view: async (root, params) => void */
const isMgr = () => ["admin", "plant_head"].includes(API.user.role);
const can = (...roles) => roles.includes(API.user.role);

/* ===================================================================== shared pieces */
function alertList(alerts, { ack = true } = {}) {
  if (!alerts.length) return '<div class="empty">No open alerts.</div>';
  return alerts.map((a) => `<div class="alert-row">${chip(a.severity, a.severity === "critical" ? "red" : a.severity === "warning" ? "amber" : "teal")}
    <div style="flex:1">${esc(a.message)}<div class="when">${fmt.label(a.kind)} · ${fmt.ago(a.created_at)}</div></div>
    ${ack ? `<button class="btn ghost sm" data-ack="${a.id}">Acknowledge</button>` : ""}</div>`).join("");
}
function bindAck(root, after) {
  $$("[data-ack]", root).forEach((b) => b.onclick = async () => { await API.post(`/alerts/${b.dataset.ack}/ack`); b.closest(".alert-row").remove(); after && after(); });
}
function riskMeter(p) {
  const lvl = riskLevel(p); const c = { low: "var(--green)", medium: "var(--amber)", high: "var(--red)", critical: "var(--red)" }[lvl];
  return `<div class="row" style="gap:10px"><div class="meter" style="flex:1"><i style="width:${Math.max(3, p * 100)}%;background:${c}"></i></div><b class="num">${fmt.risk(p)}</b>${chip(lvl)}</div>`;
}

/* ---------- new ticket (with live GenAI triage preview) ---------- */
function newTicketModal(machineId) {
  const ov = formModal({
    title: "Report an issue", submit: "Raise ticket",
    intro: '<p class="muted small">Describe what you see or hear. The assistant suggests a category, priority, likely causes and spare parts for the technician.</p>',
    fields: [
      { name: "machine_id", label: "Machine", type: "select", options: App.machineOptions(), value: machineId, numeric: true, required: true, full: true },
      { name: "title", label: "Short title", required: true, maxlength: 200, full: true, placeholder: "e.g. Grinding noise from indexing station" },
      { name: "description", label: "What happened", type: "textarea", full: true, placeholder: "When it started, alarms on HMI, noise, smell, readings…" },
      { name: "priority", label: "Your priority", type: "select", options: ["low", "medium", "high", "critical"], value: "medium" },
      { name: "category", label: "Category", type: "select", options: ["mechanical", "electrical", "pneumatic", "sensor", "software", "other"] },
      ...(isMgr() ? [{ name: "assigned_to_id", label: "Assign to", type: "select", numeric: true, options: [["", "Unassigned"], ...(App.cache.techs || []).map((t) => [t.id, t.full_name])] }] : []),
      { name: "use_ai_triage", label: "Use AI triage (may raise priority, never lowers it)", type: "checkbox", value: true },
    ],
    onSubmit: async (d) => {
      const t = await API.post("/tickets", d);
      toast(`Ticket ${t.ticket_no} raised · priority ${t.priority}`);
      ticketDrawer(t.id);
      if (location.hash.includes("tickets")) App.route();
    },
  });
  const form = $("form", ov);
  const box = document.createElement("div"); box.className = "mt"; $(".fields", form).after(box);
  const btn = document.createElement("button"); btn.type = "button"; btn.className = "btn ghost sm"; btn.textContent = "Preview AI triage";
  box.before(btn);
  btn.onclick = async () => {
    const desc = `${form.elements.title.value}. ${form.elements.description.value}`;
    if (desc.length < 8) return toast("Add a title or description first", "error");
    btn.disabled = true; box.innerHTML = '<div class="muted small">Analysing…</div>';
    try {
      const r = await API.post("/genai/triage", { machine_id: +form.elements.machine_id.value, description: desc });
      box.innerHTML = triageBox(r);
      form.elements.category.value = r.category;
      if (["low", "medium", "high", "critical"].indexOf(r.priority) > ["low", "medium", "high", "critical"].indexOf(form.elements.priority.value)) form.elements.priority.value = r.priority;
    } catch (e) { box.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
    btn.disabled = false;
  };
}
function triageBox(r) {
  if (!r) return "";
  const parts = r.suggested_parts || (r.suggested_part_nos || []).map((p) => ({ part_no: p }));
  return `<div class="ai-box"><div class="ai-head"><span>AI triage</span>${providerChip(r.provider)}</div>
    <div class="row">${chip(r.category, "teal")} ${chip(r.priority)}</div>
    ${r.probable_causes?.length ? `<h3>Probable causes</h3><ul style="margin:0;padding-left:18px">${r.probable_causes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
    ${r.first_checks?.length ? `<h3>First checks</h3><ol style="margin:0;padding-left:18px">${r.first_checks.map((c) => `<li>${esc(c)}</li>`).join("")}</ol>` : ""}
    ${parts.length ? `<h3>Suggested spares</h3><div class="row">${parts.map((p) => `<span class="chip">${esc(p.part_no)}${p.name ? " · " + esc(p.name) : ""}${p.in_stock != null ? ` (${p.in_stock} in stock)` : ""}</span>`).join("")}</div>` : ""}
    ${r.safety_note ? `<p class="small mt"><b>Safety:</b> ${esc(r.safety_note)}</p>` : ""}
    ${r.reasoning ? `<p class="small muted">${esc(r.reasoning)}</p>` : ""}</div>`;
}

/* ---------- ticket detail drawer with workflow ---------- */
async function ticketDrawer(id) {
  const t = await API.get(`/tickets/${id}`);
  const role = API.user.role;
  const acts = [];
  if (role !== "operator") {
    if (["open", "assigned"].includes(t.status)) acts.push(["in_progress", "Start work"]);
    if (t.status === "in_progress") acts.push(["on_hold", "Put on hold"], ["resolve", "Resolve"]);
    if (t.status === "on_hold") acts.push(["in_progress", "Resume"]);
    if (t.status === "resolved" && isMgr()) acts.push(["closed", "Verify & close"], ["in_progress", "Reopen"]);
    if (t.status === "open" && isMgr()) acts.push(["closed", "Close (no action)"]);
    if (role === "technician" && t.assigned_to_id !== API.user.id && !["resolved", "closed"].includes(t.status)) acts.unshift(["self", "Assign to me"]);
  }
  const ov = openOverlay(`<div class="drawer-head"><div><b>${esc(t.ticket_no)}</b> ${chip(t.status)} ${chip(t.priority)} ${t.source.startsWith("ai") ? chip("AI generated", "ai") : ""}<div class="small muted">${esc(t.machine_code)} · ${esc(t.machine_name)}</div></div><button class="x" data-close aria-label="Close">×</button></div>
  <div class="drawer-body">
    <h2>${esc(t.title)}</h2>
    <p style="white-space:pre-wrap">${esc(t.description || "")}</p>
    <div class="specs mt"><div><span>Category</span>${esc(fmt.label(t.category))}</div><div><span>Type</span>${esc(fmt.label(t.ticket_type))}</div>
      <div><span>Reported by</span>${esc(t.reported_by)}</div><div><span>Raised</span>${fmt.dt(t.created_at)}</div>
      <div><span>Assigned to</span>${esc(t.assigned_to || "–")}</div><div><span>Resolution time</span>${t.resolution_hours != null ? t.resolution_hours + " h" : "–"}</div></div>
    ${acts.length ? `<div class="row mt">${acts.map(([k, l]) => `<button class="btn ${k === "on_hold" || k === "closed" && l.startsWith("Close") ? "ghost" : ""}" data-act="${k}">${l}</button>`).join("")}
      ${isMgr() && !["resolved", "closed"].includes(t.status) ? `<select id="assignSel" style="width:auto"><option value="">Assign technician…</option>${(App.cache.techs || []).map((x) => `<option value="${x.id}">${esc(x.full_name)}</option>`).join("")}</select>` : ""}</div>` : ""}
    ${t.ai_triage ? `<div class="mt">${triageBox(t.ai_triage)}</div>` : ""}
    ${t.root_cause ? `<div class="panel mt"><h3 style="margin-top:0">Root cause</h3><p>${esc(t.root_cause)}</p><h3>Action taken</h3><p>${esc(t.action_taken || "")}</p>${t.labour_hours ? `<p class="muted small">${t.labour_hours} labour hours</p>` : ""}</div>` : ""}
    <div class="grid g2 mt">
      <div class="panel"><div class="panel-head"><h2>Spare parts used</h2><span>₹${fmt.n(t.parts_cost)}</span></div>
        ${t.parts_used.length ? t.parts_used.map((p) => `<div class="factor"><span>${esc(p.part_no)} · ${esc(p.name)}</span><span>${p.quantity} × · ₹${fmt.n(p.cost)}</span></div>`).join("") : '<p class="muted small">None issued.</p>'}
        ${can("admin", "plant_head", "technician") && !["resolved", "closed"].includes(t.status) ? '<button class="btn ghost sm mt" id="issueBtn">Issue part</button>' : ""}</div>
      <div class="panel"><h2>Activity</h2><ul class="timeline">${t.activities.map((a) => `<li>${esc(a.message)}<small>${esc(a.user)} · ${fmt.dt(a.created_at)}</small></li>`).join("")}</ul>
        <form id="cmt" class="row mt"><input name="c" placeholder="Add a comment" maxlength="2000" style="flex:1"><button class="btn sm">Post</button></form></div>
    </div>
    ${role !== "operator" ? `<button class="btn ghost mt" id="askCopilot">Ask copilot about this issue</button>` : ""}
  </div>`, { drawer: true });
  const reload = () => { ov.close(); ticketDrawer(id); if (location.hash.includes("ticket") || location.hash.includes("home")) App.route(); };
  const patch = async (b) => { try { await API.patch(`/tickets/${id}`, b); toast("Ticket updated"); reload(); } catch (e) { toast(e.message, "error"); } };
  $$("[data-act]", ov).forEach((b) => b.onclick = () => {
    const k = b.dataset.act;
    if (k === "self") return patch({ assigned_to_id: API.user.id });
    if (k === "resolve") return formModal({
      title: "Resolve ticket", submit: "Mark resolved",
      fields: [{ name: "root_cause", label: "Root cause", type: "textarea", required: true, full: true, value: t.root_cause || "" },
      { name: "action_taken", label: "Action taken", type: "textarea", required: true, full: true },
      { name: "labour_hours", label: "Labour hours", type: "number", min: 0, max: 500, step: 0.1 }],
      onSubmit: async (d) => { await API.patch(`/tickets/${id}`, { ...d, status: "resolved" }); toast("Ticket resolved — machine back to running"); reload(); },
    });
    patch({ status: k });
  });
  const sel = $("#assignSel", ov); if (sel) sel.onchange = () => sel.value && patch({ assigned_to_id: +sel.value });
  $("#cmt", ov).onsubmit = (e) => { e.preventDefault(); const c = e.target.c.value.trim(); if (c) patch({ comment: c }); };
  const ib = $("#issueBtn", ov);
  if (ib) ib.onclick = async () => {
    const parts = await API.get("/spares");
    formModal({
      title: "Issue spare part", submit: "Issue",
      fields: [{ name: "part_id", label: "Part", type: "select", numeric: true, required: true, full: true, options: parts.map((p) => [p.id, `${p.part_no} — ${p.name} (${p.quantity} in stock)`]) },
      { name: "quantity", label: "Quantity", type: "number", min: 1, max: 1000, value: 1, required: true }],
      onSubmit: async (d) => { await API.post(`/tickets/${id}/parts`, d); toast("Part issued; stock updated"); reload(); },
    });
  };
  const ac = $("#askCopilot", ov);
  if (ac) ac.onclick = () => { ov.close(); sessionStorage.setItem("copilot_q", JSON.stringify({ q: `${t.title}. ${t.description || ""}`.slice(0, 500), m: t.machine_id })); location.hash = "#/copilot"; };
}

/* ---------- machine drawer ---------- */
async function machineDrawer(id) {
  const [m, readings] = await Promise.all([API.get(`/machines/${id}`), API.get(`/sensors?machine_id=${id}&limit=60`)]);
  let pred = null; try { pred = await API.get(`/ai/predict/${id}`); } catch (_) { }
  const ov = openOverlay(`<div class="drawer-head"><div><b>${esc(m.code)}</b> ${chip(m.status)} <span class="muted small">${esc(m.machine_type)} · ${esc(m.line || "")} · Criticality ${m.criticality}</span><div class="small muted">${esc(m.name)}</div></div><button class="x" data-close aria-label="Close">×</button></div>
  <div class="drawer-body">${machineBody(m, pred, readings)}</div>`, { drawer: true });
  bindMachineBody(ov, m, pred, readings, () => { ov.close(); machineDrawer(id); });
}
function machineBody(m, pred, readings) {
  const last = readings[0];
  return `
    <div class="panel"><div class="panel-head"><h2>Failure prediction (next 24 h)</h2>${pred ? `<span>${pred.est_hours_to_failure ? "≈ " + pred.est_hours_to_failure + " h to failure" : "No failure expected"}</span>` : ""}</div>
      ${pred ? `${riskMeter(pred.probability)}<p class="mt"><b>${esc(pred.mode_description)}</b></p>
        ${pred.top_factors.length && pred.probability >= 0.25 ? `<h3>What's driving it</h3>${pred.top_factors.map((f) => `<div class="factor"><span>${esc(f.label)} is ${f.direction}</span><span class="num">${fmt.n(f.value, 2)} (z ${f.z_score > 0 ? "+" : ""}${f.z_score})</span></div>`).join("")}` : '<p class="muted small">All signals within normal range.</p>'}
        <div class="chart-box mt" style="height:150px"><canvas id="predChart"></canvas></div>` : '<p class="muted">No sensor data yet.</p>'}
    </div>
    <div class="grid g2 mt">
      <div class="panel"><h2>Latest reading</h2>${last ? `<div class="specs">
        <div><span>Temperature</span>${last.temperature} °C</div><div><span>Vibration</span>${last.vibration} mm/s</div>
        <div><span>Pressure</span>${last.pressure} bar</div><div><span>Speed</span>${last.rpm} rpm</div>
        <div><span>Torque</span>${last.torque} Nm</div><div><span>Tool wear</span>${last.tool_wear} min</div></div>
        <p class="small muted mt">${fmt.dt(last.recorded_at)} · ${esc(last.source)}${last.is_anomaly ? ` · <span style="color:var(--red)">${esc(last.anomaly_reason)}</span>` : ""}</p>` : '<p class="muted">No readings.</p>'}</div>
      <div class="panel"><h2>Health & reliability</h2><div class="specs">
        <div><span>Health score</span>${fmt.n(m.health_score, 0)} / 100</div><div><span>Downtime (30 d)</span>${fmt.n(m.downtime_minutes_30d)} min</div>
        <div><span>Breakdowns logged</span>${m.reliability.breakdowns_logged}</div><div><span>MTTR</span>${m.reliability.mttr_hours ?? "–"} h</div>
        <div><span>Last PM</span>${fmt.d(m.last_pm_date)}</div><div><span>Next PM</span>${fmt.d(m.next_pm_date)} ${m.pm_overdue ? chip("overdue", "red") : ""}</div></div></div>
    </div>
    <div class="panel mt"><div class="panel-head"><h2>Sensor trend</h2><span>last ${readings.length} readings</span></div><div class="chart-box"><canvas id="trendChart"></canvas></div></div>
    <div class="row mt">
      ${can("admin", "operator", "technician") ? '<button class="btn" id="mReading">Log reading</button>' : ""}
      <button class="btn ghost" id="mTicket">Report issue</button>
      <button class="btn ghost" id="mDown">Log downtime</button>
      ${can("admin", "plant_head", "technician") ? `<select id="mStatus" style="width:auto">${["running", "idle", "down", "maintenance"].map((s) => `<option ${s === m.status ? "selected" : ""}>${s}</option>`).join("")}</select>` : ""}
    </div>
    <div class="panel mt"><h2>Specifications</h2><div class="specs">
      <div><span>Manufacturer</span>${esc(m.manufacturer || "–")}</div><div><span>Model</span>${esc(m.model || "–")}</div><div><span>Serial</span>${esc(m.serial_no || "–")}</div>
      <div><span>Installed</span>${fmt.d(m.install_date)}</div><div><span>Rated speed</span>${fmt.n(m.rated_speed)} units/h</div><div><span>Power</span>${m.power_kw ?? "–"} kW</div>
      <div><span>Temp limit</span>${m.temp_max} °C</div><div><span>Vibration limit</span>${m.vibration_max} mm/s</div><div><span>Air pressure</span>${m.pressure_min}–${m.pressure_max} bar</div>
      ${Object.entries(m.specs || {}).map(([k, v]) => `<div><span>${esc(fmt.label(k))}</span>${esc(v)}</div>`).join("")}</div></div>
    <div class="panel flush mt"><h2>Maintenance history</h2>${table([
    { label: "Ticket", key: "ticket_no" }, { label: "Raised", get: (r) => fmt.dt(r.created_at) }, { label: "Issue", key: "title" },
    { label: "Status", html: (r) => chip(r.status) }, { label: "Root cause", get: (r) => r.root_cause || "–" },
    { label: "Parts", get: (r) => r.parts.map((p) => p.part_no).join(", ") || "–" }], m.maintenance_history.slice(0, 15), { onRow: true, empty: "No maintenance history." })}</div>
    <div class="panel flush mt"><h2>Downtime history</h2>${table([
      { label: "Start", get: (r) => fmt.dt(r.start_time) }, { label: "Reason", html: (r) => chip(r.reason_category, "teal") },
      { label: "Detail", get: (r) => r.reason_detail || "" }, { label: "Minutes", n: true, get: (r) => r.duration_min ?? "ongoing" }], m.downtime_history.slice(0, 10), { empty: "No downtime recorded." })}</div>`;
}
function bindMachineBody(root, m, pred, readings, reload) {
  const rs = [...readings].reverse();
  Charts.make($("#trendChart", root), {
    type: "line", data: {
      labels: rs.map((r) => new Date(r.recorded_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })), datasets: [
        { label: "Temperature °C", data: rs.map((r) => r.temperature), borderColor: Charts.palette.amber, pointRadius: rs.map((r) => r.is_anomaly ? 4 : 0), pointBackgroundColor: Charts.palette.red, tension: .3, yAxisID: "y" },
        { label: "Vibration mm/s", data: rs.map((r) => r.vibration), borderColor: Charts.palette.teal, pointRadius: rs.map((r) => r.is_anomaly ? 4 : 0), pointBackgroundColor: Charts.palette.red, tension: .3, yAxisID: "y1" },
        { label: "Pressure bar", data: rs.map((r) => r.pressure), borderColor: Charts.palette.ink, pointRadius: 0, tension: .3, yAxisID: "y1" }]
    }, options: { interaction: { mode: "index", intersect: false }, scales: { y: { position: "left", title: { display: true, text: "°C" } }, y1: { position: "right", grid: { drawOnChartArea: false } } } },
  });
  if (pred && pred.history.length) Charts.make($("#predChart", root), {
    type: "line", data: { labels: pred.history.map((h) => fmt.dt(h.t)), datasets: [{ label: "Failure probability", data: pred.history.map((h) => h.p * 100), borderColor: Charts.palette.red, backgroundColor: "rgba(191,58,48,.08)", fill: true, pointRadius: 0, tension: .3 }] },
    options: { plugins: { legend: { display: false } }, scales: { y: { min: 0, max: 100, ticks: { callback: (v) => v + "%" } }, x: { ticks: { maxTicksLimit: 6 } } } },
  });
  bindRows(root, m.maintenance_history.slice(0, 15), (r) => ticketDrawer(r.id));
  const q = (s) => $(s, root);
  if (q("#mReading")) q("#mReading").onclick = () => readingModal(m.id, reload);
  q("#mTicket").onclick = () => newTicketModal(m.id);
  q("#mDown").onclick = () => downtimeModal(m.id, reload);
  if (q("#mStatus")) q("#mStatus").onchange = async (e) => { await API.patch(`/machines/${m.id}/status`, { status: e.target.value }); toast(`${m.code} set to ${e.target.value}`); App.cache.machines = null; };
}

function readingModal(machineId, after) {
  formModal({
    title: "Log sensor reading", submit: "Save & analyse",
    intro: '<p class="muted small">Each reading is checked by the anomaly detector and updates the machine\'s failure prediction.</p>',
    fields: [{ name: "machine_id", label: "Machine", type: "select", options: App.machineOptions(), value: machineId, numeric: true, required: true, full: true },
    { name: "temperature", label: "Temperature (°C)", type: "number", step: 0.1, min: -20, max: 300, required: true, value: 55 },
    { name: "vibration", label: "Vibration (mm/s RMS)", type: "number", step: 0.01, min: 0, max: 100, required: true, value: 2.8 },
    { name: "pressure", label: "Air pressure (bar)", type: "number", step: 0.01, min: 0, max: 50, required: true, value: 6.0 },
    { name: "rpm", label: "Speed (rpm)", type: "number", min: 0, max: 20000, required: true, value: 1500 },
    { name: "torque", label: "Torque (Nm)", type: "number", step: 0.1, min: 0, max: 500, required: true, value: 40 },
    { name: "tool_wear", label: "Tool wear (min)", type: "number", min: 0, max: 1000, required: true, value: 100 },
    { name: "humidity", label: "Room humidity (%)", type: "number", min: 0, max: 100, step: 0.1 }],
    onSubmit: async (d) => {
      const r = await API.post("/sensors", d);
      const a = r.anomaly;
      openOverlay(`<div class="panel-head"><h2>Reading analysed</h2><button class="x" data-close>×</button></div>
        <div class="ai-box"><div class="ai-head"><span>Anomaly detection</span>${chip(a.is_anomaly ? a.severity : "normal", a.is_anomaly ? (a.severity === "critical" ? "red" : "amber") : "green")}</div>
        ${a.is_anomaly ? `<ul style="margin:0;padding-left:18px">${a.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "<p>Reading is within the normal operating envelope.</p>"}</div>
        ${r.prediction ? `<div class="mt"><h3>Failure risk now</h3>${riskMeter(r.prediction.probability)}<p class="small mt">${esc(r.prediction.mode_description)}</p></div>` : ""}
        ${r.auto_ticket ? `<p class="mt"><b>Maintenance ticket ${esc(r.auto_ticket)}</b> was created automatically.</p>` : ""}
        <div class="row mt"><span class="spacer"></span><button class="btn" data-close>Done</button></div>`);
      after && after();
    },
  });
}
function downtimeModal(machineId, after) {
  formModal({
    title: "Log downtime", submit: "Save downtime",
    intro: '<p class="muted small">Leave the end time blank if the machine is still stopped; close it later from the Downtime page.</p>',
    fields: [{ name: "machine_id", label: "Machine", type: "select", options: App.machineOptions(), value: machineId, numeric: true, required: true, full: true },
    { name: "reason_category", label: "Reason", type: "select", required: true, options: ["breakdown", "changeover", "material_shortage", "planned_maintenance", "power_failure", "quality_hold", "no_operator", "other"] },
    { name: "start_time", label: "Started", type: "datetime-local", required: true, value: nowLocalISO() },
    { name: "end_time", label: "Ended", type: "datetime-local" },
    { name: "reason_detail", label: "Details", type: "textarea", full: true },
    { name: "is_planned", label: "Planned stop", type: "checkbox" }],
    onSubmit: async (d) => { await API.post("/downtime", d); toast("Downtime logged"); App.cache.machines = null; after && after(); },
  });
}

/* ===================================================================== views */
const Views = {
  /* ---------------- plant dashboard (managers) ---------------- */
  async dashboard(root) {
    App.setTitle("Plant dashboard", "Production, OEE, downtime and quality across all 40 machines");
    App.actions(`<select id="days" style="width:auto"><option value="1">Today</option><option value="7" selected>Last 7 days</option><option value="30">Last 30 days</option></select>`);
    const draw = async () => {
      Charts.destroyAll();
      const d = await API.get(`/dashboard/overview?days=${$("#days").value}`);
      const k = d.kpis;
      root.innerHTML = `
      <div class="kpis">
        <div class="kpi hero"><span>OEE</span><b>${fmt.pct(k.oee)}</b><small>target ${d.oee_target}% · today ${fmt.pct(k.today_oee)}</small></div>
        <div class="kpi"><span>Availability</span><b>${fmt.pct(k.availability)}</b></div>
        <div class="kpi"><span>Performance</span><b>${fmt.pct(k.performance)}</b></div>
        <div class="kpi"><span>Quality</span><b>${fmt.pct(k.quality)}</b></div>
        <div class="kpi"><span>Good units</span><b>${fmt.n(k.good / 1e6, 2)}M</b><small>${fmt.n(k.rejected)} rejected</small></div>
        <div class="kpi"><span>Downtime</span><b class="${k.unplanned_downtime_hours > 50 ? "warn" : ""}">${fmt.n(k.downtime_hours, 1)} h</b><small>${fmt.n(k.unplanned_downtime_hours, 1)} h unplanned</small></div>
        <div class="kpi"><span>MTBF / MTTR</span><b>${k.mtbf_hours ?? "–"} h</b><small>MTTR ${k.mttr_hours ?? "–"} h</small></div>
        <div class="kpi"><span>Failed batches</span><b class="${k.failed_batches ? "bad" : ""}">${k.failed_batches}</b><small>of ${fmt.n(k.batches_inspected)} · ${k.defect_rate}% defects</small></div>
      </div>
      <div class="grid g-wide mt">
        <div class="panel"><div class="panel-head"><h2>OEE trend</h2><span>A × P × Q per day</span></div><div class="chart-box tall"><canvas id="cTrend"></canvas></div></div>
        <div class="panel"><div class="panel-head"><h2>Machines at risk</h2><a href="#/insights" class="small">All predictions</a></div>
          ${d.high_risk_machines.map((m) => `<div class="factor click" style="cursor:pointer" data-m="${m.id}"><span><b>${esc(m.code)}</b> <span class="muted small">${esc(m.status)}</span></span><span style="width:55%">${riskMeter(m.risk)}</span></div>`).join("")}
          <p class="small muted mt">${k.machines_running}/${k.machines_total} machines running · ${k.anomalies_24h} anomalies in 24 h</p></div>
      </div>
      <div class="grid g3 mt">
        <div class="panel"><div class="panel-head"><h2>Downtime by reason</h2><span>Pareto, minutes</span></div><div class="chart-box"><canvas id="cPareto"></canvas></div></div>
        <div class="panel"><div class="panel-head"><h2>Downtime by machine</h2><span>top 10</span></div><div class="chart-box"><canvas id="cDtM"></canvas></div></div>
        <div class="panel"><div class="panel-head"><h2>Shift comparison</h2><span>OEE components</span></div><div class="chart-box"><canvas id="cShift"></canvas></div></div>
      </div>
      <div class="grid g3 mt">
        <div class="panel"><div class="panel-head"><h2>Defects by type</h2></div><div class="chart-box"><canvas id="cDef"></canvas></div></div>
        <div class="panel"><div class="panel-head"><h2>Lowest OEE machines</h2></div>${table([{ label: "Machine", key: "machine" }, { label: "OEE", n: true, get: (r) => fmt.pct(r.oee) }, { label: "Avail.", n: true, get: (r) => fmt.pct(r.availability) }, { label: "Quality", n: true, get: (r) => fmt.pct(r.quality) }], d.worst_oee_machines.slice(0, 7))}</div>
        <div class="panel"><div class="panel-head"><h2>Open tickets</h2><a href="#/tickets" class="small">Open queue</a></div>
          ${["critical", "high", "medium", "low"].map((p) => `<div class="factor"><span>${chip(p)}</span><b class="num">${d.tickets_by_priority[p]}</b></div>`).join("")}
          <h3>Spares below reorder level</h3>${d.low_stock_parts.slice(0, 5).map((p) => `<div class="factor"><span>${esc(p.part_no)}</span><span class="num" style="color:var(--red)">${p.quantity} / ${p.reorder_level}</span></div>`).join("") || '<p class="muted small">All stocked.</p>'}
          ${d.low_stock_parts.length > 5 ? `<a class="small" href="#/spares">+${d.low_stock_parts.length - 5} more</a>` : ""}</div>
      </div>
      <div class="panel mt"><div class="panel-head"><h2>Recent alerts</h2><button class="btn ghost sm" id="ackAll">Acknowledge all</button></div><div id="alertBox">${alertList(d.recent_alerts)}</div></div>`;
      const P = Charts.palette;
      Charts.make($("#cTrend"), {
        type: "line", data: {
          labels: d.oee_trend.map((r) => r.date.slice(5)), datasets: [
            { label: "OEE", data: d.oee_trend.map((r) => r.oee), borderColor: P.teal, borderWidth: 3, tension: .3, pointRadius: 2 },
            { label: "Availability", data: d.oee_trend.map((r) => r.availability), borderColor: P.grey, borderDash: [4, 3], pointRadius: 0, tension: .3 },
            { label: "Performance", data: d.oee_trend.map((r) => r.performance), borderColor: P.amber, pointRadius: 0, tension: .3 },
            { label: "Target", data: d.oee_trend.map(() => d.oee_target), borderColor: P.red, borderWidth: 1, borderDash: [2, 4], pointRadius: 0 }]
        }, options: { scales: { y: { suggestedMin: 50, max: 100 } } },
      });
      Charts.make($("#cPareto"), {
        data: {
          labels: d.downtime_pareto.map((r) => fmt.label(r.reason)), datasets: [
            { type: "bar", label: "Minutes", data: d.downtime_pareto.map((r) => r.minutes), backgroundColor: P.ink, yAxisID: "y" },
            { type: "line", label: "Cumulative %", data: d.downtime_pareto.map((r) => r.cum_pct), borderColor: P.amber, yAxisID: "y1", pointRadius: 2 }]
        }, options: { plugins: { legend: { display: false } }, scales: { y1: { position: "right", max: 100, grid: { drawOnChartArea: false } }, x: { ticks: { maxRotation: 50, font: { size: 10 } } } } },
      });
      Charts.make($("#cDtM"), { type: "bar", data: { labels: d.downtime_by_machine.map((r) => r.machine), datasets: [{ data: d.downtime_by_machine.map((r) => r.minutes), backgroundColor: d.downtime_by_machine.map((_, i) => i < 3 ? P.red : P.grey) }] }, options: { indexAxis: "y", plugins: { legend: { display: false } } } });
      Charts.make($("#cShift"), {
        type: "bar", data: {
          labels: d.shift_comparison.map((r) => "Shift " + r.shift), datasets: [
            { label: "OEE", data: d.shift_comparison.map((r) => r.oee), backgroundColor: P.teal },
            { label: "Availability", data: d.shift_comparison.map((r) => r.availability), backgroundColor: P.grey },
            { label: "Quality", data: d.shift_comparison.map((r) => r.quality), backgroundColor: P.green }]
        }, options: { scales: { y: { suggestedMin: 50, max: 100 } } },
      });
      Charts.make($("#cDef"), { type: "doughnut", data: { labels: d.defects_by_type.map((r) => fmt.label(r.type)), datasets: [{ data: d.defects_by_type.map((r) => r.count), backgroundColor: [P.red, P.amber, P.teal, P.ink, P.violet, P.grey, P.green] }] }, options: { plugins: { legend: { position: "right" } } } });
      $$("[data-m]", root).forEach((el) => el.onclick = () => machineDrawer(+el.dataset.m));
      bindAck(root);
      $("#ackAll").onclick = async () => { await API.post("/alerts/ack-all"); $("#alertBox").innerHTML = alertList([]); };
    };
    $("#days").onchange = draw;
    await draw();
  },

  /* ---------------- role home (operator / technician) ---------------- */
  async home(root) {
    const d = await API.get("/dashboard/my");
    const role = API.user.role;
    const a = d.assignment;
    App.setTitle(role === "operator" ? "My shift" : "My work", `${fmt.d(d.today)}${a ? ` · Shift ${a.shift} (${a.window})${a.machine ? " · " + a.machine : ""}` : " · not rostered today"}`);
    const myMachine = a && a.machine_id;
    if (role === "operator") {
      App.actions(`<button class="btn" id="hProd">Log production</button><button class="btn ghost" id="hRead">Log reading</button><button class="btn ghost" id="hIssue">Report issue</button>`);
      const p = d.today_production;
      root.innerHTML = `<div class="kpis"><div class="kpi hero"><span>My OEE today</span><b>${p.produced ? fmt.pct(p.oee) : "–"}</b></div>
        <div class="kpi"><span>Produced</span><b>${fmt.n(p.produced)}</b></div><div class="kpi"><span>Rejected</span><b>${fmt.n(p.rejected)}</b></div>
        <div class="kpi"><span>Logs today</span><b>${d.logs_today}</b></div><div class="kpi"><span>My open issues</span><b>${d.my_open_issues}</b></div></div>
        <div class="grid g2 mt"><div class="panel" id="myM"></div><div class="panel"><h2>Alerts on the floor</h2>${alertList(d.alerts, { ack: false })}</div></div>`;
      $("#hProd").onclick = () => Views._prodModal(myMachine);
      $("#hRead").onclick = () => readingModal(myMachine, () => App.route());
      $("#hIssue").onclick = () => newTicketModal(myMachine);
    } else {
      App.actions(`<button class="btn" id="hCopilot">Open copilot</button>`);
      const mine = await API.get("/tickets?mine=true&open_only=true");
      const unassigned = (await API.get("/tickets?status=open")).filter((t) => !t.assigned_to_id);
      root.innerHTML = `<div class="kpis"><div class="kpi hero"><span>My open tickets</span><b>${d.my_tickets}</b></div>
        <div class="kpi"><span>Unassigned in queue</span><b class="${d.unassigned_tickets ? "warn" : ""}">${d.unassigned_tickets}</b></div>
        <div class="kpi"><span>Resolved (30 d)</span><b>${d.resolved_30d}</b></div><div class="kpi"><span>High-risk machines</span><b class="${d.high_risk_machines.length ? "bad" : ""}">${d.high_risk_machines.length}</b></div></div>
        <div class="grid g-wide mt"><div class="panel flush"><h2>Assigned to me</h2><div id="tMine"></div><h2 class="mt">Waiting for a technician</h2><div id="tUn"></div></div>
        <div class="panel"><h2>Predicted to fail</h2>${d.high_risk_machines.map((m) => `<div class="factor" style="cursor:pointer" data-m="${m.id}"><b>${esc(m.code)}</b><span style="width:60%">${riskMeter(m.risk)}</span></div>`).join("") || '<p class="muted">No machine above 40% risk.</p>'}
        <h2 class="mt">Alerts</h2>${alertList(d.alerts)}</div></div><div class="panel mt" id="myM"></div>`;
      const cols = [{ label: "Ticket", key: "ticket_no" }, { label: "Machine", key: "machine_code" }, { label: "Issue", key: "title" }, { label: "Priority", html: (r) => chip(r.priority) }, { label: "Status", html: (r) => chip(r.status) }, { label: "Raised", get: (r) => fmt.ago(r.created_at) }];
      $("#tMine").innerHTML = table(cols, mine, { onRow: true, empty: "Nothing assigned to you." }); bindRows($("#tMine"), mine, (t) => ticketDrawer(t.id));
      $("#tUn").innerHTML = table(cols, unassigned, { onRow: true, empty: "Queue is clear." }); bindRows($("#tUn"), unassigned, (t) => ticketDrawer(t.id));
      $$("[data-m]", root).forEach((el) => el.onclick = () => machineDrawer(+el.dataset.m));
      bindAck(root);
      $("#hCopilot").onclick = () => location.hash = "#/copilot";
    }
    const mm = $("#myM");
    if (myMachine) {
      const m = App.cache.machines.find((x) => x.id === myMachine);
      mm.innerHTML = `<div class="panel-head"><h2>${role === "operator" ? "My machine" : "Machine"}: ${esc(m.code)}</h2><button class="btn ghost sm" id="openM">Details</button></div>${riskMeter(m.failure_risk)}<p class="small muted mt">${esc(m.name)} · ${chip(m.status)} · health ${fmt.n(m.health_score)}</p>`;
      $("#openM").onclick = () => machineDrawer(m.id);
    } else mm.remove();
  },

  /* ---------------- live floor (signature) ---------------- */
  async floor(root) {
    App.setTitle("Live floor", "Every running machine reports every few seconds; tiles turn amber or red as predicted failure risk climbs");
    App.actions(`<span class="small muted" id="simState"></span>`);
    root.innerHTML = `<div class="row" style="margin-bottom:12px"><div class="legend"><span><i style="background:var(--green)"></i>Healthy</span><span><i style="background:var(--amber)"></i>Risk 25–50%</span><span><i style="background:var(--red)"></i>Risk over 50% or down</span><span><i style="background:#8A9AAA"></i>Idle / maintenance</span></div><span class="spacer"></span>
      <select id="fType" style="width:auto"><option value="">All machine types</option>${[...new Set(App.cache.machines.map((m) => m.machine_type))].map((t) => `<option>${esc(t)}</option>`).join("")}</select></div><div class="floor" id="floor"></div>`;
    const draw = async () => {
      const [latest, machines, sim] = await Promise.all([API.get("/sensors/latest"), API.get("/machines"), API.get("/simulator/status")]);
      $("#simState").innerHTML = `<span class="pulse ${sim.running ? "" : "off"}"></span>${sim.running ? `Live · updated ${new Date().toLocaleTimeString("en-IN")}` : "Simulator stopped"}`;
      const L = Object.fromEntries(latest.map((r) => [r.machine_id, r]));
      const ft = $("#fType").value;
      const lines = {};
      machines.filter((m) => !ft || m.machine_type === ft).forEach((m) => (lines[m.line] = lines[m.line] || []).push(m));
      $("#floor").innerHTML = Object.keys(lines).sort().map((ln) => `<div class="line-row"><h3>${esc(ln)}</h3><div class="tiles">${lines[ln].map((m) => {
        const r = L[m.id] || {}; const lvl = riskLevel(m.failure_risk);
        return `<button class="tile s-${m.status} ${m.status === "running" ? "r-" + lvl : ""} ${r.is_anomaly && m.status === "running" ? "anom" : ""}" data-m="${m.id}" aria-label="${esc(m.code)} ${m.status}, risk ${fmt.risk(m.failure_risk)}">
          <span class="st">${m.status === "running" ? fmt.risk(m.failure_risk) : esc(m.status)}</span>
          <div class="code">${esc(m.code)}</div><div class="type">${r.is_anomaly && m.status === "running" ? `<b class="anom-label">Anomaly detected</b>` : esc(m.machine_type)}</div>
          <div class="vals" title="Temperature · vibration · air pressure"><span class="${r.temperature > m.temp_max ? "hot" : ""}">${r.temperature != null ? r.temperature.toFixed(0) : "–"}°C</span><span class="${r.vibration > m.vibration_max ? "hot" : ""}">${r.vibration != null ? r.vibration.toFixed(1) : "–"}mm/s</span><span class="${r.pressure < m.pressure_min ? "hot" : ""}">${r.pressure != null ? r.pressure.toFixed(1) : "–"}b</span></div>
          <div class="risk"><i style="width:${Math.max(2, m.failure_risk * 100)}%"></i></div></button>`;
      }).join("")}</div></div>`).join("");
      $$(".tile").forEach((t) => t.onclick = () => machineDrawer(+t.dataset.m));
    };
    $("#fType").onchange = draw;
    await draw();
    App.every(5000, () => { if (!document.querySelector(".overlay")) draw().catch(() => { }); });
  },

  /* ---------------- AI insights ---------------- */
  async insights(root) {
    App.setTitle("AI insights", "Failure prediction for every machine, model quality, and what drives the risk");
    App.actions(`<button class="btn ghost" id="refresh">Re-score all machines</button>`);
    const [fleet, models, sim] = await Promise.all([API.get("/ai/fleet-risk"), API.get("/ai/models"), API.get("/simulator/status")]);
    const fm = models.metrics.failure_model || {}, am = models.metrics.anomaly_model || {}, dm = models.metrics.defect_model || {};
    root.innerHTML = `<div class="grid g3">
      <div class="panel"><h2>Failure prediction</h2><p class="small muted">${esc(fm.algorithm)} on ${fmt.n(fm.train_rows)} synthetic AI4I-style samples. Predicts failure in the next 24 h and the failure mode.</p>
        <div class="specs"><div><span>ROC-AUC</span>${fm.roc_auc}</div><div><span>F1</span>${fm.f1}</div><div><span>Precision</span>${fm.precision}</div><div><span>Recall</span>${fm.recall}</div></div></div>
      <div class="panel"><h2>Anomaly detection</h2><p class="small muted">${esc(am.algorithm)}. Runs on every reading, raises alerts and auto-tickets for critical anomalies.</p>
        <div class="specs"><div><span>Detection rate</span>${fmt.pct(am.hybrid_detection_rate_on_failure_states * 100)}</div><div><span>False alarms</span>${fmt.pct(am.hybrid_false_positive_rate * 100)}</div></div></div>
      <div class="panel"><h2>Batch defect risk</h2><p class="small muted">${esc(dm.algorithm)} on sealing temperature, pressure, line speed, fill weight, humidity and vibration.</p>
        <div class="specs"><div><span>ROC-AUC</span>${dm.roc_auc}</div><div><span>Accuracy</span>${dm.accuracy}</div><div><span>F1</span>${dm.f1}</div></div></div></div>
      <div class="grid g-wide mt"><div class="panel flush"><h2>Fleet risk ranking</h2><div id="fleet"></div></div>
      <div><div class="panel"><h2>Feature importance</h2><div class="chart-box"><canvas id="cImp"></canvas></div></div>
        ${can("admin", "plant_head") ? `<div class="panel mt"><h2>Demo: inject a fault</h2><p class="small muted">Pushes a machine into degradation in the simulator so you can watch the anomaly detector and predictor react on the live floor.</p>
        <div class="field"><label>Machine</label><select id="iM">${App.machineOptions().map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select></div>
        <div class="field"><label>Fault</label><select id="iMode"><option value="bearing">Bearing wear (vibration)</option><option value="overheat">Heater overheating</option><option value="pneumatic">Air leak (pressure)</option><option value="tool_wear">Tool overstrain (torque)</option></select></div>
        <button class="btn" id="inject">Inject fault</button><p class="small muted mt">${sim.degrading_machines.length} machine(s) currently degrading in the simulator.</p></div>` : ""}</div></div>`;
    const cols = [{ label: "Machine", html: (r) => `<b>${esc(r.code)}</b><div class="small muted">${esc(r.type)}</div>` }, { label: "Status", html: (r) => chip(r.status) },
    { label: "Risk", html: (r) => `<div style="min-width:170px">${riskMeter(r.risk)}</div>` }, { label: "Likely mode", get: (r) => r.risk >= .25 ? r.mode || "–" : "–" },
    { label: "Drivers", get: (r) => r.risk >= .25 ? (r.factors || []).map((f) => `${f.label} ${f.direction}`).join(", ") || "–" : "Within normal range" }];
    $("#fleet").innerHTML = table(cols, fleet, { onRow: true }); bindRows($("#fleet"), fleet, (r) => machineDrawer(r.id));
    const imp = fm.feature_importance || {};
    Charts.make($("#cImp"), { type: "bar", data: { labels: Object.keys(imp).map(fmt.label), datasets: [{ data: Object.values(imp), backgroundColor: Charts.palette.teal }] }, options: { indexAxis: "y", plugins: { legend: { display: false } } } });
    $("#refresh").onclick = async () => { await API.get("/ai/fleet-risk?refresh=true"); toast("All machines re-scored"); App.route(); };
    const ib = $("#inject"); if (ib) ib.onclick = async () => { const r = await API.post("/simulator/inject", { machine_id: +$("#iM").value, mode: $("#iMode").value, severity: 0.8 }); toast(`Fault injected on ${r.machine}. Watch the live floor.`); };
  },

  /* ---------------- GenAI shift reports + OEE analytics ---------------- */
  async reports(root) {
    App.setTitle("Shift reports", "AI-written shift performance report from the computed KPIs, plus OEE breakdowns");
    const y = new Date(Date.now() - 864e5); y.setMinutes(y.getMinutes() - y.getTimezoneOffset());
    root.innerHTML = `<div class="grid g-wide"><div class="panel"><div class="row"><div class="field" style="margin:0"><label>Date</label><input type="date" id="rDate" value="${y.toISOString().slice(0, 10)}" max="${todayISO()}"></div>
      <div class="field" style="margin:0"><label>Shift</label><select id="rShift">${App.shiftOptions(true).map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select></div>
      <button class="btn" id="gen" style="align-self:flex-end">Generate report</button><button class="btn ghost" id="print" style="align-self:flex-end" hidden>Print</button></div>
      <div id="report" class="mt"><div class="empty">Pick a date and shift, then generate. The report uses only numbers computed from the logs.</div></div></div>
      <div class="panel"><h2>OEE breakdown</h2><div class="field"><label>Group by</label><select id="gBy"><option value="shift">Shift</option><option value="machine_type">Machine type</option><option value="line">Line</option><option value="operator">Operator</option><option value="machine">Machine</option></select></div><div id="oeeTbl"></div><p class="small muted">${"OEE = Availability × Performance × Quality, last 7 days."}</p></div></div>`;
    $("#gen").onclick = async () => {
      const b = $("#gen"); b.disabled = true; $("#report").innerHTML = '<div class="empty">Writing the report…</div>';
      try {
        const r = await API.post("/genai/shift-report", { report_date: $("#rDate").value, shift_id: $("#rShift").value ? +$("#rShift").value : null });
        $("#report").innerHTML = `<div class="ai-box"><div class="ai-head"><span>Shift performance report</span>${providerChip(r.provider)}</div>${md(r.report)}</div>`;
        $("#print").hidden = false;
      } catch (e) { $("#report").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
      b.disabled = false;
    };
    $("#print").onclick = () => { const w = window.open(""); w.document.write(`<title>Shift report</title><body style="font-family:sans-serif;max-width:760px;margin:30px auto">${$("#report .md").innerHTML}</body>`); w.print(); };
    const oee = async () => {
      const r = await API.get(`/analytics/oee?group_by=${$("#gBy").value}`);
      $("#oeeTbl").innerHTML = table([{ label: fmt.label($("#gBy").value), key: "group" }, { label: "OEE", n: true, get: (x) => fmt.pct(x.oee) }, { label: "A", n: true, get: (x) => fmt.pct(x.availability, 0) }, { label: "P", n: true, get: (x) => fmt.pct(x.performance, 0) }, { label: "Q", n: true, get: (x) => fmt.pct(x.quality, 0) }], r.rows);
    };
    $("#gBy").onchange = oee; await oee();
  },

  /* ---------------- production ---------------- */
  _prodModal(machineId, after) {
    formModal({
      title: "Log production", submit: "Save log",
      fields: [{ name: "machine_id", label: "Machine", type: "select", options: App.machineOptions(), value: machineId, numeric: true, required: true },
      { name: "shift_id", label: "Shift", type: "select", options: App.shiftOptions(), value: App.currentShiftId(), numeric: true, required: true },
      { name: "log_date", label: "Date", type: "date", value: todayISO(), max: todayISO(), required: true },
      { name: "batch_no", label: "Batch number", required: true, pattern: "[A-Za-z0-9_\\-/]+", placeholder: "B261031A-BP01" },
      { name: "product_name", label: "Product", required: true, full: true },
      { name: "planned_qty", label: "Planned quantity", type: "number", min: 0, required: true },
      { name: "produced_qty", label: "Produced quantity", type: "number", min: 0, required: true },
      { name: "rejected_qty", label: "Rejected quantity", type: "number", min: 0, value: 0, required: true },
      { name: "planned_time_min", label: "Planned time (min)", type: "number", min: 1, max: 720, value: 450, required: true },
      { name: "run_time_min", label: "Actual run time (min)", type: "number", min: 0, max: 720, required: true },
      { name: "ideal_cycle_sec", label: "Ideal cycle time (s/unit)", type: "number", step: 0.001, min: 0.001, value: 0.6, required: true, help: "3600 ÷ rated speed" },
      { name: "notes", label: "Notes", type: "textarea", full: true }],
      onSubmit: async (d) => {
        if (d.rejected_qty > d.produced_qty) throw new Error("Rejected quantity can't exceed produced quantity");
        if (d.run_time_min > d.planned_time_min) throw new Error("Run time can't exceed planned time");
        const r = await API.post("/production", d); toast(`Logged · OEE ${r.oee.oee}%`); after ? after() : App.route();
      },
    });
    const sel = $(".overlay #f_machine_id");
    const setCycle = () => { const m = App.cache.machines.find((x) => x.id === +sel.value); if (m) $(".overlay #f_ideal_cycle_sec").value = (3600 / m.rated_speed).toFixed(3); };
    sel.onchange = setCycle; setCycle();
  },
  async production(root) {
    App.setTitle("Production", "Shift-wise output with per-batch OEE");
    if (can("admin", "plant_head", "operator")) App.actions(`<button class="btn" id="add">Log production</button>`);
    root.innerHTML = `<div class="filters"><select id="pm">${App.machineOptions(true).map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select>
      <select id="ps">${App.shiftOptions(true).map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select><input type="date" id="pd" max="${todayISO()}"><input id="pb" placeholder="Batch number"></div><div class="panel flush" id="tbl"></div>`;
    const load = async () => {
      const rows = await API.get("/production" + API.qs({ machine_id: $("#pm").value, shift_id: $("#ps").value, start: $("#pd").value, end: $("#pd").value, batch_no: $("#pb").value, limit: 200 }));
      $("#tbl").innerHTML = table([{ label: "Date", key: "log_date" }, { label: "Shift", key: "shift_code" }, { label: "Machine", key: "machine_code" }, { label: "Batch", key: "batch_no" }, { label: "Product", key: "product_name" },
      { label: "Produced", n: true, get: (r) => fmt.n(r.produced_qty) }, { label: "Rejected", n: true, get: (r) => fmt.n(r.rejected_qty) }, { label: "Run min", n: true, key: "run_time_min" },
      { label: "OEE", n: true, html: (r) => `<b style="color:${r.oee.oee < 60 ? "var(--red)" : r.oee.oee < 75 ? "var(--amber)" : "var(--green)"}">${fmt.pct(r.oee.oee)}</b>` }, { label: "Operator", get: (r) => r.operator_name || "–" }], rows, { empty: "No production logged for these filters." });
    };
    $$(".filters select, .filters input", root).forEach((e) => e.onchange = load);
    const add = $("#add"); if (add) add.onclick = () => Views._prodModal(null, load);
    await load();
  },

  /* ---------------- sensors ---------------- */
  async sensors(root) {
    App.setTitle("Sensor readings", "Manual and live readings; every reading is scored by the anomaly detector");
    if (can("admin", "operator", "technician")) App.actions(`<button class="btn" id="add">Log reading</button>`);
    root.innerHTML = `<div class="filters"><select id="sm">${App.machineOptions(true).map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select>
      <label class="check"><input type="checkbox" id="sa" checked> Anomalies only</label><select id="ss"><option value="">All sources</option><option value="manual">Manual</option><option value="simulator">Simulator</option><option value="iot">IoT history</option></select></div><div class="panel flush" id="tbl"></div>`;
    const load = async () => {
      const rows = await API.get("/sensors" + API.qs({ machine_id: $("#sm").value, anomalies_only: $("#sa").checked, source: $("#ss").value, limit: 150 }));
      $("#tbl").innerHTML = table([{ label: "Time", get: (r) => fmt.dt(r.recorded_at) }, { label: "Machine", key: "machine_code" }, { label: "Temp °C", n: true, key: "temperature" }, { label: "Vib mm/s", n: true, key: "vibration" },
      { label: "Air bar", n: true, key: "pressure" }, { label: "rpm", n: true, key: "rpm" }, { label: "Torque", n: true, key: "torque" }, { label: "Wear", n: true, key: "tool_wear" },
      { label: "Assessment", html: (r) => r.is_anomaly ? `${chip("anomaly", "red")} <span class="small">${esc(r.anomaly_reason || "")}</span>` : chip("normal", "green") }, { label: "Source", key: "source" }], rows, { onRow: true, empty: "No readings match." });
      bindRows($("#tbl"), rows, (r) => machineDrawer(r.machine_id));
    };
    $$(".filters select, .filters input", root).forEach((e) => e.onchange = load);
    const add = $("#add"); if (add) add.onclick = () => readingModal(null, load);
    await load();
    App.every(10000, () => { if (!document.querySelector(".overlay")) load().catch(() => { }); });
  },

  /* ---------------- downtime ---------------- */
  async downtime(root) {
    App.setTitle("Downtime", "Every stop with its reason — the source of the availability loss");
    App.actions(`<button class="btn" id="add">Log downtime</button>`);
    root.innerHTML = `<div class="filters"><select id="dm">${App.machineOptions(true).map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select>
      <select id="dr"><option value="">All reasons</option>${["breakdown", "changeover", "material_shortage", "planned_maintenance", "power_failure", "quality_hold", "no_operator", "other"].map((r) => `<option value="${r}">${fmt.label(r)}</option>`).join("")}</select>
      <label class="check"><input type="checkbox" id="do"> Ongoing only</label></div><div class="panel flush" id="tbl"></div>`;
    const load = async () => {
      const rows = await API.get("/downtime" + API.qs({ machine_id: $("#dm").value, reason: $("#dr").value, ongoing: $("#do").checked || "", limit: 200 }));
      $("#tbl").innerHTML = table([{ label: "Start", get: (r) => fmt.dt(r.start_time) }, { label: "Machine", key: "machine_code" }, { label: "Reason", html: (r) => chip(r.reason_category, r.reason_category === "breakdown" ? "red" : "teal") },
      { label: "Detail", get: (r) => r.reason_detail || "" }, { label: "Minutes", n: true, html: (r) => r.duration_min != null ? fmt.n(r.duration_min) : `<b style="color:var(--red)">${fmt.n(r.ongoing_minutes)} · ongoing</b>` },
      { label: "Planned", get: (r) => r.is_planned ? "Yes" : "No" }, { label: "", html: (r) => r.end_time ? "" : `<button class="btn sm" data-close-dt="${r.id}">Machine restarted</button>` }], rows, { empty: "No downtime for these filters." });
      $$("[data-close-dt]").forEach((b) => b.onclick = async () => { await API.post(`/downtime/${b.dataset.closeDt}/close`, {}); toast("Downtime closed; machine set to running"); App.cache.machines = null; load(); });
    };
    $$(".filters select, .filters input", root).forEach((e) => e.onchange = load);
    $("#add").onclick = () => downtimeModal(null, load);
    await load();
  },

  /* ---------------- quality ---------------- */
  async quality(root) {
    App.setTitle("Quality", "Batch inspections, AI pre-batch defect risk, and root-cause analysis for failed batches");
    if (can("admin", "plant_head", "operator")) App.actions(`<button class="btn ghost" id="risk">Pre-batch risk check</button><button class="btn" id="add">Record inspection</button>`);
    root.innerHTML = `<div class="filters"><select id="qr"><option value="">All results</option><option value="fail">Fail</option><option value="rework">Rework</option><option value="pass">Pass</option></select>
      <select id="qm">${App.machineOptions(true).map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select><input id="qb" placeholder="Batch number"></div><div class="panel flush" id="tbl"></div>`;
    const load = async () => {
      const rows = await API.get("/quality" + API.qs({ result: $("#qr").value, machine_id: $("#qm").value, batch_no: $("#qb").value, limit: 200 }));
      $("#tbl").innerHTML = table([{ label: "Inspected", get: (r) => fmt.dt(r.inspected_at) }, { label: "Batch", key: "batch_no" }, { label: "Machine", key: "machine_code" },
      { label: "Sample", n: true, key: "sample_size" }, { label: "Defects", n: true, key: "defects_found" }, { label: "Type", get: (r) => fmt.label(r.defect_type) },
      { label: "Seal °C", n: true, get: (r) => r.seal_temp ?? "–" }, { label: "AI risk", n: true, html: (r) => r.ai_defect_risk != null ? `<span style="color:${r.ai_defect_risk >= .5 ? "var(--red)" : "inherit"}">${fmt.risk(r.ai_defect_risk)}</span>` : "–" },
      { label: "Result", html: (r) => chip(r.result) }, { label: "", html: (r) => r.result !== "pass" && can("admin", "plant_head", "operator") ? `<button class="btn ghost sm" data-rca="${r.id}">Root cause</button>` : "" }], rows, { empty: "No inspections match." });
      $$("[data-rca]").forEach((b) => b.onclick = () => Views._rca(+b.dataset.rca));
    };
    $$(".filters select, .filters input", root).forEach((e) => e.onchange = load);
    const procFields = [{ type: "section", label: "Process parameters at time of batch" },
    { name: "seal_temp", label: "Sealing temp (°C)", type: "number", step: 0.1, min: 0, max: 400, value: 150 },
    { name: "seal_pressure", label: "Sealing pressure (bar)", type: "number", step: 0.01, min: 0, max: 50, value: 4.5 },
    { name: "line_speed", label: "Line speed (strokes/min)", type: "number", step: 0.1, min: 0, max: 1000, value: 45 },
    { name: "fill_weight_dev", label: "Fill-weight deviation (%)", type: "number", step: 0.01, min: -100, max: 100, value: 0 },
    { name: "humidity", label: "Room humidity (%)", type: "number", step: 0.1, min: 0, max: 100, value: 45 }];
    const add = $("#add");
    if (add) add.onclick = () => formModal({
      title: "Record batch inspection", submit: "Save inspection",
      fields: [{ name: "batch_no", label: "Batch number", required: true, pattern: "[A-Za-z0-9_\\-/]+" }, { name: "machine_id", label: "Machine", type: "select", options: App.machineOptions(), numeric: true, required: true },
      { name: "sample_size", label: "Sample size", type: "number", min: 1, value: 200, required: true }, { name: "defects_found", label: "Defects found", type: "number", min: 0, value: 0, required: true },
      { name: "defect_type", label: "Main defect type", type: "select", options: ["none", "seal_leak", "misprint", "underfill", "overfill", "foreign_particle", "crack", "missing_tablet", "label_error"] },
      { name: "result", label: "Result", type: "select", options: [["", "Decide by AQL rule"], "pass", "rework", "fail"] }, ...procFields, { name: "notes", label: "Notes", type: "textarea", full: true }],
      onSubmit: async (d) => { if (d.defects_found > d.sample_size) throw new Error("Defects can't exceed sample size"); const r = await API.post("/quality", d); toast(`Batch ${r.batch_no}: ${r.result}`); load(); },
    });
    const rb = $("#risk");
    if (rb) rb.onclick = () => formModal({
      title: "Pre-batch defect risk check", submit: "Check risk",
      intro: '<p class="muted small">Before starting a batch, enter the planned settings. The model estimates the chance of a defective batch and flags out-of-spec parameters.</p>',
      fields: [{ name: "machine_id", label: "Machine", type: "select", options: App.machineOptions(), numeric: true, required: true, full: true }, ...procFields.slice(1)],
      onSubmit: async (d) => {
        const r = await API.post("/quality/predict", d);
        openOverlay(`<div class="panel-head"><h2>Defect risk for next batch</h2><button class="x" data-close>×</button></div>${riskMeter(r.defect_probability)}
          <p class="mt"><b>${esc(r.recommendation)}</b>${r.likely_defect_type !== "none" ? ` · likely defect: ${esc(fmt.label(r.likely_defect_type))}` : ""}</p>
          ${r.out_of_spec.length ? `<h3>Out of spec</h3>${table([{ label: "Parameter", key: "parameter" }, { label: "Value", n: true, key: "value" }, { label: "Optimal", key: "optimal" }, { label: "Action", key: "action" }], r.out_of_spec)}` : '<p class="muted">All parameters are inside the optimal window.</p>'}
          <div class="row mt"><span class="spacer"></span><button class="btn" data-close>Done</button></div>`);
      },
    });
    await load();
  },
  async _rca(id) {
    const ov = openOverlay(`<div class="drawer-head"><b>Root-cause analysis</b><button class="x" data-close>×</button></div><div class="drawer-body" id="rcaBody"><div class="empty">Analysing batch, process parameters and machine data…</div></div>`, { drawer: true });
    try {
      const r = await API.post(`/genai/quality-rca/${id}`);
      $("#rcaBody", ov).innerHTML = `<div class="ai-box"><div class="ai-head"><span>RCA & CAPA</span>${providerChip(r.provider)}</div>${md(r.analysis)}</div>
        <div class="panel mt"><h2>Model assessment</h2>${riskMeter(r.ml_assessment.defect_probability)}<p class="small mt">${esc(r.ml_assessment.recommendation)}</p></div>`;
    } catch (e) { $("#rcaBody", ov).innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  },

  /* ---------------- tickets ---------------- */
  async tickets(root) {
    App.setTitle(API.user.role === "operator" ? "Report an issue" : "Maintenance tickets", "Raise → assign → in progress → resolved → verified & closed");
    App.actions(`<button class="btn" id="add">Report issue</button>`);
    root.innerHTML = `<div class="filters"><select id="ts"><option value="">Open (any state)</option>${["open", "assigned", "in_progress", "on_hold", "resolved", "closed"].map((s) => `<option value="${s}">${fmt.label(s)}</option>`).join("")}<option value="all">All</option></select>
      <select id="tp"><option value="">All priorities</option>${["critical", "high", "medium", "low"].map((s) => `<option>${s}</option>`).join("")}</select>
      <select id="tm">${App.machineOptions(true).map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("")}</select><label class="check"><input type="checkbox" id="tmine"> ${API.user.role === "operator" ? "Raised by me" : "Mine"}</label></div><div class="panel flush" id="tbl"></div>`;
    const load = async () => {
      const s = $("#ts").value;
      const rows = await API.get("/tickets" + API.qs({ status: s === "all" ? "" : s, open_only: s === "" ? true : "", priority: $("#tp").value, machine_id: $("#tm").value, mine: $("#tmine").checked || "", limit: 200 }));
      $("#tbl").innerHTML = table([{ label: "Ticket", key: "ticket_no" }, { label: "Machine", key: "machine_code" }, { label: "Issue", html: (r) => `${esc(r.title)} ${r.source.startsWith("ai") ? chip("AI", "ai") : r.ai_triage ? chip("triaged", "ai") : ""}` },
      { label: "Priority", html: (r) => chip(r.priority) }, { label: "Status", html: (r) => chip(r.status) }, { label: "Assigned", get: (r) => r.assigned_to || "–" }, { label: "Raised", get: (r) => fmt.ago(r.created_at) }], rows, { onRow: true, empty: "No tickets match." });
      bindRows($("#tbl"), rows, (r) => ticketDrawer(r.id));
    };
    $$(".filters select, .filters input", root).forEach((e) => e.onchange = load);
    $("#add").onclick = () => newTicketModal(null);
    await load();
  },

  /* ---------------- spares ---------------- */
  async spares(root) {
    App.setTitle("Spare parts", "Inventory with automatic reorder alerts when stock falls to the reorder level");
    App.actions(`${can("admin", "plant_head", "technician") ? '<button class="btn ghost" id="po">Reorder report</button>' : ""}${isMgr() ? '<button class="btn" id="add">Add part</button>' : ""}`);
    root.innerHTML = `<div class="filters"><input id="sq" placeholder="Search part number or name"><label class="check"><input type="checkbox" id="sl"> Below reorder level</label></div><div class="panel flush" id="tbl"></div>`;
    const load = async () => {
      const rows = await API.get("/spares" + API.qs({ q: $("#sq").value, low_stock: $("#sl").checked || "" }));
      $("#tbl").innerHTML = table([{ label: "Part no.", key: "part_no" }, { label: "Name", key: "name" }, { label: "Category", key: "category" },
      { label: "In stock", n: true, html: (r) => `<b style="color:${r.needs_reorder ? "var(--red)" : "inherit"}">${r.quantity}</b>` }, { label: "Reorder at", n: true, key: "reorder_level" },
      { label: "Unit ₹", n: true, get: (r) => fmt.n(r.unit_cost) }, { label: "Location", key: "location" }, { label: "Supplier", key: "supplier" },
      { label: "", html: (r) => `${r.needs_reorder ? chip("reorder", "red") : ""} ${can("admin", "plant_head", "technician") ? `<button class="btn ghost sm" data-adj="${r.id}">Adjust</button>` : ""}` }], rows, { empty: "No parts found." });
      $$("[data-adj]").forEach((b) => b.onclick = () => formModal({
        title: "Adjust stock", submit: "Update stock", intro: '<p class="small muted">Positive for goods received, negative for write-off.</p>',
        fields: [{ name: "delta", label: "Change in quantity", type: "number", required: true, min: -100000, max: 100000 }, { name: "reason", label: "Reason / GRN number", required: true, maxlength: 200 }],
        onSubmit: async (d) => { if (!d.delta) throw new Error("Enter a non-zero quantity"); await API.post(`/spares/${b.dataset.adj}/adjust`, d); toast("Stock updated"); load(); },
      }));
    };
    $("#sq").oninput = () => { clearTimeout(this._t); this._t = setTimeout(load, 250); }; $("#sl").onchange = load;
    const po = $("#po");
    if (po) po.onclick = async () => {
      const r = await API.get("/spares/reorder-report");
      openOverlay(`<div class="panel-head"><h2>Reorder report</h2><button class="x" data-close>×</button></div><p class="small muted">${r.length} part(s) at or below reorder level · estimated ₹${fmt.n(r.reduce((s, x) => s + x.estimated_cost, 0))}</p>
        ${table([{ label: "Part", get: (x) => `${x.part_no} — ${x.name}` }, { label: "Stock", n: true, key: "quantity" }, { label: "Order", n: true, key: "suggested_order_qty" }, { label: "₹", n: true, get: (x) => fmt.n(x.estimated_cost) }, { label: "Supplier", key: "supplier" }, { label: "Lead", get: (x) => x.lead_time_days + " d" }, { label: "", html: (x) => chip(x.urgency, x.urgency === "critical" ? "red" : x.urgency === "high" ? "amber" : "teal") }], r, { empty: "Nothing to reorder." })}`);
    };
    const add = $("#add");
    if (add) add.onclick = () => formModal({
      title: "Add spare part", submit: "Add part",
      fields: [{ name: "part_no", label: "Part number", required: true, pattern: "[A-Za-z0-9_\\-/]+" }, { name: "name", label: "Name", required: true }, { name: "category", label: "Category", required: true },
      { name: "compatible_types", label: "Compatible machine types" }, { name: "quantity", label: "Opening stock", type: "number", min: 0, value: 0, required: true },
      { name: "reorder_level", label: "Reorder level", type: "number", min: 0, value: 5, required: true }, { name: "reorder_qty", label: "Reorder quantity", type: "number", min: 1, value: 10, required: true },
      { name: "unit_cost", label: "Unit cost (₹)", type: "number", min: 0, step: 0.01, value: 0, required: true }, { name: "location", label: "Store location" }, { name: "supplier", label: "Supplier" },
      { name: "lead_time_days", label: "Lead time (days)", type: "number", min: 0, value: 7, required: true }],
      onSubmit: async (d) => { await API.post("/spares", d); toast("Part added"); load(); },
    });
    await load();
  },

  /* ---------------- copilot (GenAI RAG) ---------------- */
  async copilot(root) {
    App.setTitle("Troubleshooting copilot", "Answers from the maintenance manuals, grounded in the machine's live readings and repair history");
    const [status, manuals] = await Promise.all([API.get("/genai/status"), API.get("/genai/manuals")]);
    const pre = JSON.parse(sessionStorage.getItem("copilot_q") || "null"); sessionStorage.removeItem("copilot_q");
    root.innerHTML = `<div class="grid g-wide"><div class="panel"><div class="row" style="margin-bottom:10px"><select id="cm" style="width:auto;min-width:240px">${App.machineOptions(true).map(([v, l]) => `<option value="${v}">${v ? esc(l) : "No specific machine"}</option>`).join("")}</select><span class="spacer"></span>${providerChip(status.provider)}</div>
      <div class="suggest">${["Vibration is above 7 mm/s, what should I check?", "Seal leak test failing on blister packs", "Air pressure keeps dropping below 4.5 bar", "How do I replace the main drive bearing?", "Sealing temperature alarm E02"].map((q) => `<button type="button">${q}</button>`).join("")}</div>
      <div class="chat" id="chat"><div class="msg bot">Pick the machine you're working on and describe the symptom. I'll use its live readings, past repairs and the manuals to suggest causes and steps, with sources.</div></div>
      <form id="ask" class="row mt"><input id="q" placeholder="Describe the problem or ask a question" maxlength="2000" style="flex:1" required><button class="btn">Ask</button></form></div>
      <div class="panel"><h2>Manual library</h2><p class="small muted">Indexed for retrieval (TF-IDF). Answers cite the section used.</p>${manuals.map((m) => `<div class="factor"><span>${esc(m.title)}</span><span class="muted small">${m.sections} sections</span></div>`).join("")}
      ${status.live ? "" : `<p class="small mt">${esc(status.note)}</p>`}</div></div>`;
    const history = [];
    const chat = $("#chat");
    const ask = async (q) => {
      chat.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(q)}</div><div class="msg bot muted" id="typing">Looking through the manuals…</div>`);
      chat.scrollTop = chat.scrollHeight;
      try {
        const r = await API.post("/genai/troubleshoot", { question: q, machine_id: $("#cm").value ? +$("#cm").value : null, history: history.slice(-6) });
        $("#typing").remove();
        chat.insertAdjacentHTML("beforeend", `<div class="msg bot">${md(r.answer)}${r.sources.length ? `<div class="src">Sources: ${r.sources.map((s) => `${esc(s.manual)} § ${esc(s.section)}`).join("; ")}</div>` : ""}</div>`);
        history.push({ role: "user", content: q }, { role: "assistant", content: r.answer });
      } catch (e) { $("#typing").outerHTML = `<div class="msg bot err">${esc(e.message)}</div>`; }
      chat.scrollTop = chat.scrollHeight;
    };
    $("#ask").onsubmit = (e) => { e.preventDefault(); const q = $("#q").value.trim(); if (q.length >= 3) { $("#q").value = ""; ask(q); } };
    $$(".suggest button").forEach((b) => b.onclick = () => ask(b.textContent));
    if (pre) { $("#cm").value = pre.m; ask(pre.q); }
  },

  /* ---------------- machines registry ---------------- */
  async machines(root) {
    App.setTitle("Machines", "Registry of all 40 machines with specifications, health and maintenance history");
    if (can("admin")) App.actions(`<button class="btn" id="add">Add machine</button>`);
    const machines = await API.get("/machines");
    root.innerHTML = `<div class="filters"><input id="mq" placeholder="Search code or name"><select id="mt"><option value="">All types</option>${[...new Set(machines.map((m) => m.machine_type))].map((t) => `<option>${esc(t)}</option>`).join("")}</select>
      <select id="mst"><option value="">All statuses</option>${["running", "idle", "down", "maintenance"].map((s) => `<option>${s}</option>`).join("")}</select></div><div class="panel flush" id="tbl"></div>`;
    const draw = () => {
      const q = $("#mq").value.toLowerCase();
      const rows = machines.filter((m) => (!q || (m.code + m.name).toLowerCase().includes(q)) && (!$("#mt").value || m.machine_type === $("#mt").value) && (!$("#mst").value || m.status === $("#mst").value));
      $("#tbl").innerHTML = table([{ label: "Code", html: (r) => `<b>${esc(r.code)}</b>` }, { label: "Name", key: "name" }, { label: "Type", key: "machine_type" }, { label: "Line", key: "line" }, { label: "Crit.", key: "criticality" },
      { label: "Status", html: (r) => chip(r.status) }, { label: "Health", n: true, get: (r) => fmt.n(r.health_score) }, { label: "Risk", html: (r) => `<div style="min-width:150px">${riskMeter(r.failure_risk)}</div>` },
      { label: "Next PM", html: (r) => `${fmt.d(r.next_pm_date)} ${r.pm_overdue ? chip("overdue", "red") : ""}` }, { label: "Open tickets", n: true, key: "open_tickets" },
      ...(can("admin") ? [{ label: "", html: (r) => `<button class="btn ghost sm" data-edit="${r.id}">Edit</button>` }] : [])], rows, { onRow: true });
      bindRows($("#tbl"), rows, (r) => machineDrawer(r.id));
      $$("[data-edit]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); Views._machineForm(machines.find((m) => m.id === +b.dataset.edit)); });
    };
    $("#mq").oninput = draw; $("#mt").onchange = draw; $("#mst").onchange = draw; draw();
    const add = $("#add"); if (add) add.onclick = () => Views._machineForm(null);
  },
  machine(root, [id]) { App.setTitle("Machine"); machineDrawer(+id); location.hash = "#/machines"; },
  _machineForm(m) {
    const v = (k, d) => m ? m[k] : d;
    formModal({
      title: m ? `Edit ${m.code}` : "Add machine", submit: m ? "Save changes" : "Add machine",
      fields: [...(m ? [] : [{ name: "code", label: "Code", required: true, pattern: "[A-Za-z0-9_\\-/]+", placeholder: "BP-11" }]),
      { name: "name", label: "Name", required: true, value: v("name", "") }, { name: "machine_type", label: "Type", type: "select", options: ["Blister Packer", "Cartoner", "Strip Packer", "Labeller", "Filler-Capper", "Checkweigher"], value: v("machine_type") },
      { name: "manufacturer", label: "Manufacturer", value: v("manufacturer", "") }, { name: "model", label: "Model", value: v("model", "") }, { name: "serial_no", label: "Serial no.", value: v("serial_no", "") },
      { name: "line", label: "Line", value: v("line", "Line-1") }, { name: "criticality", label: "Criticality", type: "select", options: ["A", "B", "C"], value: v("criticality", "B") },
      { name: "rated_speed", label: "Rated speed (units/h)", type: "number", min: 1, value: v("rated_speed", 6000), required: true }, { name: "power_kw", label: "Power (kW)", type: "number", min: 0, step: 0.1, value: v("power_kw", "") },
      { name: "pm_interval_days", label: "PM interval (days)", type: "number", min: 1, max: 365, value: v("pm_interval_days", 30), required: true },
      { name: "temp_max", label: "Temperature limit (°C)", type: "number", step: 0.1, value: v("temp_max", 75), required: true }, { name: "vibration_max", label: "Vibration limit (mm/s)", type: "number", step: 0.1, value: v("vibration_max", 7.1), required: true },
      { name: "pressure_min", label: "Min air pressure (bar)", type: "number", step: 0.1, value: v("pressure_min", 4.5), required: true }, { name: "pressure_max", label: "Max air pressure (bar)", type: "number", step: 0.1, value: v("pressure_max", 7.0), required: true }],
      onSubmit: async (d) => {
        if (d.pressure_min >= d.pressure_max) throw new Error("Min pressure must be below max pressure");
        m ? await API.patch(`/machines/${m.id}`, d) : await API.post("/machines", d);
        toast(m ? "Machine updated" : "Machine added"); App.cache.machines = null; App.route();
      },
    });
  },

  /* ---------------- roster ---------------- */
  async roster(root) {
    App.setTitle("Shifts & roster", "Three shifts; who works where on each day");
    App.actions(`<button class="btn" id="add">Assign operator</button>${can("admin") ? '<button class="btn ghost" id="addShift">Add shift</button>' : ""}`);
    root.innerHTML = `<div class="grid g3" id="shifts"></div><div class="filters mt"><input type="date" id="rd" value="${todayISO()}"></div><div class="panel flush" id="tbl"></div>`;
    $("#shifts").innerHTML = App.cache.shifts.map((s) => `<div class="panel"><div class="panel-head"><h2>Shift ${esc(s.code)} · ${esc(s.name)}</h2>${can("admin") ? `<button class="btn ghost sm" data-es="${s.id}">Edit</button>` : ""}</div><p class="num" style="font-size:20px;margin:0">${s.start_time} – ${s.end_time}</p><p class="small muted">Supervisor: ${esc(s.supervisor_name || "–")}</p></div>`).join("");
    const users = await API.get("/users?active=true");
    const shiftForm = (s) => formModal({
      title: s ? `Edit shift ${s.code}` : "Add shift", fields: [{ name: "code", label: "Code", required: true, value: s?.code || "", maxlength: 5 }, { name: "name", label: "Name", required: true, value: s?.name || "" },
      { name: "start_time", label: "Start", type: "time", required: true, value: s?.start_time || "" }, { name: "end_time", label: "End", type: "time", required: true, value: s?.end_time || "" },
      { name: "supervisor_id", label: "Supervisor", type: "select", numeric: true, value: s?.supervisor_id, options: [["", "None"], ...users.filter((u) => u.role !== "operator").map((u) => [u.id, u.full_name])] }],
      onSubmit: async (d) => { s ? await API.put(`/shifts/${s.id}`, d) : await API.post("/shifts", d); toast("Shift saved"); App.cache.shifts = null; App.cache.machines = null; App.route(); },
    });
    $$("[data-es]").forEach((b) => b.onclick = () => shiftForm(App.cache.shifts.find((s) => s.id === +b.dataset.es)));
    const as = $("#addShift"); if (as) as.onclick = () => shiftForm(null);
    const load = async () => {
      const rows = await API.get(`/roster?work_date=${$("#rd").value}`);
      $("#tbl").innerHTML = table([{ label: "Name", key: "user_name" }, { label: "Role", get: (r) => fmt.label(r.role) }, { label: "Shift", key: "shift_code" }, { label: "Machine", get: (r) => r.machine_code || "–" }, { label: "", html: (r) => `<button class="btn ghost sm" data-del="${r.id}">Remove</button>` }],
        rows.sort((a, b) => a.shift_code.localeCompare(b.shift_code)), { empty: "Nobody rostered on this date." });
      $$("[data-del]").forEach((b) => b.onclick = async () => { await API.del(`/roster/${b.dataset.del}`); load(); });
    };
    $("#rd").onchange = load;
    $("#add").onclick = () => formModal({
      title: "Assign to shift", submit: "Assign",
      fields: [{ name: "user_id", label: "Person", type: "select", numeric: true, required: true, options: users.filter((u) => ["operator", "technician"].includes(u.role)).map((u) => [u.id, `${u.full_name} (${fmt.label(u.role)})`]) },
      { name: "work_date", label: "Date", type: "date", value: $("#rd").value, required: true }, { name: "shift_id", label: "Shift", type: "select", numeric: true, options: App.shiftOptions(), required: true },
      { name: "machine_id", label: "Machine", type: "select", numeric: true, options: [["", "None"], ...App.machineOptions()] }],
      onSubmit: async (d) => { await API.post("/roster", d); toast("Assigned"); load(); },
    });
    await load();
  },

  /* ---------------- users (admin) ---------------- */
  async users(root) {
    App.setTitle("Users", "Accounts and roles");
    App.actions(`<button class="btn" id="add">Add user</button>`);
    const load = async () => {
      const rows = await API.get("/users");
      root.innerHTML = `<div class="panel flush">${table([{ label: "Name", key: "full_name" }, { label: "Username", key: "username" }, { label: "Role", get: (r) => ROLE_NAME[r.role] }, { label: "Email", key: "email" },
      { label: "Last sign-in", get: (r) => fmt.dt(r.last_login) }, { label: "Status", html: (r) => r.is_active ? chip("active", "green") : chip("disabled", "red") }, { label: "", html: (r) => `<button class="btn ghost sm" data-u="${r.id}">Edit</button>` }], rows)}</div>`;
      $$("[data-u]").forEach((b) => b.onclick = () => {
        const u = rows.find((x) => x.id === +b.dataset.u);
        formModal({
          title: `Edit ${u.username}`, fields: [{ name: "full_name", label: "Full name", required: true, value: u.full_name }, { name: "email", label: "Email", type: "email", value: u.email },
          { name: "role", label: "Role", type: "select", options: Object.entries(ROLE_NAME), value: u.role }, { name: "is_active", label: "Account active", type: "checkbox", value: u.is_active },
          { name: "password", label: "Reset password", type: "password", help: "Leave blank to keep current password" }],
          onSubmit: async (d) => { await API.patch(`/users/${u.id}`, d); toast("User updated"); load(); },
        });
      });
    };
    $("#add").onclick = () => formModal({
      title: "Add user", submit: "Create user",
      fields: [{ name: "full_name", label: "Full name", required: true }, { name: "username", label: "Username", required: true, pattern: "[a-zA-Z0-9_.]{3,50}" }, { name: "email", label: "Email", type: "email" },
      { name: "phone", label: "Phone" }, { name: "role", label: "Role", type: "select", options: Object.entries(ROLE_NAME), value: "operator" },
      { name: "password", label: "Password", type: "password", required: true, help: "8+ characters with letters and numbers" }],
      onSubmit: async (d) => { await API.post("/users", d); toast("User created"); load(); },
    });
    await load();
  },

  /* ---------------- settings & simulator (admin) ---------------- */
  async settings(root) {
    App.setTitle("Settings & simulator", "Automation thresholds, the real-time sensor simulator and model retraining");
    const [settings, sim, gs] = await Promise.all([API.get("/settings"), API.get("/simulator/status"), API.get("/genai/status")]);
    root.innerHTML = `<div class="grid g2"><div class="panel"><h2>Automation settings</h2>${settings.map((s) => `<div class="field"><label>${esc(s.description)}</label><div class="row">${["true", "false"].includes(s.value) ? `<select data-k="${s.key}" style="flex:1"><option value="true" ${s.value === "true" ? "selected" : ""}>On</option><option value="false" ${s.value === "false" ? "selected" : ""}>Off</option></select>` : `<input value="${esc(s.value)}" data-k="${s.key}" style="flex:1">`}<button class="btn ghost sm" data-save="${s.key}">Save</button></div></div>`).join("")}</div>
      <div><div class="panel"><h2>Real-time sensor simulator</h2><p><span class="pulse ${sim.running ? "" : "off"}"></span>${sim.running ? "Running" : "Stopped"} · every ${sim.interval_sec}s · ${fmt.n(sim.readings_generated)} readings generated</p>
        <p class="small muted">${sim.degrading_machines.length} machine(s) degrading: ${sim.degrading_machines.map((d) => `${App.cache.machines.find((m) => m.id === d.machine_id)?.code} (${d.mode}, ${Math.round(d.severity * 100)}%)`).join(", ") || "none"}</p>
        <div class="row"><button class="btn" id="simT">${sim.running ? "Stop simulator" : "Start simulator"}</button><input type="number" id="simI" value="${sim.interval_sec}" min="1" max="300" style="width:90px"><button class="btn ghost" id="simS">Set interval (s)</button></div></div>
      <div class="panel mt"><h2>Models</h2><p class="small muted">Retrains failure, anomaly and defect models on fresh synthetic data (about 15 s).</p><button class="btn ghost" id="retrain">Retrain models</button></div>
      <div class="panel mt"><h2>GenAI provider</h2><p>${providerChip(gs.provider)}</p><p class="small muted">${esc(gs.note)}</p></div></div></div>`;
    $$("[data-save]").forEach((b) => b.onclick = async () => { try { await API.put(`/settings/${b.dataset.save}`, { value: $(`[data-k="${b.dataset.save}"]`).value }); toast("Setting saved"); } catch (e) { toast(e.message, "error"); } });
    $("#simT").onclick = async () => { await API.post(sim.running ? "/simulator/stop" : "/simulator/start"); App.route(); };
    $("#simS").onclick = async () => { await API.post("/simulator/interval", { interval_sec: +$("#simI").value }); toast("Interval updated"); App.route(); };
    $("#retrain").onclick = async (e) => { e.target.disabled = true; e.target.textContent = "Retraining…"; const r = await API.post("/ai/retrain"); toast(`Retrained · failure AUC ${r.metrics.failure_model.roc_auc}`); App.route(); };
  },

  async audit(root) {
    App.setTitle("Audit log", "Who changed what, and when");
    const rows = await API.get("/audit-logs?limit=300");
    root.innerHTML = `<div class="panel flush">${table([{ label: "Time", get: (r) => fmt.dt(r.created_at) }, { label: "User", get: (r) => r.username || "system" }, { label: "Action", key: "action" }, { label: "Entity", get: (r) => `${r.entity || ""} ${r.entity_id || ""}` }, { label: "Detail", get: (r) => (r.detail || "").slice(0, 120) }], rows)}</div>`;
  },
};
