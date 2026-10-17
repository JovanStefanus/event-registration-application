/* Dasbor Karcis: satu halaman, isi menyesuaikan peran (admin / pembuat acara / peserta). */
const S = {
  user: null, stats: {},
  events: [], eventsTotal: 0, eventsPages: 1, page: 1,
  filters: { q: "", category: "", date_from: "", date_to: "", available: false },
  regs: [], users: [], usersTotal: 0, usersPages: 1, usersPage: 1, usersQ: "", usersStatus: "",
  waitlist: [], categories: [],
  tab: "events", openEvent: null, participants: [], ticketReg: null,
};

const isAdmin = () => S.user.role === "admin";
const isOrganizer = () => S.user.role === "organizer";
const isUser = () => S.user.role === "user";
const ownsEvent = ev => isOrganizer() && ev.owner_id === S.user.id;

const TABS = {
  admin: [["events", "Kegiatan"], ["regs", "Pendaftaran"], ["users", "Pengguna"], ["waitlist", "Daftar tunggu"], ["checkin", "Check-in"]],
  organizer: [["events", "Kegiatan"], ["regs", "Pendaftar"], ["waitlist", "Daftar tunggu"], ["checkin", "Check-in"]],
  user: [["events", "Kegiatan"], ["regs", "Pendaftaran saya"], ["waitlist", "Daftar tunggu"]],
};
const HEADS = {
  events: {
    admin: ["Semua kegiatan", "Cari, saring, dan pantau seluruh kegiatan. Admin dapat menghapus kegiatan yang melanggar aturan."],
    organizer: ["Kegiatan", "Buat kegiatan dengan peta, poster, dan pertanyaan tambahan. Kelola kegiatan milik Anda."],
    user: ["Kegiatan tersedia", "Saring kegiatan, lihat poster dan lokasi di peta, lalu daftar."],
  },
  regs: {
    admin: ["Semua pendaftaran", "Seluruh pendaftaran peserta di semua kegiatan."],
    organizer: ["Pendaftar kegiatan Anda", "Peserta yang mendaftar ke kegiatan milik Anda."],
    user: ["Pendaftaran saya", "Lihat tiket QR, ubah data, beri ulasan, atau batalkan pendaftaran."],
  },
  users: { admin: ["Pengguna", "Setujui akun pembuat acara dan kelola akun lain."] },
  waitlist: {
    admin: ["Semua daftar tunggu", "Peserta yang menunggu kursi di semua kegiatan."],
    organizer: ["Daftar tunggu kegiatan Anda", "Kursi otomatis ditawarkan ke antrean paling awal saat ada yang membatalkan."],
    user: ["Daftar tunggu saya", "Kami akan membuatkan pendaftaran saat kursi tersedia."],
  },
  checkin: {
    admin: ["Check-in peserta", "Masukkan kode tiket untuk menandai kehadiran."],
    organizer: ["Check-in peserta", "Masukkan kode tiket peserta kegiatan Anda untuk menandai kehadiran."],
  },
};

/* ---------- Muat data & tampilkan ---------- */
async function fetchEvents() {
  const f = S.filters;
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.category) p.set("category", f.category);
  if (f.date_from) p.set("date_from", f.date_from);
  if (f.date_to) p.set("date_to", f.date_to);
  if (f.available) p.set("available", "true");
  p.set("page", S.page); p.set("limit", 12);
  const r = await api("/api/events?" + p.toString());
  S.events = r.items; S.eventsTotal = r.total; S.eventsPages = r.pages;
}

async function load() {
  try {
    const jobs = [fetchEvents(), api("/api/stats"), api("/api/registrations"), api("/api/waitlist"), api("/api/meta/categories")];
    if (isAdmin()) jobs.push(fetchUsers());
    const [, stats, regs, waitlist, cats] = await Promise.all(jobs);
    S.stats = stats; S.regs = regs; S.waitlist = waitlist; S.categories = cats;
    fillCategoryOptions();
    if (S.openEvent) S.participants = await api(`/api/events/${S.openEvent}/participants`).catch(() => []);
    S.loaded = true;
    render();
  } catch (e) { toast(e.message, "bad"); }
}

async function fetchUsers() {
  const p = new URLSearchParams({ page: S.usersPage, limit: 10 });
  if (S.usersQ) p.set("q", S.usersQ);
  if (S.usersStatus) p.set("status", S.usersStatus);
  const r = await api("/api/users?" + p.toString());
  S.users = r.items; S.usersTotal = r.total; S.usersPages = r.pages;
}

function reloadEvents() { return fetchEvents().then(render).catch(e => toast(e.message, "bad")); }

function render() { renderNav(); renderHead(); renderStats(); renderView(); }

function renderNav() {
  $("nav").replaceChildren(...TABS[S.user.role].map(([key, label]) =>
    h("button", { class: key === S.tab ? "active" : "", type: "button", "aria-current": key === S.tab ? "page" : "false", onclick: () => { location.hash = key; } }, label)));
}

function renderHead() {
  const [title, sub] = HEADS[S.tab][S.user.role];
  $("pageTitle").textContent = title;
  $("pageSub").textContent = sub;
  const actions = [];
  if (isOrganizer() && S.tab === "events") actions.push(btn("Buat kegiatan", "", () => openEventDialog()));
  $("pageActions").replaceChildren(...actions);
}

function renderStats() {
  const st = S.stats || {};
  let items;
  if (isAdmin()) {
    items = [[st.events, "Kegiatan"], [st.registrations, "Pendaftaran"], [st.users, "Pengguna"],
      [st.pending, "Menunggu persetujuan"], [st.waitlist, "Daftar tunggu"]];
  } else if (isOrganizer()) {
    items = [[st.my_events, "Kegiatan saya"], [st.my_registrations, "Total pendaftar"],
      [(st.fill || 0) + "%", "Kursi terisi"], [st.waitlist, "Daftar tunggu"]];
  } else {
    items = [[st.available, "Kegiatan masih ada kursi"], [st.my_registrations, "Pendaftaran saya"], [st.waitlist, "Daftar tunggu"]];
  }
  $("stats").replaceChildren(...items.map(([n, l]) => h("div", {}, h("b", {}, String(n ?? 0)), h("span", {}, l))));
}

function renderView() {
  if (S.tab === "regs") return viewRegs();
  if (S.tab === "waitlist") return viewWaitlist();
  if (S.tab === "users" && isAdmin()) return viewUsers();
  if (S.tab === "checkin") return viewCheckin();
  viewEvents();
}

function emptyState(title, text) {
  return h("div", { class: "empty" }, h("h3", {}, title), h("p", {}, text));
}

/* ---------- Kegiatan ---------- */
function viewEvents() {
  const search = h("input", { class: "input", type: "search", placeholder: "Cari nama, lokasi, atau kategori", "aria-label": "Cari kegiatan", value: S.filters.q });
  search.oninput = debounce(() => { S.filters.q = search.value; S.page = 1; reloadEvents(); }, 350);

  const cat = h("select", { class: "input", "aria-label": "Saring kategori" });
  cat.append(h("option", { value: "" }, "Semua kategori"));
  fillCategoryOptions(cat);
  cat.value = S.filters.category;
  cat.onchange = () => { S.filters.category = cat.value; S.page = 1; reloadEvents(); };

  const from = h("input", { class: "input", type: "date", "aria-label": "Mulai tanggal", value: S.filters.date_from });
  from.onchange = () => { S.filters.date_from = from.value; S.page = 1; reloadEvents(); };
  const to = h("input", { class: "input", type: "date", "aria-label": "Sampai tanggal", value: S.filters.date_to });
  to.onchange = () => { S.filters.date_to = to.value; S.page = 1; reloadEvents(); };

  const avail = checkbox("Hanya yang masih ada kursi", S.filters.available, () => { S.filters.available = avail.querySelector("input").checked; S.page = 1; reloadEvents(); });

  const reset = btn("Atur ulang", "ghost sm", () => {
    S.filters = { q: "", category: "", date_from: "", date_to: "", available: false };
    S.page = 1; reloadEvents();
  });

  const filters = h("div", { class: "filters" },
    h("div", { class: "field" }, h("label", {}, "Kategori"), cat),
    h("div", { class: "field" }, h("label", {}, "Dari"), from),
    h("div", { class: "field" }, h("label", {}, "Sampai"), to),
    avail, reset);

  const list = h("div", {});
  fillEvents(list);
  $("view").replaceChildren(h("div", { class: "toolbar" }, search, filters), list, pager());
}

function fillCategoryOptions(select) {
  const list = document.querySelector("#categoryList");
  if (list) list.replaceChildren(...S.categories.map(c => h("option", { value: c })));
  if (select) {
    select.querySelectorAll("option:not(:first-child)").forEach(o => o.remove());
    S.categories.forEach(c => select.append(h("option", { value: c }, c)));
  }
}

function pager() {
  if (S.eventsPages <= 1) return h("div");
  return h("div", { class: "pager" },
    btn("‹ Sebelumnya", "ghost sm", () => { if (S.page > 1) { S.page--; reloadEvents(); } }),
    h("span", { class: "pg" }, `Halaman ${S.page} dari ${S.eventsPages} • ${S.eventsTotal} kegiatan`),
    btn("Berikutnya ›", "ghost sm", () => { if (S.page < S.eventsPages) { S.page++; reloadEvents(); } }));
}

function fillEvents(list) {
  if (!S.events.length) {
    const q = S.filters.q || S.filters.category || S.filters.date_from || S.filters.available;
    let empty;
    if (q) empty = emptyState("Tidak ada hasil", "Coba ubah kata kunci atau filter.");
    else if (isOrganizer()) empty = emptyState("Belum ada kegiatan", "Pilih Buat kegiatan untuk menambahkan yang pertama.");
    else empty = emptyState("Belum ada kegiatan", "Kegiatan akan muncul di sini setelah pembuat acara menambahkannya.");
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...S.events.map(eventTicket));
}

function myRegFor(eventId) {
  return S.regs.find(r => r.event_id === eventId);
}

function eventTicket(ev) {
  const d = new Date(ev.event_date);
  const full = ev.remaining <= 0;
  const past = d < new Date();
  const pct = Math.min(100, Math.round(ev.registered / ev.quota * 100));
  const bar = h("div", { class: "bar", role: "img", "aria-label": `${pct}% kursi terisi` }, h("span", {}));
  bar.firstChild.style.width = pct + "%";

  const participants = S.openEvent === ev.id
    ? h("div", { class: "parts" },
        h("strong", {}, "Peserta"),
        S.participants.length
          ? h("ul", {}, S.participants.map(p => h("li", {}, p.name, p.email ? h("span", { class: "muted" }, `${p.email}, ${p.phone}${p.checked_in_at ? " • sudah check-in" : ""}`) : null)))
          : h("span", { class: "muted" }, "Belum ada peserta."))
    : null;

  const badges = [];
  if (ev.category) badges.push(h("span", { class: "badge cat" }, ev.category));
  if (ev.status !== "published") badges.push(h("span", { class: "badge " + ev.status }, STATUS_LABEL[ev.status]));
  if (full) badges.push(h("span", { class: "badge full" }, "Penuh"));
  else if (past) badges.push(h("span", { class: "badge" }, "Selesai"));

  const meta = [h("div", { class: "meta" }, icon("pin"), ev.location)];
  if (ev.latitude != null && ev.longitude != null) {
    meta.push(h("div", { class: "meta" }, icon("calendar"),
      h("span", { class: "small" }, `Koordinat ${Number(ev.latitude).toFixed(4)}, ${Number(ev.longitude).toFixed(4)}`)));
  }
  if (ev.owner_name) meta.push(h("div", { class: "meta" }, icon("person"), "Dibuat oleh " + ev.owner_name));
  if (ev.rating_count) meta.push(h("div", { class: "meta" }, stars(ev.rating_avg, ev.rating_count)));

  const actions = [];
  const reg = myRegFor(ev.id);
  if (isUser() && ev.status === "published") {
    if (full) actions.push(btn("Ikut daftar tunggu", "ghost", () => openWaitDialog(ev)));
    else if (!reg) actions.push(btn("Daftar", "", () => openRegDialog(ev.id)));
  }
  if (reg) actions.push(btn("Lihat tiket", "ghost", () => openTicket(ev, reg)));
  if (ownsEvent(ev)) actions.push(btn("Ubah", "ghost", () => openEventDialog(ev)));
  if (isAdmin() || ownsEvent(ev)) {
    actions.push(btn("Hapus", "danger", () => removeEvent(ev)));
    actions.push(btn("Ekspor CSV", "ghost", () => exportCsv(ev)));
  }
  actions.push(btn(S.openEvent === ev.id ? "Tutup peserta" : "Lihat peserta", "ghost", () => toggleParticipants(ev.id)));
  actions.push(btn("Detail", "ghost", () => { location.href = "/event?id=" + ev.id; }));

  const thumb = ev.poster_url ? h("img", { class: "thumb", src: ev.poster_url, alt: `Poster ${ev.name}`, loading: "lazy" }) : null;

  return h("article", { class: "ticket" + (full ? " full" : "") },
    h("div", { class: "stub" },
      h("b", {}, String(d.getDate())),
      h("span", {}, d.toLocaleString("id-ID", { month: "short" })),
      h("small", {}, d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" }))),
    h("div", { class: "tbody" },
      h("div", { class: "title" }, h("h3", {}, ev.name), ...badges),
      ...meta,
      ev.description ? h("p", { class: "desc" }, ev.description) : null,
      thumb,
      h("div", { class: "seats" }, bar, h("span", { class: "txt" }, full ? "Kursi penuh" : `Sisa ${ev.remaining} dari ${ev.quota} kursi`)),
      ev.fields && ev.fields.length ? h("div", { class: "muted small" }, `${ev.fields.length} pertanyaan tambahan saat mendaftar`) : null,
      participants),
    h("div", { class: "tact" }, actions));
}

async function toggleParticipants(id) {
  if (S.openEvent === id) { S.openEvent = null; S.participants = []; return renderView(); }
  try {
    S.participants = await api(`/api/events/${id}/participants`);
    S.openEvent = id;
    renderView();
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

function exportCsv(ev) {
  busy(null, async () => {
    try { await downloadFile(`/api/events/${ev.id}/participants.csv`, `peserta-${ev.id}.csv`); toast("CSV diunduh"); }
    catch (e) { toast(e.message, "bad"); }
  });
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
  const rows = S.regs.map(r => {
    const past = new Date(r.event_date) < new Date();
    const actions = [];
    if (isUser()) actions.push(btn("Tiket", "ghost sm", () => openTicket(r.event_id, r)));
    if (isUser() && past) actions.push(btn("Ulasan", "ghost sm", () => openReview(r.event_id)));
    if (isUser()) actions.push(btn("Ubah", "ghost sm", () => openRegDialog(r.event_id, r)));
    actions.push(btn("Batalkan", "danger sm", () => cancelReg(r)));
    return h("div", { class: "row" },
      h("div", { class: "grow" },
        h("strong", {}, r.event_name),
        h("div", { class: "kv" }, h("span", {}, r.name), h("span", { class: "muted" }, r.email), h("span", { class: "muted" }, r.phone)),
        h("div", { class: "muted small" },
          `Kode ${r.code || "-"} • `, r.checked_in_at ? `Check-in ${fmtDate(r.checked_in_at)}` : "Belum check-in",
          ` • Terdaftar ${fmtDate(r.created_at)}`)),
      h("div", { class: "actions" }, actions));
  });
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

/* ---------- Daftar tunggu ---------- */
function viewWaitlist() {
  if (!S.waitlist.length) {
    const empty = isUser()
      ? emptyState("Belum ikut daftar tunggu", "Saat kursi penuh, Anda bisa ikut daftar tunggu dari halaman Kegiatan.")
      : emptyState("Belum ada daftar tunggu", "Entri akan muncul di sini setelah peserta mendaftar ke antrean.");
    $("view").replaceChildren(empty);
    return;
  }
  const rows = S.waitlist.map(w => {
    const actions = [btn("Hapus", "danger sm", () => removeWait(w))];
    return h("div", { class: "row" },
      h("div", { class: "grow" },
        h("div", { class: "namebar" }, h("strong", {}, w.event_name), h("span", { class: "badge " + (w.status === "converted" ? "active" : "pending") }, w.status === "converted" ? "Terkonversi" : w.status === "notified" ? "Diberi tahu" : "Menunggu")),
        h("div", { class: "kv" }, h("span", {}, w.name), h("span", { class: "muted" }, w.email), h("span", { class: "muted" }, w.phone)),
        h("div", { class: "muted small" }, "Antre sejak " + fmtDate(w.created_at))),
      h("div", { class: "actions" }, actions));
  });
  $("view").replaceChildren(h("div", { class: "rows" }, rows));
}

async function removeWait(w) {
  const ok = await confirmBox(`Hapus ${w.name} dari daftar tunggu "${w.event_name}"?`, "Hapus dari daftar tunggu");
  if (!ok) return;
  try { await api(`/api/waitlist/${w.id}`, { method: "DELETE" }); toast("Entri dihapus"); load(); }
  catch (e) { toast(e.message, "bad"); }
}

/* ---------- Pengguna (admin) ---------- */
function viewUsers() {
  const search = h("input", { class: "input", type: "search", placeholder: "Cari nama atau email", value: S.usersQ });
  search.oninput = debounce(() => { S.usersQ = search.value; S.usersPage = 1; fetchUsers().then(renderView).catch(e => toast(e.message, "bad")); }, 350);

  const status = h("select", { class: "input" },
    h("option", { value: "" }, "Semua status"),
    h("option", { value: "pending" }, "Menunggu persetujuan"),
    h("option", { value: "active" }, "Aktif"));
  status.value = S.usersStatus;
  status.onchange = () => { S.usersStatus = status.value; S.usersPage = 1; fetchUsers().then(renderView).catch(e => toast(e.message, "bad")); };

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

  const pg = h("div", { class: "pager" },
    btn("‹ Sebelumnya", "ghost sm", () => { if (S.usersPage > 1) { S.usersPage--; fetchUsers().then(renderView); } }),
    h("span", { class: "pg" }, `Halaman ${S.usersPage} dari ${S.usersPages} • ${S.usersTotal} pengguna`),
    btn("Berikutnya ›", "ghost sm", () => { if (S.usersPage < S.usersPages) { S.usersPage++; fetchUsers().then(renderView); } }));

  $("view").replaceChildren(
    h("div", { class: "toolbar" }, h("div", { class: "filters" }, h("div", { class: "field" }, h("label", {}, "Cari"), search), h("div", { class: "field" }, h("label", {}, "Status"), status))),
    h("div", { class: "rows" }, rows),
    S.usersPages > 1 ? pg : h("div"));
}

async function approveUser(u) {
  try { await api(`/api/users/${u.id}/approve`, { method: "POST" }); toast(`${u.name} disetujui sebagai pembuat acara`); load(); }
  catch (e) { toast(e.message, "bad"); }
}

async function deleteUser(u) {
  const ok = await confirmBox(`Hapus akun ${u.name}? Kegiatan dan pendaftaran milik akun ini ikut terhapus.`, "Hapus akun");
  if (!ok) return;
  try { await api(`/api/users/${u.id}`, { method: "DELETE" }); toast("Akun dihapus"); load(); }
  catch (e) { toast(e.message, "bad"); }
}

/* ---------- Check-in ---------- */
function viewCheckin() {
  const code = h("input", { class: "input", placeholder: "mis. 42A5AE91", "aria-label": "Kode tiket" });
  const out = h("div", { class: "checkin-result" });
  const submit = btn("Tandai check-in", "", () => busy(submit, async () => {
    out.replaceChildren();
    const value = code.value.trim();
    if (!value) return toast("Masukkan kode tiket", "bad");
    try {
      const r = await api("/api/registrations/checkin", { method: "POST", body: JSON.stringify({ code: value }) });
      out.append(h("div", { class: "notice ok" }, `Berhasil: ${r.name} (${r.email}) — ${r.event_name}`));
      code.value = "";
      load();
    } catch (e) {
      out.append(h("div", { class: "notice bad" }, e.message));
    }
  }));
  $("view").replaceChildren(h("div", { class: "panel" },
    h("h2", {}, "Check-in peserta"),
    h("p", { class: "muted" }, "Ketik kode tiket peserta untuk menandai kehadiran."),
    h("div", { class: "two" }, code, submit),
    out));
}

/* ---------- Dialog kegiatan ---------- */
let picker = null;

function ensurePicker() {
  if (picker) return picker;
  picker = createPickerMap($("evMap"), async (lat, lon, reverse) => {
    $("evLat").value = lat; $("evLon").value = lon;
    if (reverse) {
      try {
        const r = await api(`/api/geocode/reverse?lat=${lat}&lon=${lon}`);
        if (r && r.label) $("evLocation").value = r.label;
      } catch (e) { /* biarkan alamat apa adanya */ }
    }
  });
  return picker;
}

function setPickerPoint(lat, lon, zoom) {
  const p = ensurePicker();
  if (p) p.setPoint(lat, lon, zoom);
}

function addFieldRow(f) {
  f = f || { label: "", field_type: "text", required: false, options: [] };
  const label = h("input", { class: "input f-label", placeholder: "Pertanyaan", value: f.label });
  const type = h("select", { class: "input f-type" },
    h("option", { value: "text" }, "Teks"),
    h("option", { value: "number" }, "Angka"),
    h("option", { value: "select" }, "Pilihan"),
    h("option", { value: "checkbox" }, "Centang"));
  type.value = f.field_type;
  const req = checkbox("Wajib", f.required, null);
  const opts = h("input", { class: "input f-opts opt", placeholder: "Pilihan dipisah koma, mis. S,M,L", value: (f.options || []).join(",") });
  opts.hidden = f.field_type !== "select";
  type.onchange = () => { opts.hidden = type.value !== "select"; };
  const remove = btn("✕", "danger sm", () => row.remove());
  const row = h("div", { class: "fieldrow" }, label, type, req, remove, opts);
  row.dataset.id = f.id || "";
  return row;
}

function collectFields() {
  return [...$("evFields").children].map(row => ({
    id: row.dataset.id ? Number(row.dataset.id) : null,
    label: row.querySelector(".f-label").value.trim(),
    field_type: row.querySelector(".f-type").value,
    required: row.querySelector(".f-req") ? row.querySelector(".f-req").checked : row.querySelector("input[type=checkbox]").checked,
    options: row.querySelector(".f-opts").value.split(",").map(s => s.trim()).filter(Boolean),
  })).filter(f => f.label);
}

function postPrintPreview(f) {
  const prev = $("evPosterPreview");
  if (f) { prev.src = URL.createObjectURL(f); prev.hidden = false; }
  else { prev.removeAttribute("src"); prev.hidden = true; }
}

function openEventDialog(ev) {
  $("evId").value = ev ? ev.id : "";
  $("evName").value = ev ? ev.name : "";
  $("evDesc").value = ev ? ev.description : "";
  $("evDate").value = ev ? ev.event_date.slice(0, 16) : "";
  $("evQuota").value = ev ? ev.quota : "";
  $("evCategory").value = ev ? ev.category : "";
  $("evStatus").value = ev ? ev.status : "published";
  $("evLocation").value = ev ? ev.location : "";
  $("evLat").value = ev && ev.latitude != null ? ev.latitude : "";
  $("evLon").value = ev && ev.longitude != null ? ev.longitude : "";
  $("evPoster").value = "";
  postPrintPreview(null);
  if (ev && ev.poster_url) { $("evPosterPreview").src = ev.poster_url; $("evPosterPreview").hidden = false; }
  $("evPosterRemove").hidden = !(ev && ev.poster_url);
  $("evFields").replaceChildren(...((ev && ev.fields) || []).map(addFieldRow));
  $("evTitle").textContent = ev ? "Ubah kegiatan" : "Buat kegiatan";
  $("evSubmit").textContent = ev ? "Simpan perubahan" : "Simpan kegiatan";
  $("evErr").textContent = "";
  $("evGeoList").hidden = true;
  $("eventDialog").showModal();
  if (ev && ev.latitude != null && ev.longitude != null) setPickerPoint(ev.latitude, ev.longitude, 15);
  const p = ensurePicker();
  if (p) p.invalidate();
}

$("evAddField").onclick = () => $("evFields").append(addFieldRow());

$("evSearchBtn").onclick = () => busy($("evSearchBtn"), async () => {
  const q = $("evLocation").value.trim();
  if (q.length < 3) return toast("Alamat minimal 3 karakter", "bad");
  try {
    const list = await api("/api/geocode/search?q=" + encodeURIComponent(q));
    renderGeoList(list);
  } catch (e) { toast(e.message, "bad"); }
});

function renderGeoList(list) {
  const box = $("evGeoList");
  if (!list.length) { box.hidden = false; box.replaceChildren(h("span", { class: "muted small" }, "Alamat tidak ditemukan.")); return; }
  box.hidden = false;
  box.replaceChildren(...list.map(item => h("button", { type: "button", onclick: () => {
    $("evLocation").value = item.label;
    $("evLat").value = item.lat; $("evLon").value = item.lon;
    setPickerPoint(item.lat, item.lon, 15);
    box.hidden = true;
  } }, item.label)));
}

$("evPoster").onchange = e => postPrintPreview(e.target.files[0] || null);

$("evPosterRemove").onclick = () => busy($("evPosterRemove"), async () => {
  const id = $("evId").value;
  if (!id) { $("evPoster").value = ""; postPrintPreview(null); $("evPosterRemove").hidden = true; return; }
  try { await api(`/api/events/${id}/poster`, { method: "DELETE" }); $("evPoster").value = ""; postPrintPreview(null); $("evPosterRemove").hidden = true; toast("Poster dihapus"); }
  catch (e) { toast(e.message, "bad"); }
});

$("eventForm").onsubmit = async e => {
  e.preventDefault();
  $("evErr").textContent = "";
  const id = $("evId").value;
  const lat = $("evLat").value, lon = $("evLon").value;
  const body = JSON.stringify({
    name: $("evName").value.trim(),
    description: $("evDesc").value,
    event_date: $("evDate").value,
    location: $("evLocation").value.trim(),
    quota: parseInt($("evQuota").value, 10),
    latitude: lat === "" ? null : parseFloat(lat),
    longitude: lon === "" ? null : parseFloat(lon),
    category: $("evCategory").value.trim(),
    status: $("evStatus").value,
    fields: collectFields(),
  });
  await busy($("evSubmit"), async () => {
    try {
      const saved = await api(id ? `/api/events/${id}` : "/api/events", { method: id ? "PUT" : "POST", body });
      const file = $("evPoster").files[0];
      if (file) await apiUpload(`/api/events/${saved.id}/poster`, file);
      $("eventDialog").close();
      toast(id ? "Kegiatan disimpan" : "Kegiatan dibuat");
      load();
    } catch (err) { $("evErr").textContent = err.message; }
  });
};

/* ---------- Dialog pendaftaran ---------- */
let regEvents = [];

function renderRegFields(container, eventId, answers) {
  const ev = regEvents.find(x => x.id === eventId) || S.events.find(x => x.id === eventId);
  const fields = (ev && ev.fields) || [];
  container.replaceChildren(...fields.map(f => {
    const id = "rf-" + f.id;
    const val = answers && answers[f.id] !== undefined ? answers[f.id] : "";
    let input;
    if (f.field_type === "select") {
      input = h("select", { class: "input", id });
      input.append(h("option", { value: "" }, "— pilih —"));
      f.options.forEach(o => input.append(h("option", { value: o }, o)));
      input.value = val;
    } else if (f.field_type === "checkbox") {
      input = h("input", { type: "checkbox", id });
      input.checked = val === "1" || val === "true";
    } else {
      input = h("input", { class: "input", id, value: val, type: f.field_type === "number" ? "number" : "text" });
    }
    return h("div", { class: "field" }, h("label", { for: id }, f.label + (f.required ? " *" : "")), input);
  }));
}

function collectRegAnswers() {
  const ev = regEvents.find(x => x.id === parseInt($("rgEvent").value, 10));
  const answers = {};
  ((ev && ev.fields) || []).forEach(f => {
    const el = $("rf-" + f.id);
    if (!el) return;
    answers[f.id] = f.field_type === "checkbox" ? (el.checked ? "1" : "") : el.value.trim();
  });
  return answers;
}

async function openRegDialog(eventId, reg) {
  try {
    const r = await api("/api/events?limit=100");
    regEvents = r.items.filter(e => e.status === "published" || e.id === eventId);
  } catch (e) { regEvents = S.events; }
  const sel = $("rgEvent");
  sel.replaceChildren(...regEvents.map(e =>
    h("option", { value: e.id }, `${e.name} (${e.remaining <= 0 ? "penuh" : "sisa " + e.remaining + " kursi"})`)));
  const target = eventId || (regEvents[0] && regEvents[0].id);
  sel.value = target;
  sel.onchange = () => renderRegFields($("rgFields"), parseInt(sel.value, 10), null);
  $("rgId").value = reg ? reg.id : "";
  $("rgName").value = reg ? reg.name : S.user.name;
  $("rgEmail").value = reg ? reg.email : S.user.email;
  $("rgPhone").value = reg ? reg.phone : S.user.phone;
  $("rgTitle").textContent = reg ? "Ubah pendaftaran" : "Daftar ke kegiatan";
  $("rgSubmit").textContent = reg ? "Simpan pendaftaran" : "Daftar";
  $("rgErr").textContent = "";
  renderRegFields($("rgFields"), parseInt(target, 10), reg ? reg.answers : null);
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
    answers: collectRegAnswers(),
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

/* ---------- Dialog daftar tunggu ---------- */
function openWaitDialog(ev) {
  $("wlEvent").value = ev.id;
  $("wlInfo").textContent = `Kuota "${ev.name}" penuh. Anda akan dihubungi bila ada kursi kosong.`;
  $("wlName").value = S.user.name;
  $("wlEmail").value = S.user.email;
  $("wlPhone").value = S.user.phone;
  $("wlErr").textContent = "";
  $("waitDialog").showModal();
}

$("waitForm").onsubmit = async e => {
  e.preventDefault();
  $("wlErr").textContent = "";
  const body = JSON.stringify({
    event_id: parseInt($("wlEvent").value, 10),
    name: $("wlName").value.trim(),
    email: $("wlEmail").value.trim(),
    phone: $("wlPhone").value.trim(),
  });
  await busy($("wlSubmit"), async () => {
    try {
      await api("/api/waitlist", { method: "POST", body });
      $("waitDialog").close();
      toast("Masuk daftar tunggu");
      load();
    } catch (err) { $("wlErr").textContent = err.message; }
  });
};

/* ---------- Tiket & ulasan ---------- */
function openTicket(eventId, reg) {
  S.ticketReg = { eventId, reg };
  $("tkCode").textContent = reg.code || "-";
  $("tkEvent").textContent = reg.event_name || "";
  $("tkNote").textContent = reg.checked_in_at ? "Sudah check-in pada " + fmtDate(reg.checked_in_at) : "Tunjukkan kode ini kepada panitia saat check-in.";
  const qr = $("tkQr");
  qr.replaceChildren();
  if (reg.code && typeof QRCode !== "undefined") {
    new QRCode(qr, { text: reg.code, width: 168, height: 168, correctLevel: QRCode.CorrectLevel.M });
  } else if (reg.code) {
    qr.append(h("div", { class: "muted small" }, "Pembuat QR tidak tersedia, gunakan kode di atas."));
  }
  $("ticketDialog").showModal();
}

$("tkIcs").onclick = () => {
  if (!S.ticketReg) return;
  busy($("tkIcs"), async () => {
    try { await downloadFile(`/api/events/${S.ticketReg.eventId}/calendar.ics`, `kegiatan-${S.ticketReg.eventId}.ics`); }
    catch (e) { toast(e.message, "bad"); }
  });
};

function openReview(eventId) {
  $("rvEvent").value = eventId;
  $("rvRating").value = "5";
  $("rvComment").value = "";
  $("rvErr").textContent = "";
  $("reviewDialog").showModal();
}

$("reviewForm").onsubmit = async e => {
  e.preventDefault();
  $("rvErr").textContent = "";
  const eventId = $("rvEvent").value;
  const body = JSON.stringify({ rating: parseInt($("rvRating").value, 10), comment: $("rvComment").value });
  await busy($("rvSubmit"), async () => {
    try {
      await api(`/api/events/${eventId}/reviews`, { method: "POST", body });
      $("reviewDialog").close();
      toast("Terima kasih atas ulasannya");
      load();
    } catch (err) { $("rvErr").textContent = err.message; }
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

/* Perpanjang sesi secara berkala agar pengguna tidak terlempar ke halaman masuk */
function startSessionRefresh() {
  setInterval(async () => {
    if (!getToken()) return;
    try {
      const r = await api("/api/auth/refresh", { method: "POST" });
      if (r && r.access_token) saveSession(r.access_token, r.user);
    } catch (e) { /* biarkan penanganan 401 di api() */ }
  }, 45 * 60 * 1000);
}

(function boot() {
  if (!getToken()) { location.replace("/login"); return; }
  initTheme();
  const tb = $("theme");
  if (tb) { tb.textContent = document.documentElement.dataset.theme === "dark" ? "Mode terang" : "Mode gelap"; tb.onclick = () => toggleTheme(); }
  startHealth("health");
  (async () => {
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
    startSessionRefresh();
    load();
  })();
})();
