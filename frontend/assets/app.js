/* Dasbor Karcis: satu halaman, isi menyesuaikan peran (admin / pembuat acara / peserta). */
const S = { user: null, events: [], regs: [], users: [], tab: "events", openEvent: null, participants: [], q: "" };

const isAdmin = () => S.user.role === "admin";
const isOrganizer = () => S.user.role === "organizer";
const isUser = () => S.user.role === "user";
const ownsEvent = ev => isOrganizer() && ev.owner_id === S.user.id;

const TABS = {
  admin: [["events", "Kegiatan"], ["regs", "Pendaftaran"], ["users", "Pengguna"]],
  organizer: [["events", "Kegiatan"], ["regs", "Pendaftar"]],
  user: [["events", "Kegiatan"], ["regs", "Pendaftaran saya"]],
};
const HEADS = {
  events: {
    admin: ["Semua kegiatan", "Pantau kegiatan di seluruh platform. Admin dapat menghapus kegiatan yang melanggar aturan."],
    organizer: ["Kegiatan", "Buat kegiatan, ubah kegiatan milik Anda, dan lihat siapa yang mendaftar."],
    user: ["Kegiatan tersedia", "Pilih kegiatan, cek sisa kursi, lalu daftar."],
  },
  regs: {
    admin: ["Semua pendaftaran", "Seluruh pendaftaran peserta di semua kegiatan."],
    organizer: ["Pendaftar kegiatan Anda", "Peserta yang mendaftar ke kegiatan milik Anda."],
    user: ["Pendaftaran saya", "Ubah data atau batalkan pendaftaran Anda."],
  },
  users: { admin: ["Pengguna", "Setujui akun pembuat acara dan kelola akun lain."] },
};

const btn = (label, cls, fn) => h("button", { class: "btn " + (cls || ""), type: "button", onclick: fn }, label);

/* ---------- Muat data & tampilkan ---------- */
async function load() {
  try {
    const jobs = [api("/api/events"), api("/api/registrations")];
    if (isAdmin()) jobs.push(api("/api/users"));
    const [events, regs, users] = await Promise.all(jobs);
    S.events = events; S.regs = regs; S.users = users || []; S.loaded = true;
    if (S.openEvent) S.participants = await api(`/api/events/${S.openEvent}/participants`).catch(() => []);
    render();
  } catch (e) { toast(e.message, "bad"); }
}

function render() { renderNav(); renderHead(); renderStats(); renderView(); }

function renderNav() {
  $("nav").replaceChildren(...TABS[S.user.role].map(([key, label]) =>
    h("button", { class: key === S.tab ? "active" : "", type: "button", "aria-current": key === S.tab ? "page" : "false", onclick: () => { location.hash = key; } }, label)));
}

function renderHead() {
  const [title, sub] = HEADS[S.tab][S.user.role];
  $("pageTitle").textContent = title;
  $("pageSub").textContent = sub;
  // replaceChildren() mengubah null menjadi teks "null", jadi hanya kirim elemen yang ada
  const actions = [];
  if (isOrganizer() && S.tab === "events") actions.push(btn("Buat kegiatan", "", () => openEventDialog()));
  $("pageActions").replaceChildren(...actions);
}

function renderStats() {
  let items;
  if (isAdmin()) {
    items = [
      [S.events.length, "Kegiatan"], [S.regs.length, "Pendaftaran"], [S.users.length, "Pengguna"],
      [S.users.filter(u => u.status === "pending").length, "Menunggu persetujuan"],
    ];
  } else if (isOrganizer()) {
    const mine = S.events.filter(ownsEvent);
    const cap = mine.reduce((a, e) => a + e.quota, 0);
    const reg = mine.reduce((a, e) => a + e.registered, 0);
    items = [[mine.length, "Kegiatan saya"], [reg, "Total pendaftar"], [cap ? Math.round(reg / cap * 100) + "%" : "0%", "Kursi terisi"]];
  } else {
    items = [
      [S.events.filter(e => e.remaining > 0).length, "Kegiatan masih ada kursi"],
      [S.regs.length, "Pendaftaran saya"],
      [S.events.filter(e => e.remaining <= 0).length, "Kegiatan penuh"],
    ];
  }
  $("stats").replaceChildren(...items.map(([n, l]) => h("div", {}, h("b", {}, String(n)), h("span", {}, l))));
}

function renderView() {
  if (S.tab === "regs") return viewRegs();
  if (S.tab === "users" && isAdmin()) return viewUsers();
  viewEvents();
}

function emptyState(title, text) {
  return h("div", { class: "empty" }, h("h3", {}, title), h("p", {}, text));
}

/* ---------- Kegiatan ---------- */
function viewEvents() {
  const search = h("input", { class: "input", type: "search", placeholder: "Cari nama kegiatan, lokasi, atau penyelenggara", "aria-label": "Cari kegiatan", value: S.q });
  const list = h("div", {});
  search.oninput = () => { S.q = search.value; fillEvents(list); };
  $("view").replaceChildren(h("div", { class: "toolbar" }, search), list);
  fillEvents(list);
}

function fillEvents(list) {
  const q = S.q.trim().toLowerCase();
  const shown = S.events.filter(e => !q || [e.name, e.location, e.owner_name || ""].some(t => t.toLowerCase().includes(q)));
  if (!shown.length) {
    let empty;
    if (q) empty = emptyState("Tidak ada hasil", "Coba kata kunci lain.");
    else if (isOrganizer()) empty = emptyState("Belum ada kegiatan", "Pilih Buat kegiatan untuk menambahkan yang pertama.");
    else empty = emptyState("Belum ada kegiatan", "Kegiatan akan muncul di sini setelah pembuat acara menambahkannya.");
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...shown.map(eventTicket));
}

function eventTicket(ev) {
  const d = new Date(ev.event_date);
  const full = ev.remaining <= 0;
  const pct = Math.min(100, Math.round(ev.registered / ev.quota * 100));
  const bar = h("div", { class: "bar", role: "img", "aria-label": `${pct}% kursi terisi` }, h("span", {}));
  bar.firstChild.style.width = pct + "%";

  const participants = S.openEvent === ev.id
    ? h("div", { class: "parts" },
        h("strong", {}, "Peserta"),
        S.participants.length
          ? h("ul", {}, S.participants.map(p => h("li", {}, p.name, p.email ? h("span", { class: "muted" }, `${p.email}, ${p.phone}`) : null)))
          : h("span", { class: "muted" }, "Belum ada peserta."))
    : null;

  const actions = [];
  if (isUser()) actions.push(btn("Daftar", "", () => openRegDialog(ev.id)));
  if (ownsEvent(ev)) actions.push(btn("Ubah", "ghost", () => openEventDialog(ev)));
  if (isAdmin() || ownsEvent(ev)) actions.push(btn("Hapus", "danger", () => removeEvent(ev)));
  actions.push(btn(S.openEvent === ev.id ? "Tutup peserta" : "Lihat peserta", "ghost", () => toggleParticipants(ev.id)));

  return h("article", { class: "ticket" + (full ? " full" : "") },
    h("div", { class: "stub" },
      h("b", {}, String(d.getDate())),
      h("span", {}, d.toLocaleString("id-ID", { month: "short" })),
      h("small", {}, d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" }))),
    h("div", { class: "tbody" },
      h("div", { class: "title" }, h("h3", {}, ev.name), full ? h("span", { class: "badge full" }, "Penuh") : null),
      h("div", { class: "meta" }, icon("pin"), ev.location),
      ev.owner_name ? h("div", { class: "meta" }, icon("person"), "Dibuat oleh " + ev.owner_name) : null,
      ev.description ? h("p", { class: "desc" }, ev.description) : null,
      h("div", { class: "seats" }, bar, h("span", { class: "txt" }, full ? "Kursi penuh" : `Sisa ${ev.remaining} dari ${ev.quota} kursi`)),
      participants),
    h("div", { class: "tact" }, actions));
}

async function toggleParticipants(id) {
  if (S.openEvent === id) { S.openEvent = null; S.participants = []; return viewEvents(); }
  try {
    S.participants = await api(`/api/events/${id}/participants`);
    S.openEvent = id;
    viewEvents();
  } catch (e) { toast(e.message, "bad"); }
}

async function removeEvent(ev) {
  const ok = await confirmBox(`Hapus kegiatan "${ev.name}"? Semua pendaftarnya ikut terhapus.`, "Hapus kegiatan");
  if (!ok) return;
  try {
    await api(`/api/events/${ev.id}`, { method: "DELETE" });
    if (S.openEvent === ev.id) S.openEvent = null;
    toast("Kegiatan dihapus");
    load();
  } catch (e) { toast(e.message, "bad"); }
}

/* ---------- Pendaftaran ---------- */
function viewRegs() {
  if (!S.regs.length) {
    let empty;
    if (isUser()) empty = emptyState("Belum ada pendaftaran", "Buka menu Kegiatan, pilih kegiatan, lalu pilih Daftar.");
    else empty = emptyState("Belum ada pendaftar", "Pendaftar akan muncul di sini setelah peserta mendaftar.");
    $("view").replaceChildren(empty);
    return;
  }
  const rows = S.regs.map(r => h("div", { class: "row" },
    h("div", { class: "grow" },
      h("strong", {}, r.event_name),
      h("div", { class: "kv" }, h("span", {}, r.name), h("span", { class: "muted" }, r.email), h("span", { class: "muted" }, r.phone)),
      h("div", { class: "muted small" }, "Terdaftar " + fmtDate(r.created_at))),
    h("div", { class: "actions" },
      isUser() ? btn("Ubah", "ghost sm", () => openRegDialog(r.event_id, r)) : null,
      btn("Batalkan", "danger sm", () => cancelReg(r)))));
  $("view").replaceChildren(h("div", { class: "rows" }, rows));
}

async function cancelReg(r) {
  const who = isUser() ? "pendaftaran Anda" : `pendaftaran ${r.name}`;
  const ok = await confirmBox(`Batalkan ${who} pada "${r.event_name}"?`, "Batalkan pendaftaran");
  if (!ok) return;
  try {
    await api(`/api/registrations/${r.id}`, { method: "DELETE" });
    toast("Pendaftaran dibatalkan");
    load();
  } catch (e) { toast(e.message, "bad"); }
}

/* ---------- Pengguna (admin) ---------- */
function viewUsers() {
  if (!S.users.length) { $("view").replaceChildren(emptyState("Belum ada pengguna", "Akun akan muncul di sini.")); return; }
  const rows = S.users.map(u => h("div", { class: "row" },
    h("div", { class: "grow" },
      h("div", { class: "namebar" },
        h("strong", {}, u.name),
        h("span", { class: "badge " + u.role }, ROLE_LABEL[u.role]),
        u.status === "pending" ? h("span", { class: "badge pending" }, "Menunggu persetujuan") : null),
      h("div", { class: "kv" }, h("span", { class: "muted" }, u.email), h("span", { class: "muted" }, u.phone))),
    h("div", { class: "actions" },
      u.status === "pending" ? btn("Setujui", "sm", () => approveUser(u)) : null,
      u.role !== "admin" ? btn("Hapus", "danger sm", () => deleteUser(u)) : null)));
  $("view").replaceChildren(h("div", { class: "rows" }, rows));
}

async function approveUser(u) {
  try {
    await api(`/api/users/${u.id}/approve`, { method: "POST" });
    toast(`${u.name} disetujui sebagai pembuat acara`);
    load();
  } catch (e) { toast(e.message, "bad"); }
}

async function deleteUser(u) {
  const ok = await confirmBox(`Hapus akun ${u.name}? Kegiatan dan pendaftaran milik akun ini ikut terhapus.`, "Hapus akun");
  if (!ok) return;
  try {
    await api(`/api/users/${u.id}`, { method: "DELETE" });
    toast("Akun dihapus");
    load();
  } catch (e) { toast(e.message, "bad"); }
}

/* ---------- Dialog ---------- */
function openEventDialog(ev) {
  $("evId").value = ev ? ev.id : "";
  $("evName").value = ev ? ev.name : "";
  $("evDesc").value = ev ? ev.description : "";
  $("evDate").value = ev ? ev.event_date.slice(0, 16) : "";
  $("evLocation").value = ev ? ev.location : "";
  $("evQuota").value = ev ? ev.quota : "";
  $("evTitle").textContent = ev ? "Ubah kegiatan" : "Buat kegiatan";
  $("evErr").textContent = "";
  $("eventDialog").showModal();
}

$("eventForm").onsubmit = async e => {
  e.preventDefault();
  $("evErr").textContent = "";
  const id = $("evId").value;
  const body = JSON.stringify({
    name: $("evName").value.trim(),
    description: $("evDesc").value,
    event_date: $("evDate").value,
    location: $("evLocation").value.trim(),
    quota: parseInt($("evQuota").value, 10),
  });
  await busy($("evSubmit"), async () => {
    try {
      await api(id ? `/api/events/${id}` : "/api/events", { method: id ? "PUT" : "POST", body });
      $("eventDialog").close();
      toast(id ? "Kegiatan disimpan" : "Kegiatan dibuat");
      load();
    } catch (err) { $("evErr").textContent = err.message; }
  });
};

function openRegDialog(eventId, reg) {
  const sel = $("rgEvent");
  sel.replaceChildren(...S.events.map(e =>
    h("option", { value: e.id }, `${e.name} (${e.remaining <= 0 ? "penuh" : "sisa " + e.remaining + " kursi"})`)));
  sel.value = eventId;
  $("rgId").value = reg ? reg.id : "";
  $("rgName").value = reg ? reg.name : S.user.name;
  $("rgEmail").value = reg ? reg.email : S.user.email;
  $("rgPhone").value = reg ? reg.phone : S.user.phone;
  $("rgTitle").textContent = reg ? "Ubah pendaftaran" : "Daftar ke kegiatan";
  $("rgSubmit").textContent = reg ? "Simpan pendaftaran" : "Daftar";
  $("rgErr").textContent = "";
  $("regDialog").showModal();
}

$("regForm").onsubmit = async e => {
  e.preventDefault();
  $("rgErr").textContent = "";
  const id = $("rgId").value;
  const body = JSON.stringify({
    event_id: parseInt($("rgEvent").value, 10),
    name: $("rgName").value.trim(),
    email: $("rgEmail").value.trim(),
    phone: $("rgPhone").value.trim(),
  });
  await busy($("rgSubmit"), async () => {
    try {
      await api(id ? `/api/registrations/${id}` : "/api/registrations", { method: id ? "PUT" : "POST", body });
      $("regDialog").close();
      toast(id ? "Pendaftaran disimpan" : "Pendaftaran berhasil");
      load();
    } catch (err) { $("rgErr").textContent = err.message; }
  });
};

function confirmBox(message, okLabel) {
  return new Promise(resolve => {
    const dlg = $("confirmDialog");
    let result = false;
    $("cfMsg").textContent = message;
    $("cfOk").textContent = okLabel || "Lanjutkan";
    $("cfOk").onclick = () => { result = true; dlg.close(); };
    dlg.onclose = () => resolve(result);
    dlg.showModal();
  });
}

/* Tutup dialog lewat tombol Batal atau klik di luar kotak */
document.querySelectorAll("dialog").forEach(d => {
  d.addEventListener("click", ev => { if (ev.target === d) d.close(); });
  d.querySelectorAll("[data-close]").forEach(b => { b.onclick = () => d.close(); });
});

/* ---------- Mulai ---------- */
function applyHash() {
  const keys = TABS[S.user.role].map(t => t[0]);
  const wanted = location.hash.replace("#", "");
  S.tab = keys.includes(wanted) ? wanted : "events";
  S.openEvent = null; S.participants = [];
  if (S.loaded) render();
}

(async function boot() {
  if (!getToken()) { location.replace("/login"); return; }
  startHealth("health");
  try {
    S.user = await api("/api/auth/me");
  } catch (e) {
    if (getToken()) toast(e.message, "bad");
    return;
  }
  $("uname").textContent = S.user.name;
  $("urole").textContent = ROLE_LABEL[S.user.role];
  $("urole").className = "badge " + S.user.role;
  $("logout").onclick = () => { clearSession(); location.href = "/login"; };
  window.addEventListener("hashchange", applyHash);
  applyHash();
  load();
})();
