/* App shell: auth, role-based navigation, hash router, shared lookups */
const NAV = {
  admin: [
    ["Overview", [["dashboard", "Plant dashboard"], ["floor", "Live floor"], ["insights", "AI insights"], ["reports", "Shift reports"]]],
    ["Operations", [["production", "Production"], ["sensors", "Sensor readings"], ["downtime", "Downtime"], ["quality", "Quality"]]],
    ["Maintenance", [["tickets", "Tickets"], ["spares", "Spare parts"], ["copilot", "Troubleshooting copilot"], ["machines", "Machines"]]],
    ["Administration", [["roster", "Shifts & roster"], ["users", "Users"], ["settings", "Settings & simulator"], ["audit", "Audit log"]]],
  ],
  plant_head: [
    ["Overview", [["dashboard", "Plant dashboard"], ["floor", "Live floor"], ["insights", "AI insights"], ["reports", "Shift reports"]]],
    ["Operations", [["production", "Production"], ["downtime", "Downtime"], ["quality", "Quality"], ["sensors", "Sensor readings"]]],
    ["Maintenance", [["tickets", "Tickets"], ["spares", "Spare parts"], ["machines", "Machines"], ["copilot", "Troubleshooting copilot"]]],
    ["People", [["roster", "Shifts & roster"]]],
  ],
  technician: [
    ["My work", [["home", "My work"], ["tickets", "Tickets"], ["copilot", "Troubleshooting copilot"]]],
    ["Plant", [["floor", "Live floor"], ["insights", "AI insights"], ["machines", "Machines"], ["sensors", "Sensor readings"], ["spares", "Spare parts"], ["downtime", "Downtime"]]],
  ],
  operator: [
    ["My shift", [["home", "My shift"], ["production", "Production log"], ["sensors", "Sensor readings"], ["quality", "Quality checks"], ["downtime", "Downtime"]]],
    ["Help", [["tickets", "Report an issue"], ["copilot", "Troubleshooting copilot"], ["floor", "Live floor"]]],
  ],
};
const ROLE_NAME = { admin: "Administrator", plant_head: "Plant head", technician: "Maintenance technician", operator: "Machine operator" };

const App = {
  cache: {},
  timers: [],
  async lookups(force = false) {
    if (this.cache.machines && !force) return this.cache;
    const [machines, shifts] = await Promise.all([API.get("/machines"), API.get("/shifts")]);
    this.cache.machines = machines; this.cache.shifts = shifts;
    if (API.user.role !== "operator") this.cache.techs = await API.get("/users?role=technician&active=true").catch(() => []);
    return this.cache;
  },
  machineOptions(withBlank = false) {
    const o = (this.cache.machines || []).map((m) => [m.id, `${m.code} — ${m.name}`]);
    return withBlank ? [["", "All machines"], ...o] : o;
  },
  shiftOptions(withBlank = false) {
    const o = (this.cache.shifts || []).map((s) => [s.id, `Shift ${s.code} (${s.start_time}–${s.end_time})`]);
    return withBlank ? [["", "All shifts"], ...o] : o;
  },
  currentShiftId() {
    const now = new Date(); const t = now.getHours() * 60 + now.getMinutes();
    const s = (this.cache.shifts || []).find((s) => {
      const [a, b] = [s.start_time, s.end_time].map((x) => +x.slice(0, 2) * 60 + +x.slice(3));
      return a < b ? t >= a && t < b : t >= a || t < b;
    });
    return s ? s.id : (this.cache.shifts || [{}])[0].id;
  },
  every(ms, fn) { this.timers.push(setInterval(fn, ms)); },
  clearTimers() { this.timers.forEach(clearInterval); this.timers = []; },

  render() {
    this.clearTimers(); Charts.destroyAll();
    if (!API.token) return this.renderLogin();
    if (!$(".shell")) this.renderShell();
    this.route();
  },

  renderLogin() {
    const tiles = Array.from({ length: 40 }, (_, i) => `<i class="${[3, 17].includes(i) ? "c" : [8, 22, 31].includes(i) ? "w" : i % 7 === 0 ? "g" : ""}"></i>`).join("");
    $("#app").innerHTML = `<div class="login">
      <section class="login-art">
        <div class="brand">FactoryGuard AI</div>
        <div><h1>Know which machine will stop before it does.</h1>
        <p>Predictive maintenance, quality and shift performance for the 40-machine packaging floor — readings, tickets and batches in one place, with AI watching the trends.</p>
        <div class="mini-floor" aria-hidden="true">${tiles}</div></div>
        <div class="small" style="color:#71879C">Team 17 · Neural Stack Knights · Synthetic demo data</div>
      </section>
      <section class="login-form"><form id="loginForm">
        <h1 style="margin-bottom:18px">Sign in</h1>
        <div class="field"><label for="u">Username</label><input id="u" autocomplete="username" required></div>
        <div class="field"><label for="p">Password</label><input id="p" type="password" autocomplete="current-password" required></div>
        <button class="btn block" type="submit">Sign in</button>
        <div class="err" hidden></div>
        <div class="demo-users"><div class="muted" style="margin-bottom:6px">Demo accounts — select to fill in</div>
          ${[["admin", "Admin@123", "Administrator"], ["planthead", "Plant@123", "Plant head"], ["tech1", "Tech@123", "Technician"], ["op01", "Oper@123", "Operator"]]
        .map(([u, p, r]) => `<button type="button" data-u="${u}" data-p="${p}"><b>${u}</b><span>${r}</span></button>`).join("")}
        </div></form></section></div>`;
    $$(".demo-users button").forEach((b) => b.onclick = () => { $("#u").value = b.dataset.u; $("#p").value = b.dataset.p; });
    $("#loginForm").onsubmit = async (e) => {
      e.preventDefault();
      const err = $("#loginForm .err"); err.hidden = true;
      try { await API.login($("#u").value.trim(), $("#p").value); location.hash = ""; this.render(); }
      catch (ex) { err.textContent = ex.message; err.hidden = false; }
    };
  },

  renderShell() {
    const u = API.user;
    const nav = NAV[u.role].map(([g, items]) => `<div class="grp">${g}</div>` + items.map(([k, l]) =>
      `<a href="#/${k}" data-k="${k}">${l}${k === "tickets" ? '<span class="badge" id="tkBadge" hidden></span>' : ""}</a>`).join("")).join("");
    $("#app").innerHTML = `<div class="shell">
      <aside class="side" id="side"><div class="brand">FactoryGuard AI<small>Vizag packaging unit</small></div>
        <nav>${nav}</nav>
        <div class="me"><b>${esc(u.full_name)}</b>${ROLE_NAME[u.role]}<div class="row"><button class="btn ghost sm" id="pwBtn">Change password</button><button class="btn ghost sm" id="logout">Sign out</button></div></div>
      </aside>
      <main><div class="top"><div class="row"><button class="btn ghost sm menu-btn" id="menuBtn" aria-label="Menu">Menu</button><div><h1 id="title"></h1><div class="sub" id="subtitle"></div></div></div><div class="actions" id="actions"></div></div>
      <div class="content" id="view"></div></main></div>`;
    $("#logout").onclick = () => API.logout();
    $("#menuBtn").onclick = () => $("#side").classList.toggle("open");
    $("#pwBtn").onclick = () => formModal({
      title: "Change password", submit: "Update password",
      fields: [{ name: "current_password", label: "Current password", type: "password", required: true },
      { name: "new_password", label: "New password", type: "password", required: true, help: "8+ characters with letters and numbers" }],
      onSubmit: async (d) => { await API.post("/auth/change-password", d); toast("Password updated"); },
    });
    window.addEventListener("hashchange", () => this.render());
  },

  async route() {
    const role = API.user.role;
    const parts = location.hash.replace(/^#\/?/, "").split("/");
    let key = parts[0] || (["admin", "plant_head"].includes(role) ? "dashboard" : "home");
    const allowed = NAV[role].flatMap(([, i]) => i.map(([k]) => k)).concat(["machine"]);
    if (!allowed.includes(key)) key = allowed[0];
    $$(".side nav a").forEach((a) => a.classList.toggle("on", a.dataset.k === key || (key === "machine" && a.dataset.k === "machines")));
    $("#side").classList.remove("open");
    const view = $("#view");
    $("#actions").innerHTML = "";
    view.innerHTML = '<div class="empty">Loading…</div>';
    try {
      await this.lookups();
      await Views[key](view, parts.slice(1));
    } catch (e) {
      view.innerHTML = `<div class="panel empty">Couldn't load this page: ${esc(e.message)}</div>`;
    }
    this.refreshBadge();
  },
  setTitle(t, sub = "") { $("#title").textContent = t; $("#subtitle").textContent = sub; document.title = `${t} · FactoryGuard AI`; },
  actions(html) { $("#actions").innerHTML = html; },
  async refreshBadge() {
    try {
      const n = (await API.get("/tickets?open_only=true&limit=500" + (["technician", "operator"].includes(API.user.role) ? "&mine=true" : ""))).length;
      const b = $("#tkBadge"); if (b) { b.hidden = !n; b.textContent = n; }
    } catch (_) { }
  },
};

window.addEventListener("DOMContentLoaded", () => App.render());
