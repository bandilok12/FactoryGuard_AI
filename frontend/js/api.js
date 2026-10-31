/* Thin fetch wrapper with JWT handling */
const API = {
  base: "/api",
  token: localStorage.getItem("fg_token"),
  user: JSON.parse(localStorage.getItem("fg_user") || "null"),

  async req(method, path, body) {
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (this.token) opts.headers.Authorization = "Bearer " + this.token;
    if (body !== undefined) opts.body = JSON.stringify(body);
    let res;
    try { res = await fetch(this.base + path, opts); }
    catch (e) { throw new Error("Cannot reach the server. Check that the backend is running."); }
    let data = null;
    try { data = await res.json(); } catch (_) { }
    if (res.status === 401 && path !== "/auth/login") { this.logout(); throw new Error("Session expired. Sign in again."); }
    if (!res.ok) throw new Error((data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `Request failed (${res.status})`);
    return data;
  },
  get(p) { return this.req("GET", p); },
  post(p, b = {}) { return this.req("POST", p, b); },
  put(p, b) { return this.req("PUT", p, b); },
  patch(p, b) { return this.req("PATCH", p, b); },
  del(p) { return this.req("DELETE", p); },

  async login(username, password) {
    const d = await this.post("/auth/login", { username, password });
    this.token = d.access_token; this.user = d.user;
    localStorage.setItem("fg_token", this.token);
    localStorage.setItem("fg_user", JSON.stringify(this.user));
    return d.user;
  },
  logout() {
    this.token = null; this.user = null;
    localStorage.removeItem("fg_token"); localStorage.removeItem("fg_user");
    location.hash = ""; if (window.App) App.render();
  },
  qs(obj) {
    const p = Object.entries(obj).filter(([, v]) => v !== "" && v !== null && v !== undefined);
    return p.length ? "?" + p.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&") : "";
  },
};
