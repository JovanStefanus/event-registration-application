/* Fungsi bersama untuk semua halaman Karcis */
const $ = id => document.getElementById(id);

const ROLE_LABEL = { admin: "Admin", organizer: "Pembuat acara", user: "Peserta" };
const STATUS_LABEL = { draft: "Draf", published: "Dibuka", cancelled: "Dibatalkan" };
const ROLE_HOME = { admin: "/app", organizer: "/app", user: "/app" };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el[k] = v;
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v);
  }
  kids.flat().filter(c => c !== null && c !== undefined && c !== false)
    .forEach(c => el.append(c));
  return el;
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}

/* Tombol dan kotak centang standar (dipakai halaman dasbor dan detail) */
function btn(label, cls, fn) {
  return h("button", { class: "btn " + (cls || ""), type: "button", onclick: fn }, label);
}
function checkbox(label, checked, onchange) {
  const input = h("input", { type: "checkbox", onchange });
  input.checked = !!checked;
  return h("label", { class: "check" }, input, label);
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function qs(name) { return new URLSearchParams(location.search).get(name); }

/* Ikon kecil (SVG statis, aman dimasukkan sebagai HTML) */
const ICONS = {
  pin: '<path d="M12 21s7-6.1 7-11a7 7 0 1 0-14 0c0 4.9 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  person: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/>',
  tag: '<path d="M20 12l-8 8-8-8V4h8z"/><circle cx="8" cy="8" r="1.5"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  star: '<path d="M12 3l2.9 5.9 6.1.9-4.5 4.3 1.1 6-5.6-3-5.6 3 1.1-6L3 9.8l6.1-.9z"/>',
  ticket: '<path d="M3 8a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-2a2 2 0 0 0 0-4z"/><path d="M12 7v10" stroke-dasharray="2 2"/>',
};
function icon(name) {
  const s = h("span", { "aria-hidden": "true" });
  s.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + ICONS[name] + "</svg>";
  return s;
}

function stars(rating, count) {
  const full = Math.round(rating || 0);
  const wrap = h("span", { class: "stars", title: `${rating || 0} dari 5`, "aria-label": `Rating ${rating || 0} dari 5` });
  wrap.textContent = "★★★★★".slice(0, full) + "☆☆☆☆☆".slice(0, 5 - full);
  if (count !== undefined) wrap.append(h("span", { class: "muted small" }, ` (${count})`));
  return wrap;
}

/* ---------- Tema (terang / gelap) ---------- */
function initTheme() {
  const current = document.documentElement.dataset.theme ||
    (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = current;
  return current;
}
function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("theme", next); } catch (e) {}
  document.querySelectorAll("[data-theme-toggle]").forEach(b => {
    b.textContent = next === "dark" ? "Mode terang" : "Mode gelap";
    b.setAttribute("aria-pressed", next === "dark" ? "true" : "false");
  });
  return next;
}
function themeButton() {
  const dark = document.documentElement.dataset.theme === "dark";
  return h("button", {
    class: "btn ghost sm", type: "button", "data-theme-toggle": "1",
    "aria-pressed": dark ? "true" : "false",
    onclick: () => toggleTheme(),
  }, dark ? "Mode terang" : "Mode gelap");
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
    return detail.map(e => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`).join("; ");
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
  if (res.status === 429) throw new Error("Terlalu banyak percobaan. Coba lagi beberapa saat lagi.");
  if (!res.ok) throw new Error(fmtErr(data.detail, res.status));
  return data;
}

/* Unggah berkas (multipart) — jangan set Content-Type agar boundary otomatis */
async function apiUpload(path, file) {
  const body = new FormData();
  body.append("file", file);
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = "Bearer " + token;
  const res = await fetch(path, { method: "POST", headers, body });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(fmtErr(data.detail, res.status));
  return data;
}

/* Unduh berkas yang butuh header Authorization (CSV, ICS) */
async function downloadFile(path, filename) {
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = "Bearer " + token;
  const res = await fetch(path, { headers });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(fmtErr(data.detail, res.status));
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
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
function fmtDay(iso) {
  return new Date(iso).toLocaleDateString("id-ID", { dateStyle: "full" });
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
  if (button) button.disabled = true;
  try { return await fn(); } finally { if (button) button.disabled = false; }
}

/* ---------- Peta (Leaflet + OpenStreetMap) ---------- */
const DEFAULT_CENTER = [-6.1754, 106.8272]; // Jakarta

function pinIcon() {
  return L.divIcon({
    className: "map-marker",
    html: '<span class="map-marker-dot"></span>',
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

/* Membuat peta yang bisa diklik dan pin yang bisa digeser.
   onPoint(lat, lon, reverse) dipanggil setiap titik berubah. */
function createPickerMap(el, onPoint) {
  if (typeof L === "undefined") {
    el.replaceChildren(h("div", { class: "map-fallback" },
      "Peta tidak dapat dimuat (perpustakaan Leaflet tidak tersedia). Isi alamat secara manual."));
    return null;
  }
  const map = L.map(el, { scrollWheelZoom: true }).setView(DEFAULT_CENTER, 12);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  const marker = L.marker(DEFAULT_CENTER, { draggable: true, icon: pinIcon() }).addTo(map);
  marker.on("dragend", () => {
    const p = marker.getLatLng();
    onPoint(p.lat, p.lng, true);
  });
  map.on("click", e => onPoint(e.latlng.lat, e.latlng.lng, true));
  return {
    map, marker,
    setPoint(lat, lon, zoom) {
      marker.setLatLng([lat, lon]);
      map.setView([lat, lon], zoom || Math.max(map.getZoom(), 15));
    },
    invalidate() { setTimeout(() => map.invalidateSize(), 60); },
  };
}
