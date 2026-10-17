# Karcis: Aplikasi Pendaftaran Kegiatan (Proyek Cloud Services, Checkpoint UTS)

Aplikasi web untuk membuat kegiatan dan mendaftar ke kegiatan tersebut. Ada tiga peran (admin, pembuat acara, peserta), halaman masuk dan daftar yang terpisah, serta persetujuan admin untuk akun pembuat acara. Seluruh layanan berjalan di Docker dan Docker Compose.

## Arsitektur

![Diagram arsitektur aplikasi Pendaftaran Kegiatan](docs/arsitektur.png)

```mermaid
flowchart LR
    U[Browser / User] -->|HTTP :8080| F[frontend<br/>Nginx]
    F -->|/api/* dan /health<br/>reverse proxy, frontend_net| B[backend<br/>FastAPI :8000]
    B -->|SQL :5432<br/>backend_net| D[(db<br/>PostgreSQL)]
    D --- V[(volume: pgdata)]
```

| Service  | Teknologi       | Port host → container | Keterangan |
|----------|-----------------|-----------------------|------------|
| frontend | Nginx + HTML/JS | 8080 → 80             | Menyajikan halaman `/login`, `/register`, `/app` dan mem-proxy `/api` ke backend |
| backend  | Python FastAPI  | 8000 → 8000           | REST API, autentikasi JWT, hak akses per peran, validasi, log, `/health` |
| db       | PostgreSQL 16   | tidak dipublikasikan  | Data disimpan di volume `pgdata` |

**Komunikasi antarlayanan:** container saling menemukan lewat nama service (DNS internal Docker). Backend memakai `DB_HOST=db`, Nginx memakai `http://backend:8000`. Database hanya ada di jaringan `backend_net`, sehingga tidak bisa diakses langsung dari frontend maupun dari host.

## Halaman

| Alamat | Fungsi |
|--------|--------|
| `/login` | Masuk (satu pintu untuk semua peran, lalu diarahkan ke dasbor) |
| `/register` | Buat akun sebagai Peserta atau Pembuat acara |
| `/app` | Dasbor; isi menu menyesuaikan peran |

## Tampilan aplikasi

![Halaman buat akun](docs/screenshots/Screenshot272.png)
![Dasbor login](docs/screenshots/Screenshot273.png)
![Dasbor user](docs/screenshots/Screenshot274.png)
![Dasbor user](docs/screenshots/Screenshot275.png)
![Dasbor user](docs/screenshots/Screenshot276.png)
![Dasbor admin](docs/screenshots/Screenshot277.png)
![Dasbor admin](docs/screenshots/Screenshot278.png)
![Dasbor admin](docs/screenshots/Screenshot279.png)
![Dasbor pembuat acara](docs/screenshots/Screenshot280.png)
![Dasbor pembuat acara](docs/screenshots/Screenshot281.png)
![Dasbor pembuat acara](docs/screenshots/Screenshot282.png)

### Bukti pengujian

Seluruh screenshot disimpan di folder [`docs/bukti-uji/`](docs/bukti-uji/).

| # | Pengujian | Hasil | Bukti |
|---|-----------|-------|-------|
| 0 | Semua service berjalan | Semua container `healthy` | <img src="docs/bukti-uji/uji-00-compose-ps.png" width="360"> |
| 1 | Buat akun peserta | Akun terbuat, bisa masuk | <img src="docs/bukti-uji/uji-01-buat-peserta.png" width="360"> |
| 2 | Login dengan password salah | 401, "Email atau password salah" | <img src="docs/bukti-uji/uji-02-login-salah.png" width="360"> |
| 3 | Pembuat acara belum disetujui | 403, menunggu persetujuan | <img src="docs/bukti-uji/uji-03-organizer-pending.png" width="360"> |
| 4 | Persetujuan admin | Pembuat acara bisa masuk | <img src="docs/bukti-uji/uji-04-persetujuan-admin.png" width="360"> |
| 5 | Kelola kegiatan | Daftar kegiatan ikut berubah | <img src="docs/bukti-uji/uji-05-kelola-kegiatan.png" width="360"> |
| 6 | Batas hak akses | 403 | <img src="docs/bukti-uji/uji-06-hak-akses.png" width="360"> |
| 7 | Daftar ke kegiatan | Muncul di "Pendaftaran saya", kursi berkurang | <img src="docs/bukti-uji/uji-07-daftar-kegiatan.png" width="360"> |
| 8 | Ubah dan batalkan pendaftaran | Data berubah, kursi kembali | <img src="docs/bukti-uji/uji-08-ubah-batal.png" width="360"> |
| 9 | Email tidak valid | 422, data tidak tersimpan | <img src="docs/bukti-uji/uji-09-email-tidak-valid.png" width="360"> |
| 10 | Kuota penuh | 409 | <img src="docs/bukti-uji/uji-10-kuota-penuh.png" width="360"> |
| 11 | Pendaftaran ganda | 409 | <img src="docs/bukti-uji/uji-11-pendaftaran-ganda.png" width="360"> |
| - | Akses tanpa login | 401 | <img src="docs/bukti-uji/uji-401-tanpa-login.png" width="360"> |

## Peran dan hak akses

| Aksi | Admin | Pembuat acara | Peserta |
|------|:-----:|:-------------:|:-------:|
| Lihat daftar kegiatan | ✔ | ✔ | ✔ |
| Buat kegiatan | ✘ | ✔ | ✘ |
| Ubah kegiatan | ✘ | milik sendiri | ✘ |
| Hapus kegiatan | semua | milik sendiri | ✘ |
| Daftar ke kegiatan | ✘ | ✘ | ✔ |
| Ubah data pendaftaran | ✘ | ✘ | milik sendiri |
| Batalkan pendaftaran | semua | pendaftar di kegiatannya | milik sendiri |
| Lihat daftar peserta kegiatan | lengkap | lengkap (kegiatan sendiri), nama saja (lainnya) | nama saja |
| Setujui atau hapus akun | ✔ | ✘ | ✘ |

Alur akun:
- **Peserta** langsung aktif setelah membuat akun.
- **Pembuat acara** berstatus *menunggu persetujuan* dan baru bisa masuk setelah admin menyetujuinya di menu Pengguna.
- **Admin** dibuat otomatis dari `ADMIN_EMAIL` dan `ADMIN_PASSWORD` di `.env`.

## Model data

- `users`: nama, email (unik), telepon, password (di-hash bcrypt), peran (`admin` / `organizer` / `user`), status (`active` / `pending`)
- `events`: nama, deskripsi, tanggal, lokasi, kuota, `owner_id` (pembuat acara)
- `registrations`: kegiatan, akun pemilik, nama, email, telepon. Pasangan `(event_id, email)` unik sehingga pendaftaran ganda ditolak oleh database juga.

Skema dibuat otomatis saat backend start dan aman dijalankan ulang. Database dari versi sebelumnya (tanpa peran pembuat acara) dimigrasi otomatis tanpa kehilangan data, jadi `docker compose down -v` tidak wajib.

## Validasi dan aturan bisnis

- Email tidak valid ditolak (422); telepon harus 9–15 digit; password minimal 8 karakter.
- Kuota penuh ditolak (409).
- Pendaftaran ganda (email sama, kegiatan sama) ditolak (409).
- Kuota kegiatan tidak boleh diubah di bawah jumlah peserta saat ini (409).
- Menghapus kegiatan ikut menghapus pendaftarnya; menghapus akun pembuat acara ikut menghapus kegiatannya.
- Pengecekan kuota memakai penguncian baris (`SELECT ... FOR UPDATE`) agar aman saat pendaftaran bersamaan.
- Akses tanpa login ditolak (401); aksi di luar hak peran ditolak (403).

## Operasional

- `/health` memeriksa koneksi database; Compose memiliki `healthcheck` untuk `db` dan `backend`.
- Log request dan peristiwa penting: `docker compose logs -f backend`.
- Data persisten lewat named volume `pgdata`.
- Konfigurasi lewat `.env` (contoh: `.env.example`), tanpa kredensial asli di repository.
- Jika database mati, API membalas 503 dengan pesan jelas dan indikator di UI berubah merah; backend tidak crash.

## Cara menjalankan

Prasyarat: Docker dan Docker Compose terpasang.

```bash
git clone https://github.com/JovanStefanus/event-registration-application
cd event-registration-application
cp .env.example .env        # lalu edit password, JWT_SECRET, dan akun admin
docker compose up -d --build
docker compose ps           # semua service harus running / healthy
```

Buka:
- Aplikasi: http://localhost:8080
- Dokumentasi API (Swagger): http://localhost:8000/docs
- Health check: http://localhost:8080/health

Masuk sebagai admin dengan `ADMIN_EMAIL` dan `ADMIN_PASSWORD` dari `.env`.

Menghentikan: `docker compose down` (data tetap ada). Hapus beserta data: `docker compose down -v`.

## Skenario pengujian

| # | Pengujian | Langkah | Hasil yang diharapkan |
|---|-----------|---------|-----------------------|
| 1 | Buat akun peserta | `/register`, pilih Peserta | Diarahkan ke `/login`, bisa masuk |
| 2 | Login salah | Password salah | Pesan "Email atau password salah" (401) |
| 3 | Pembuat acara belum disetujui | Daftar sebagai Pembuat acara, lalu coba masuk | Ditolak (403) dengan pesan menunggu persetujuan |
| 4 | Persetujuan admin | Admin, menu Pengguna, Setujui | Pembuat acara bisa masuk |
| 5 | Kelola kegiatan | Pembuat acara membuat, mengubah, menghapus kegiatan | Daftar kegiatan ikut berubah |
| 6 | Batas hak akses | Admin memanggil `POST /api/events`; pembuat acara mengubah kegiatan orang lain; peserta memanggil `POST /api/events` | Semuanya 403 |
| 7 | Daftar kegiatan | Peserta memilih kegiatan, Daftar | Muncul di "Pendaftaran saya", sisa kursi berkurang |
| 8 | Ubah / batalkan | Peserta mengubah atau membatalkan pendaftaran | Data berubah / hilang, kursi kembali |
| 9 | Email tidak valid | Isi email `abc` | Pesan error validasi (422), data tidak tersimpan |
| 10 | Kuota penuh | Kegiatan kuota 1, dua peserta berbeda mendaftar | Pendaftaran kedua ditolak (409) |
| 11 | Pendaftaran ganda | Daftar dua kali dengan email sama di kegiatan sama | Ditolak (409) |
| 12 | Persistensi | `docker compose down` lalu `docker compose up -d` | Akun, kegiatan, dan pendaftaran masih ada |
| 13 | Gangguan layanan | `docker compose stop db` | Indikator UI merah, API 503 dengan pesan jelas |
| 14 | Pemulihan | `docker compose start db` | Indikator kembali hijau, aplikasi normal |

Contoh uji lewat terminal:

```bash
# Email tidak valid -> 422
curl -i -X POST localhost:8080/api/auth/register -H "Content-Type: application/json" \
  -d '{"name":"Budi","email":"abc","phone":"081234567890","password":"rahasia123","role":"user"}'

# Tanpa login -> 401
curl -i localhost:8080/api/events

# Admin tidak boleh membuat kegiatan -> 403
TOKEN=$(curl -s -X POST localhost:8080/api/auth/login -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"<ADMIN_PASSWORD>"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -i -X POST localhost:8080/api/events -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"Uji admin","event_date":"2026-12-01T09:00","location":"Aula","quota":10}'

# Health check dan log
curl -i localhost:8080/health
docker compose logs -f backend
```

Simpan screenshot bukti pengujian di folder `docs/` (mis. `docs/uji-06-hak-akses.png`) dan rujuk di sini.

## Kaitan dengan materi Week 1–7

> Sesuaikan dengan silabus mata kuliah.

| Materi | Penerapan di proyek |
|--------|--------------------|
| Konsep Cloud Services / model layanan | Aplikasi dipaketkan sebagai layanan yang siap di-deploy ke cloud (IaaS/PaaS) |
| Virtualisasi vs container | Tiap komponen berjalan di container terisolasi |
| Docker image dan Dockerfile | `backend/Dockerfile`, `frontend/Dockerfile` |
| Networking container | Jaringan `frontend_net` dan `backend_net`, pemetaan port, reverse proxy Nginx |
| Storage / volume | Volume `pgdata` untuk persistensi database |
| Docker Compose / orkestrasi | `docker-compose.yml`, `depends_on` dengan healthcheck |
| Konfigurasi dan keamanan | `.env`, tanpa kredensial di repo, password di-hash, JWT, akses per peran, user non-root, DB tidak diekspos |
| Monitoring dan logging | `/health`, healthcheck Compose, log terstruktur |

## Catatan kontribusi

Proyek ini dikerjakan secara individu.

| Nama | NIM | Kontribusi |
|------|-----|-----------|
| (isi) | (isi) | Seluruh bagian: backend, frontend, Docker/Compose, database, dokumentasi, pengujian |

## Struktur repository

```
.
├── backend/              # FastAPI + Dockerfile
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/             # Nginx + halaman statis
│   ├── index.html, login.html, register.html, app.html
│   ├── assets/           # style.css, common.js, app.js
│   ├── nginx.conf
│   └── Dockerfile
├── docs/                 # diagram dan bukti pengujian
├── docker-compose.yml
├── .env.example
└── README.md
```
