/* Halaman detail kegiatan. */
let EV = null;
let user = null;

const isUser = () => user.role === "user";
const isAdmin = () => user.role === "admin";
const isOrganizer = () => user.role === "organizer";
const canManage = () => EV && (isAdmin() || (isOrganizer() && EV.owner_id === user.id));

function detailMap(ev) {
  const box = h("div", { class: "map map-small" });
  setTimeout(() => {
    if (typeof L === "undefined") {
      box.replaceChildren(h("div", { class: "map-fallback" }, "Peta tidak dapat dimuat."));
      return;
    }
    const m = L.map(box).setView([ev.latitude, ev.longitude], 15);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(m);
    L.marker([ev.latitude, ev.longitude], { icon: pinIcon() }).addTo(m);
    setTimeout(() => m.invalidateSize(), 80);
  }, 0);
  return box;
}

function seatBar(ev) {
  const pct = Math.min(100, Math.round(ev.registered / ev.quota * 100));
  const bar = h("div", { class: "bar", role: "img", "aria-label": `${pct}% kursi terisi` }, h("span", {}));
  bar.firstChild.style.width = pct + "%";
  return h("div", { class: "seats" }, bar,
    h("span", { class: "txt" }, ev.remaining <= 0 ? "Kursi penuh" : `Sisa ${ev.remaining} dari ${ev.quota} kursi`));
}

function render() {
  const ev = EV;
  const d = new Date(ev.event_date);
  const full = ev.remaining <= 0;

  const badges = [];
  if (ev.category) badges.push(h("span", { class: "badge cat" }, ev.category));
  badges.push(h("span", { class: "badge " + ev.status }, STATUS_LABEL[ev.status]));
  if (ev.rating_count) badges.push(stars(ev.rating_avg, ev.rating_count));

  const actions = [];
  const mine = ev.my_registration;
  if (isUser() && ev.status === "published") {
    if (full) actions.push(btn("Ikut daftar tunggu", "ghost", () => openWait()));
    else if (!mine) actions.push(btn("Daftar sekarang", "", () => openReg()));
  }
  if (mine) actions.push(h("span", { class: "chip" }, "Kode tiket: " + (mine.code || "-")));
  if (canManage()) actions.push(btn("Ekspor CSV peserta", "ghost", exportCsv));
  actions.push(btn("Tambah ke kalender (.ics)", "ghost", () => busy(null, async () => {
    try { await downloadFile(`/api/events/${ev.id}/calendar.ics`, `kegiatan-${ev.id}.ics`); }
    catch (e) { toast(e.message, "bad"); }
  })));
  actions.push(h("a", { class: "btn ghost", href: "/app" }, "Kembali ke dasbor"));

  const overview = h("div", { class: "panel" },
    h("h1", {}, ev.name),
    h("div", { class: "title", style: "display:flex;gap:.5rem;flex-wrap:wrap;align-items:center" }, ...badges),
    h("div", { class: "meta" }, icon("calendar"), `${fmtDay(ev.event_date)} • ${d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })}`),
    h("div", { class: "meta" }, icon("pin"), ev.location),
    ev.owner_name ? h("div", { class: "meta" }, icon("person"), "Dibuat oleh " + ev.owner_name) : null,
    seatBar(ev),
    ev.description ? h("p", {}, ev.description) : h("p", { class: "muted" }, "Tidak ada deskripsi."),
    ev.latitude != null && ev.longitude != null ? detailMap(ev) : null);
  if (ev.poster_url) overview.prepend(h("img", { class: "hero-poster", src: ev.poster_url, alt: `Poster ${ev.name}` }));

  const side = h("div", { class: "panel" },
    h("h2", {}, "Aksi"),
    h("div", { class: "actions" }, actions),
    ev.fields && ev.fields.length
      ? h("div", {}, h("h3", {}, "Pertanyaan saat mendaftar"),
          h("ul", { class: "small muted" }, ev.fields.map(f => h("li", {}, f.label + (f.required ? " (wajib)" : "")))))
      : null);

  const reviews = h("div", { class: "panel" },
    h("h2", {}, `Ulasan (${ev.reviews.length})`),
    ev.reviews.length
      ? h("div", {}, ev.reviews.map(r => h("div", { class: "review" },
          h("div", { class: "who" }, h("strong", {}, r.user_name), stars(r.rating), h("span", { class: "muted small" }, fmtDate(r.created_at))),
          r.comment ? h("p", {}, r.comment) : null)))
      : h("p", { class: "muted" }, "Belum ada ulasan."),
    ev.can_review && !ev.reviews.some(r => r.user_name === user.name)
      ? btn("Tulis ulasan", "", openReview)
      : null);

  $("detail").replaceChildren(
    h("div", { class: "detail-grid" },
      h("div", { style: "display:grid;gap:1rem" }, overview, reviews),
      side));
}

function openReg() {
  $("rgName").value = user.name;
  $("rgEmail").value = user.email;
  $("rgPhone").value = user.phone;
  $("rgErr").textContent = "";
  $("rgFields").replaceChildren(...(EV.fields || []).map(f => {
    const id = "rf-" + f.id;
    let input;
    if (f.field_type === "select") {
      input = h("select", { class: "input", id });
      input.append(h("option", { value: "" }, "— pilih —"));
      f.options.forEach(o => input.append(h("option", { value: o }, o)));
    } else if (f.field_type === "checkbox") {
      input = h("input", { type: "checkbox", id });
    } else {
      input = h("input", { class: "input", id, type: f.field_type === "number" ? "number" : "text" });
    }
    return h("div", { class: "field" }, h("label", { for: id }, f.label + (f.required ? " *" : "")), input);
  }));
  $("regDialog").showModal();
}

$("regForm").onsubmit = async e => {
  e.preventDefault();
  $("rgErr").textContent = "";
  const answers = {};
  (EV.fields || []).forEach(f => {
    const el = $("rf-" + f.id);
    if (!el) return;
    answers[f.id] = f.field_type === "checkbox" ? (el.checked ? "1" : "") : el.value.trim();
  });
  const body = JSON.stringify({
    event_id: EV.id,
    name: $("rgName").value.trim(),
    email: $("rgEmail").value.trim(),
    phone: $("rgPhone").value.trim(),
    answers,
  });
  await busy($("rgSubmit"), async () => {
    try {
      await api("/api/registrations", { method: "POST", body });
      $("regDialog").close();
      toast("Pendaftaran berhasil");
      await reload();
    } catch (err) { $("rgErr").textContent = err.message; }
  });
};

async function openWait() {
  const name = user.name, email = user.email, phone = user.phone;
  try {
    await api("/api/waitlist", { method: "POST", body: JSON.stringify({ event_id: EV.id, name, email, phone }) });
    toast("Masuk daftar tunggu");
    await reload();
  } catch (e) { toast(e.message, "bad"); }
}

function openReview() {
  $("rvRating").value = "5";
  $("rvComment").value = "";
  $("rvErr").textContent = "";
  $("reviewDialog").showModal();
}

$("reviewForm").onsubmit = async e => {
  e.preventDefault();
  $("rvErr").textContent = "";
  const body = JSON.stringify({ rating: parseInt($("rvRating").value, 10), comment: $("rvComment").value });
  await busy($("rvSubmit"), async () => {
    try {
      await api(`/api/events/${EV.id}/reviews`, { method: "POST", body });
      $("reviewDialog").close();
      toast("Terima kasih atas ulasannya");
      await reload();
    } catch (err) { $("rvErr").textContent = err.message; }
  });
};

function exportCsv() {
  busy(null, async () => {
    try { await downloadFile(`/api/events/${EV.id}/participants.csv`, `peserta-${EV.id}.csv`); toast("CSV diunduh"); }
    catch (e) { toast(e.message, "bad"); }
  });
}

document.querySelectorAll("dialog").forEach(d => {
  d.addEventListener("click", ev => { if (ev.target === d) d.close(); });
  d.querySelectorAll("[data-close]").forEach(b => { b.onclick = () => d.close(); });
});

async function reload() {
  EV = await api("/api/events/" + qs("id"));
  render();
}

(function boot() {
  if (!getToken()) { location.replace("/login"); return; }
  initTheme();
  const tb = $("theme");
  if (tb) { tb.textContent = document.documentElement.dataset.theme === "dark" ? "Mode terang" : "Mode gelap"; tb.onclick = () => toggleTheme(); }
  startHealth("health");
  (async () => {
    try {
      user = await api("/api/auth/me");
      if (!qs("id")) { location.replace("/app"); return; }
      $("uname").textContent = user.name;
      $("urole").textContent = ROLE_LABEL[user.role];
      $("urole").className = "badge " + user.role;
      $("logout").onclick = () => { clearSession(); location.href = "/login"; };
      await reload();
    } catch (e) {
      $("detail").replaceChildren(h("div", { class: "empty" }, h("h3", {}, "Tidak dapat memuat kegiatan"), h("p", {}, e.message)));
    }
  })();
})();
