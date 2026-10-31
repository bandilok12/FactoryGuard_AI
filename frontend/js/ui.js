/* UI helpers: escaping, formatting, modal/drawer, toasts, markdown, charts */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const fmt = {
  n: (v, d = 0) => v == null ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }),
  pct: (v, d = 1) => v == null ? "–" : `${Number(v).toFixed(d)}%`,
  dt: (s) => s ? new Date(s).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "–",
  d: (s) => s ? new Date(s).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "–",
  ago(s) {
    if (!s) return "–";
    const m = Math.round((Date.now() - new Date(s).getTime()) / 60000);
    if (m < 1) return "just now"; if (m < 60) return `${m} min ago`;
    const h = Math.round(m / 60); if (h < 48) return `${h} h ago`; return `${Math.round(h / 24)} d ago`;
  },
  label: (s) => String(s ?? "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()),
  risk: (p) => `${Math.round((p || 0) * 100)}%`,
};
const todayISO = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };
const nowLocalISO = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };
const riskLevel = (p) => p >= 0.75 ? "critical" : p >= 0.5 ? "high" : p >= 0.25 ? "medium" : "low";
const chip = (v, cls) => `<span class="chip ${esc(cls || v)}">${esc(fmt.label(v))}</span>`;

function toast(msg, type = "") {
  const el = document.createElement("div");
  el.className = "toast " + type; el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), type === "error" ? 6000 : 3500);
}

/* modal (centered form) and drawer (side detail) */
function openOverlay(html, { drawer = false, onClose } = {}) {
  const ov = document.createElement("div");
  ov.className = "overlay" + (drawer ? "" : " center");
  ov.innerHTML = drawer ? `<div class="drawer" role="dialog" aria-modal="true">${html}</div>` : `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
  const close = () => { ov.remove(); document.removeEventListener("keydown", onKey); onClose && onClose(); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  ov.addEventListener("mousedown", (e) => { if (e.target === ov) close(); });
  document.addEventListener("keydown", onKey);
  document.body.appendChild(ov);
  $$("[data-close]", ov).forEach((b) => b.onclick = close);
  const f = $("input, select, textarea, button", ov); f && f.focus();
  ov.close = close;
  return ov;
}

/* Build a form from a field spec; resolves submit via handler */
function formModal({ title, fields, submit = "Save", onSubmit, intro = "" }) {
  const body = fields.map((f) => {
    if (f.type === "section") return `<h3>${esc(f.label)}</h3>`;
    const id = "f_" + f.name;
    const req = f.required ? "required" : "";
    const attrs = `id="${id}" name="${f.name}" ${req} ${f.min != null ? `min="${f.min}"` : ""} ${f.max != null ? `max="${f.max}"` : ""} ${f.step ? `step="${f.step}"` : ""} ${f.pattern ? `pattern="${f.pattern}"` : ""} ${f.maxlength ? `maxlength="${f.maxlength}"` : ""} placeholder="${esc(f.placeholder || "")}"`;
    let input;
    if (f.type === "select") input = `<select ${attrs}>${(f.options || []).map((o) => { const [v, l] = Array.isArray(o) ? o : [o, fmt.label(o)]; return `<option value="${esc(v)}" ${String(f.value) === String(v) ? "selected" : ""}>${esc(l)}</option>`; }).join("")}</select>`;
    else if (f.type === "textarea") input = `<textarea ${attrs}>${esc(f.value || "")}</textarea>`;
    else if (f.type === "checkbox") return `<div class="field"><label class="check"><input type="checkbox" id="${id}" name="${f.name}" ${f.value ? "checked" : ""}> ${esc(f.label)}</label></div>`;
    else input = `<input type="${f.type || "text"}" ${attrs} value="${esc(f.value ?? "")}">`;
    return `<div class="field" ${f.full ? 'style="grid-column:1/-1"' : ""}><label for="${id}">${esc(f.label)}${f.required ? "" : ' <span class="muted">(optional)</span>'}</label>${input}${f.help ? `<div class="small muted">${esc(f.help)}</div>` : ""}</div>`;
  }).join("");
  const ov = openOverlay(`<div class="panel-head"><h2>${esc(title)}</h2><button class="x" data-close aria-label="Close">×</button></div>
    ${intro}<form novalidate><div class="fields">${body}</div><div class="err" hidden></div>
    <div class="row mt"><span class="spacer"></span><button type="button" class="btn ghost" data-close>Cancel</button><button class="btn" type="submit">${esc(submit)}</button></div></form>`);
  const form = $("form", ov);
  form.onsubmit = async (e) => {
    e.preventDefault();
    if (!form.checkValidity()) { form.reportValidity(); return; }
    const data = {};
    fields.forEach((f) => {
      if (f.type === "section") return;
      const el = form.elements[f.name];
      let v = f.type === "checkbox" ? el.checked : el.value;
      if (v === "" && f.type !== "checkbox") { v = null; }
      else if (f.type === "number") v = Number(v);
      else if (f.type === "select" && f.numeric) v = v === null ? null : Number(v);
      if (v !== null || f.keepNull) data[f.name] = v;
    });
    const btn = $("button[type=submit]", form); btn.disabled = true;
    const err = $(".err", form); err.hidden = true;
    try { await onSubmit(data, ov); ov.close(); }
    catch (ex) { err.textContent = ex.message; err.hidden = false; }
    finally { btn.disabled = false; }
  };
  return ov;
}

/* Minimal, safe markdown renderer for GenAI output */
function md(src) {
  const lines = esc(src || "").split("\n");
  let out = "", list = null;
  const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[^*])\*(?!\s)(.+?)\*/g, "$1<i>$2</i>")
    .replace(/_(.+?)_/g, "<i>$1</i>").replace(/`(.+?)`/g, "<code>$1</code>");
  const close = () => { if (list) { out += `</${list}>`; list = null; } };
  for (const raw of lines) {
    const l = raw.trimEnd();
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)/))) { close(); const n = Math.min(3, m[1].length); out += `<h${n}>${inline(m[2])}</h${n}>`; }
    else if ((m = l.match(/^\s*[-*•]\s+(.*)/))) { if (list !== "ul") { close(); out += "<ul>"; list = "ul"; } out += `<li>${inline(m[1])}</li>`; }
    else if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== "ol") { close(); out += "<ol>"; list = "ol"; } out += `<li>${inline(m[1])}</li>`; }
    else if (!l.trim()) { close(); }
    else { close(); out += `<p>${inline(l)}</p>`; }
  }
  close();
  return `<div class="md">${out}</div>`;
}

const providerChip = (p) => `<span class="chip ${String(p).startsWith("offline") ? "" : "ai"}">${String(p).startsWith("offline") ? "Offline engine" : "LLM · " + esc(p)}</span>`;

/* Charts (Chart.js); degrades to a note if CDN unavailable */
const Charts = {
  list: [],
  palette: { teal: "#0E7A80", ink: "#15283B", amber: "#D9971A", red: "#BF3A30", green: "#2F8A5B", grey: "#9AA9B6", violet: "#5B4FA8" },
  destroyAll() { this.list.forEach((c) => c.destroy()); this.list = []; },
  make(canvas, cfg) {
    if (!canvas) return;
    if (!window.Chart) { canvas.parentElement.innerHTML = '<div class="empty">Charts need internet access to load Chart.js.</div>'; return; }
    Chart.defaults.font.family = "Archivo, system-ui, sans-serif";
    Chart.defaults.color = "#44566A";
    Chart.defaults.borderColor = "#E6ECF0";
    Chart.defaults.animation = false;   // data changes are shown immediately, no decorative motion
    cfg.options = Object.assign({ responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { boxWidth: 10, boxHeight: 10 } } } }, cfg.options || {});
    const c = new Chart(canvas, cfg); this.list.push(c); return c;
  },
};

function table(cols, rows, { onRow, empty = "Nothing here yet." } = {}) {
  if (!rows.length) return `<div class="empty">${esc(empty)}</div>`;
  const head = cols.map((c) => `<th class="${c.n ? "n" : ""}">${esc(c.label)}</th>`).join("");
  const body = rows.map((r, i) => `<tr ${onRow ? `class="click" data-i="${i}"` : ""}>${cols.map((c) => `<td class="${c.n ? "n" : ""}">${c.html ? c.html(r) : esc(c.get ? c.get(r) : r[c.key])}</td>`).join("")}</tr>`).join("");
  return `<div class="tbl-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
function bindRows(root, rows, fn) { $$("tr.click", root).forEach((tr) => tr.onclick = () => fn(rows[+tr.dataset.i])); }
