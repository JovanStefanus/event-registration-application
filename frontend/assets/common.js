/* Fungsi bersama untuk semua halaman Karcis */
const $ = id => document.getElementById(id);

const ROLE_LABEL = { admin: "Admin", organizer: "Pembuat acara", user: "Peserta" };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el[k] = v;
    else el.setAttribute(k, v);
  }
  kids.flat().filter(c => c !== null && c !== undefined && c !== false)
    .forEach(c => el.append(c));
  return el;
}

/* Ikon kecil (SVG statis, aman dimasukkan sebagai HTML) */
const ICONS = {
  pin: '<path d="M12 21s7-6.1 7-11a7 7 0 1 0-14 0c0 4.9 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  person: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/>',
};
function icon(name) {
  const s = h("span", { "aria-hidden": "true" });
  s.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + ICONS[name] + "</svg>";
  return s;
}

/* ---------- Sesi ---------- */
function getToken() { try { return localStorage.getItem("token"); } catch (e) { return null; } }
function saveSession(token, user) {
  try { localStorage.setItem("token", token); localStorage.setItem("user", JSON.stringify(user)); } catch (e) {}
}
function clearSession() {
  try { localStorage.removeItem("token"); localStorage.removeItem("user"); } catch (e) {}
}

/* ---------- API ---------- */
function fmtErr(detail, status) {
  if (Array.isArray(detail)) {
    return detail.map(e => `${e.loc.slice(1).join(".")}: ${e.msg}`).join("; ");
  }
  return detail || `Terjadi kesalahan (${status})`;
}
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = "Bearer " + token;
  let res;
  try { res = await fetch(path, { ...opts, headers }); }
  catch (e) { throw new Error("Tidak dapat terhubung ke server"); }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && token && !path.startsWith("/api/auth/login")) {
    clearSession();
    location.href = "/login?expired=1";
    throw new Error("Sesi berakhir, silakan masuk lagi");
  }
  if (!res.ok) throw new Error(fmtErr(data.detail, res.status));
  return data;
}

/* ---------- Tampilan ---------- */
function toast(msg, type) {
  let box = $("toasts");
  if (!box) {
    box = h("div", { id: "toasts", class: "toasts", "aria-live": "polite" });
    document.body.append(box);
  }
  const t = h("div", { class: "toast" + (type === "bad" ? " bad" : "") }, msg);
  box.append(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 300); }, type === "bad" ? 5000 : 3200);
}

function fmtDate(iso) {
  return new Date(iso).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" });
}

/* Indikator kesehatan layanan (memanggil /health tiap 5 detik) */
function startHealth(id) {
  const el = $(id);
  if (!el) return;
  async function ping() {
    try {
      const r = await fetch("/health");
      if (r.ok) { el.className = "health up"; el.replaceChildren(h("i"), "Layanan normal"); }
      else { el.className = "health down"; el.replaceChildren(h("i"), "Database bermasalah"); }
    } catch (e) {
      el.className = "health down"; el.replaceChildren(h("i"), "Backend tidak terjangkau");
    }
  }
  ping();
  setInterval(ping, 5000);
}

/* Menonaktifkan tombol selama permintaan berjalan */
async function busy(button, fn) {
  button.disabled = true;
  try { return await fn(); } finally { button.disabled = false; }
}
