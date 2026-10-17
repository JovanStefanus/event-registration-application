import csv
import io
import json
import logging
import mimetypes
import os
import re
import secrets
import smtplib
import threading
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Literal, Optional

import bcrypt
import jwt
import psycopg2
import psycopg2.extras
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg2 import errors
from pydantic import BaseModel, EmailStr, Field

# ---------- Logging ----------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("eventapp")

# ---------- Konfigurasi dari environment ----------
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "db"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("POSTGRES_DB", "eventdb"),
    "user": os.getenv("POSTGRES_USER", "eventuser"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
    "connect_timeout": 3,
}
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "8"))
ADMIN_NAME = os.getenv("ADMIN_NAME", "Administrator")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/app/uploads")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

GEOCODE_URL = os.getenv("GEOCODE_URL", "https://nominatim.openstreetmap.org")
GEOCODE_USER_AGENT = os.getenv("GEOCODE_USER_AGENT", "KarcisApp/2.0 (demo pendaftaran kegiatan)")

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER or "no-reply@karcis.local").strip()
SMTP_TLS = os.getenv("SMTP_TLS", "true").lower() != "false"
REMINDER_HOURS = int(os.getenv("REMINDER_HOURS", "24"))

PHONE_RE = r"^\+?\d{9,15}$"
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

app = FastAPI(title="Event Registration API", version="3.0.0")

# Penghitung sederhana untuk /metrics (Prometheus text format)
STARTED_AT = time.time()
REQUEST_COUNTS: dict[tuple[str, str, str], int] = {}
COUNT_LOCK = threading.Lock()
_ID_RE = re.compile(r"/\d+")


# ---------- Database helpers ----------
@contextmanager
def get_conn():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


# Skema dibuat idempotent: aman dijalankan ulang, dan memigrasi database versi lama
# tanpa menghapus data. Kolom dan tabel baru ditambahkan lewat ALTER/CREATE IF NOT EXISTS.
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    phone VARCHAR(20) NOT NULL,
    password_hash TEXT NOT NULL,
    role VARCHAR(10) NOT NULL DEFAULT 'user',
    status VARCHAR(10) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(10) NOT NULL DEFAULT 'active';
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('admin', 'organizer', 'user'));
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_status_check;
ALTER TABLE users ADD CONSTRAINT users_status_check CHECK (status IN ('active', 'pending'));

CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    event_date TIMESTAMP NOT NULL,
    location VARCHAR(150) NOT NULL,
    quota INTEGER NOT NULL CHECK (quota > 0),
    owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    poster_url TEXT,
    category VARCHAR(50) NOT NULL DEFAULT '',
    status VARCHAR(10) NOT NULL DEFAULT 'published',
    reminded_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
ALTER TABLE events ADD COLUMN IF NOT EXISTS owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE events ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION;
ALTER TABLE events ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;
ALTER TABLE events ADD COLUMN IF NOT EXISTS poster_url TEXT;
ALTER TABLE events ADD COLUMN IF NOT EXISTS category VARCHAR(50) NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN IF NOT EXISTS status VARCHAR(10) NOT NULL DEFAULT 'published';
ALTER TABLE events ADD COLUMN IF NOT EXISTS reminded_at TIMESTAMP;
ALTER TABLE events DROP CONSTRAINT IF EXISTS events_status_check;
ALTER TABLE events ADD CONSTRAINT events_status_check CHECK (status IN ('draft', 'published', 'cancelled'));

CREATE TABLE IF NOT EXISTS registrations (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(20) NOT NULL,
    code VARCHAR(12),
    checked_in_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (event_id, email)
);
ALTER TABLE registrations ADD COLUMN IF NOT EXISTS code VARCHAR(12);
ALTER TABLE registrations ADD COLUMN IF NOT EXISTS checked_in_at TIMESTAMP;
UPDATE registrations SET code = UPPER(SUBSTRING(MD5(RANDOM()::TEXT) FROM 1 FOR 8)) WHERE code IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS registrations_code_idx ON registrations (code);

-- Pertanyaan tambahan yang didefinisikan pembuat acara untuk tiap kegiatan
CREATE TABLE IF NOT EXISTS event_fields (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    label VARCHAR(100) NOT NULL,
    field_type VARCHAR(20) NOT NULL DEFAULT 'text',
    required BOOLEAN NOT NULL DEFAULT FALSE,
    options TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0
);
ALTER TABLE event_fields DROP CONSTRAINT IF EXISTS event_fields_type_check;
ALTER TABLE event_fields ADD CONSTRAINT event_fields_type_check
    CHECK (field_type IN ('text', 'number', 'select', 'checkbox'));

-- Jawaban peserta untuk pertanyaan tambahan
CREATE TABLE IF NOT EXISTS registration_answers (
    id SERIAL PRIMARY KEY,
    registration_id INTEGER NOT NULL REFERENCES registrations(id) ON DELETE CASCADE,
    field_id INTEGER NOT NULL REFERENCES event_fields(id) ON DELETE CASCADE,
    value TEXT NOT NULL DEFAULT '',
    UNIQUE (registration_id, field_id)
);

-- Daftar tunggu ketika kuota penuh
CREATE TABLE IF NOT EXISTS waitlist (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(20) NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'waiting',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (event_id, email)
);
ALTER TABLE waitlist DROP CONSTRAINT IF EXISTS waitlist_status_check;
ALTER TABLE waitlist ADD CONSTRAINT waitlist_status_check
    CHECK (status IN ('waiting', 'notified', 'converted', 'cancelled'));

-- Ulasan dan rating setelah kegiatan selesai
CREATE TABLE IF NOT EXISTS reviews (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (event_id, user_id)
);

CREATE INDEX IF NOT EXISTS events_date_idx ON events (event_date);
CREATE INDEX IF NOT EXISTS events_category_idx ON events (category);
CREATE INDEX IF NOT EXISTS events_status_idx ON events (status);
CREATE INDEX IF NOT EXISTS reg_event_idx ON registrations (event_id);
CREATE INDEX IF NOT EXISTS reg_user_idx ON registrations (user_id);
CREATE INDEX IF NOT EXISTS waitlist_event_idx ON waitlist (event_id);
CREATE INDEX IF NOT EXISTS reviews_event_idx ON reviews (event_id);
"""


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def init_db(retries: int = 10, delay: float = 2.0):
    for attempt in range(1, retries + 1):
        try:
            with get_conn() as conn, cursor(conn) as cur:
                cur.execute(SCHEMA)
                if ADMIN_EMAIL and ADMIN_PASSWORD:
                    cur.execute("SELECT 1 FROM users WHERE email=%s", (ADMIN_EMAIL,))
                    if not cur.fetchone():
                        cur.execute(
                            "INSERT INTO users (name, email, phone, password_hash, role, status) "
                            "VALUES (%s, %s, %s, %s, 'admin', 'active')",
                            (ADMIN_NAME, ADMIN_EMAIL, "000000000", hash_password(ADMIN_PASSWORD)),
                        )
                        log.info("Akun admin awal dibuat: %s", ADMIN_EMAIL)
            log.info("Database siap (percobaan %d)", attempt)
            return
        except psycopg2.OperationalError as e:
            log.warning("Database belum siap (%d/%d): %s", attempt, retries, e)
            time.sleep(delay)
    raise RuntimeError("Tidak bisa terhubung ke database")


# ---------- Email (notifikasi) ----------
def send_email(to: str, subject: str, body: str):
    """Kirim email. Bila SMTP tidak dikonfigurasi, cukup dicatat ke log (mode dev)."""
    if not SMTP_HOST:
        log.info("[email:dev] ke=%s subjek=%s", to, subject)
        return
    try:
        msg = EmailMessage()
        msg["From"] = SMTP_FROM
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            if SMTP_TLS:
                server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        log.info("Email terkirim ke %s (%s)", to, subject)
    except Exception as e:  # pragma: no cover - bergantung jaringan
        log.warning("Gagal mengirim email ke %s: %s", to, e)


def email_async(to: str, subject: str, body: str):
    threading.Thread(target=send_email, args=(to, subject, body), daemon=True).start()


def app_link(path: str) -> str:
    return f"{PUBLIC_BASE_URL}{path}" if PUBLIC_BASE_URL else path


def run_reminders():
    """Kirim pengingat H-1 sekali untuk setiap kegiatan yang belum diingatkan."""
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute(
            "SELECT id, name, event_date, location FROM events "
            "WHERE status='published' AND reminded_at IS NULL "
            "AND event_date > NOW() AND event_date <= NOW() + (%s || ' hours')::interval",
            (REMINDER_HOURS,),
        )
        events = cur.fetchall()
        for ev in events:
            cur.execute("SELECT name, email FROM registrations WHERE event_id=%s", (ev["id"],))
            for r in cur.fetchall():
                email_async(
                    r["email"],
                    f"Pengingat: {ev['name']}",
                    f"Halo {r['name']},\n\nKegiatan \"{ev['name']}\" akan berlangsung pada "
                    f"{ev['event_date']:%d-%m-%Y %H:%M} di {ev['location']}.\n\nSampai jumpa!\nKarcis",
                )
            cur.execute("UPDATE events SET reminded_at=NOW() WHERE id=%s", (ev["id"],))
            log.info("Pengingat dikirim untuk kegiatan id=%d (%d pendaftar)", ev["id"], len(events))


def reminder_loop(stop: threading.Event):
    while not stop.wait(1800):  # tiap 30 menit
        try:
            run_reminders()
        except Exception as e:  # pragma: no cover
            log.warning("Pengingat gagal dijalankan: %s", e)


@app.on_event("startup")
def on_startup():
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET belum diatur di environment")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    init_db()
    app.state.reminder_stop = threading.Event()
    threading.Thread(target=reminder_loop, args=(app.state.reminder_stop,), daemon=True).start()


@app.on_event("shutdown")
def on_shutdown():
    stop = getattr(app.state, "reminder_stop", None)
    if stop:
        stop.set()


# ---------- Error handler, middleware & metrik ----------
@app.exception_handler(psycopg2.OperationalError)
@app.exception_handler(psycopg2.InterfaceError)
async def db_unavailable(request: Request, exc: Exception):
    log.error("Database tidak tersedia: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "Database tidak tersedia, coba lagi nanti"})


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response: Response = await call_next(request)
    ms = (time.time() - start) * 1000
    log.info("%s %s -> %d (%.1f ms)", request.method, request.url.path, response.status_code, ms)
    key = (request.method, _ID_RE.sub("/{id}", request.url.path), str(response.status_code))
    with COUNT_LOCK:
        REQUEST_COUNTS[key] = REQUEST_COUNTS.get(key, 0) + 1
    return response


@app.get("/metrics")
def metrics():
    lines = [
        "# HELP karcis_uptime_seconds Lama proses backend berjalan",
        "# TYPE karcis_uptime_seconds gauge",
        f"karcis_uptime_seconds {time.time() - STARTED_AT:.1f}",
        "# HELP karcis_requests_total Jumlah permintaan HTTP",
        "# TYPE karcis_requests_total counter",
    ]
    with COUNT_LOCK:
        snapshot = dict(REQUEST_COUNTS)
    for (method, path, status), count in sorted(snapshot.items()):
        lines.append(f'karcis_requests_total{{method="{method}",path="{path}",status="{status}"}} {count}')
    lines.append("# HELP karcis_events_total Jumlah kegiatan per status")
    lines.append("# TYPE karcis_events_total gauge")
    try:
        with get_conn() as conn, cursor(conn) as cur:
            cur.execute("SELECT status, COUNT(*) AS c FROM events GROUP BY status")
            for row in cur.fetchall():
                lines.append(f'karcis_events_total{{status="{row["status"]}"}} {row["c"]}')
            cur.execute("SELECT COUNT(*) AS c FROM registrations")
            lines.append(f'karcis_registrations_total {cur.fetchone()["c"]}')
    except Exception:
        pass
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


# ---------- Model & validasi input ----------
class RegisterIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    phone: str = Field(..., pattern=PHONE_RE)
    password: str = Field(..., min_length=8, max_length=72)
    role: Literal["user", "organizer"] = "user"


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=72)


class FieldIn(BaseModel):
    id: Optional[int] = None
    label: str = Field(..., min_length=1, max_length=100)
    field_type: Literal["text", "number", "select", "checkbox"] = "text"
    required: bool = False
    options: list[str] = Field(default_factory=list)


class EventIn(BaseModel):
    name: str = Field(..., min_length=3, max_length=100)
    description: str = Field("", max_length=2000)
    event_date: datetime
    location: str = Field(..., min_length=2, max_length=150)
    quota: int = Field(..., ge=1, le=10000)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    category: str = Field("", max_length=50)
    status: Literal["draft", "published", "cancelled"] = "published"
    fields: list[FieldIn] = Field(default_factory=list)


class RegistrationIn(BaseModel):
    event_id: int
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    phone: str = Field(..., pattern=PHONE_RE)
    answers: dict[str, str] = Field(default_factory=dict)


class WaitlistIn(BaseModel):
    event_id: int
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    phone: str = Field(..., pattern=PHONE_RE)


class CheckinIn(BaseModel):
    code: str = Field(..., min_length=4, max_length=12)


class ReviewIn(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str = Field("", max_length=1000)


# ---------- Autentikasi & otorisasi ----------
bearer = HTTPBearer(auto_error=False)


def make_token(user: dict) -> str:
    payload = {
        "sub": str(user["id"]),
        "role": user["role"],
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def public_user(u: dict) -> dict:
    return {k: u[k] for k in ("id", "name", "email", "phone", "role", "status")}


def current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> dict:
    if not creds:
        raise HTTPException(status_code=401, detail="Silakan login terlebih dahulu")
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=["HS256"])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Token tidak valid atau sudah kedaluwarsa")
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, name, email, phone, role, status FROM users WHERE id=%s", (user_id,))
        user = cur.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="Akun tidak ditemukan")
    if user["status"] != "active":
        raise HTTPException(status_code=403, detail="Akun belum aktif")
    return dict(user)


def require_roles(*roles: str):
    """Dependency: hanya peran tertentu yang boleh mengakses endpoint."""

    def dependency(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Peran Anda tidak diizinkan melakukan aksi ini")
        return user

    return dependency


@app.post("/api/auth/register", status_code=201)
def register(body: RegisterIn):
    email = body.email.lower()
    status = "pending" if body.role == "organizer" else "active"
    try:
        with get_conn() as conn, cursor(conn) as cur:
            cur.execute(
                "INSERT INTO users (name, email, phone, password_hash, role, status) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (body.name.strip(), email, body.phone, hash_password(body.password), body.role, status),
            )
            user_id = cur.fetchone()["id"]
    except errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="Email sudah terdaftar sebagai akun")
    log.info("Akun baru id=%d peran=%s status=%s", user_id, body.role, status)
    message = (
        "Akun dibuat. Admin perlu menyetujui akun pembuat acara sebelum Anda bisa masuk."
        if status == "pending"
        else "Akun dibuat. Silakan masuk."
    )
    return {"id": user_id, "role": body.role, "status": status, "message": message}


@app.post("/api/auth/login")
def login(body: LoginIn):
    email = body.email.lower()
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = cur.fetchone()
    if not user or not verify_password(body.password, user["password_hash"]):
        log.warning("Login gagal untuk email=%s", email)
        raise HTTPException(status_code=401, detail="Email atau password salah")
    if user["status"] != "active":
        log.warning("Login ditolak, akun menunggu persetujuan: user id=%d", user["id"])
        raise HTTPException(status_code=403, detail="Akun pembuat acara Anda belum disetujui admin")
    log.info("Login berhasil: user id=%d peran=%s", user["id"], user["role"])
    return {"access_token": make_token(user), "user": public_user(user)}


@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)):
    return public_user(user)


@app.post("/api/auth/refresh")
def refresh(user: dict = Depends(current_user)):
    """Perpanjang sesi: tukar token yang masih valid dengan token baru."""
    return {"access_token": make_token(user), "user": public_user(user)}


# ---------- Health check ----------
@app.get("/health")
def health():
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return {"status": "ok", "database": "up"}
    except Exception as e:
        log.error("Health check gagal: %s", e)
        raise HTTPException(status_code=503, detail="Database tidak tersedia")


# ---------- Geocode (proxy Nominatim / OpenStreetMap) ----------
_geo_cache: dict[str, tuple[float, object]] = {}
_geo_lock = threading.Lock()


def _nominatim(path: str, params: dict) -> object:
    query = urllib.parse.urlencode({"format": "jsonv2", "addressdetails": 1, **params})
    key = f"{path}?{query}"
    with _geo_lock:
        hit = _geo_cache.get(key)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
    req = urllib.request.Request(
        f"{GEOCODE_URL.rstrip('/')}{path}?{query}",
        headers={"User-Agent": GEOCODE_USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        log.warning("Geocode gagal: %s", e)
        raise HTTPException(status_code=502, detail="Layanan peta sedang tidak tersedia, isi alamat manual")
    with _geo_lock:
        _geo_cache[key] = (time.time(), data)
    return data


@app.get("/api/geocode/search")
def geocode_search(q: str = "", limit: int = 5, user: dict = Depends(current_user)):
    q = q.strip()
    if len(q) < 3:
        raise HTTPException(status_code=422, detail="Alamat terlalu pendek")
    data = _nominatim("/search", {"q": q, "limit": max(1, min(limit, 10))})
    return [
        {"label": item.get("display_name", ""), "lat": float(item["lat"]), "lon": float(item["lon"])}
        for item in data
    ]


@app.get("/api/geocode/reverse")
def geocode_reverse(lat: float, lon: float, user: dict = Depends(current_user)):
    data = _nominatim("/reverse", {"lat": lat, "lon": lon, "zoom": 18})
    if not isinstance(data, dict) or "lat" not in data:
        raise HTTPException(status_code=404, detail="Alamat tidak ditemukan pada titik itu")
    return {"label": data.get("display_name", ""), "lat": float(data["lat"]), "lon": float(data["lon"])}


# ---------- Berkas unggahan (poster) ----------
def delete_upload(url: Optional[str]):
    if not url or not url.startswith("/api/uploads/"):
        return
    name = os.path.basename(url)
    path = os.path.join(UPLOAD_DIR, name)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError as e:  # pragma: no cover
        log.warning("Gagal menghapus berkas %s: %s", path, e)


@app.get("/api/uploads/{name}")
def serve_upload(name: str):
    safe = os.path.basename(name)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", safe):
        raise HTTPException(status_code=404, detail="Berkas tidak ditemukan")
    path = os.path.join(UPLOAD_DIR, safe)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Berkas tidak ditemukan")
    media = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "public, max-age=86400"})


# ---------- Manajemen pengguna (admin) ----------
@app.get("/api/users")
def list_users(
    user: dict = Depends(require_roles("admin")),
    q: str = "",
    status: str = "",
    page: int = 1,
    limit: int = 10,
):
    page = max(1, page)
    limit = max(1, min(limit, 100))
    where, params = [], []
    if q.strip():
        where.append("(name ILIKE %s OR email ILIKE %s)")
        params += [f"%{q.strip()}%", f"%{q.strip()}%"]
    if status in ("active", "pending"):
        where.append("status=%s")
        params.append(status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute(f"SELECT COUNT(*) AS c FROM users {clause}", params)
        total = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM users WHERE status='pending'")
        pending = cur.fetchone()["c"]
        cur.execute(
            "SELECT id, name, email, phone, role, status, created_at FROM users "
            f"{clause} ORDER BY (status = 'pending') DESC, id LIMIT %s OFFSET %s",
            params + [limit, (page - 1) * limit],
        )
        items = [dict(r) for r in cur.fetchall()]
    return {
        "items": items,
        "total": total,
        "pending": pending,
        "page": page,
        "pages": max(1, -(-total // limit)) if total else 1,
        "limit": limit,
    }


@app.post("/api/users/{user_id}/approve")
def approve_user(user_id: int, admin: dict = Depends(require_roles("admin"))):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, role, status FROM users WHERE id=%s FOR UPDATE", (user_id,))
        target = cur.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Akun tidak ditemukan")
        if target["role"] != "organizer" or target["status"] != "pending":
            raise HTTPException(status_code=409, detail="Akun ini tidak sedang menunggu persetujuan")
        cur.execute("UPDATE users SET status='active' WHERE id=%s", (user_id,))
    log.info("Pembuat acara disetujui: user id=%d oleh admin id=%d", user_id, admin["id"])
    return {"id": user_id, "status": "active"}


@app.delete("/api/users/{user_id}", status_code=204)
def delete_user(user_id: int, admin: dict = Depends(require_roles("admin"))):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, role FROM users WHERE id=%s FOR UPDATE", (user_id,))
        target = cur.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Akun tidak ditemukan")
        if target["role"] == "admin":
            raise HTTPException(status_code=403, detail="Akun admin tidak dapat dihapus")
        cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
    log.info("Akun dihapus: user id=%d oleh admin id=%d", user_id, admin["id"])
    return Response(status_code=204)


# ---------- Kegiatan ----------
EVENT_SELECT = (
    "SELECT e.*, u.name AS owner_name, COUNT(r.id)::int AS registered, "
    "(SELECT COALESCE(AVG(rv.rating), 0) FROM reviews rv WHERE rv.event_id = e.id) AS rating_avg, "
    "(SELECT COUNT(*) FROM reviews rv WHERE rv.event_id = e.id)::int AS rating_count "
    "FROM events e "
    "LEFT JOIN users u ON u.id = e.owner_id "
    "LEFT JOIN registrations r ON r.event_id = e.id "
)


def event_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "event_date": row["event_date"],
        "location": row["location"],
        "quota": row["quota"],
        "owner_id": row["owner_id"],
        "owner_name": row["owner_name"],
        "registered": row["registered"],
        "remaining": row["quota"] - row["registered"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "poster_url": row["poster_url"],
        "category": row["category"],
        "status": row["status"],
        "rating_avg": round(float(row["rating_avg"] or 0), 2),
        "rating_count": row["rating_count"],
    }


def field_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "label": row["label"],
        "field_type": row["field_type"],
        "required": row["required"],
        "options": [o for o in (row["options"] or "").split("|") if o],
    }


def load_fields(cur, event_ids: list[int]) -> dict[int, list[dict]]:
    if not event_ids:
        return {}
    cur.execute(
        "SELECT * FROM event_fields WHERE event_id = ANY(%s) ORDER BY position, id",
        (list(event_ids),),
    )
    out: dict[int, list[dict]] = {}
    for r in cur.fetchall():
        out.setdefault(r["event_id"], []).append(field_dict(r))
    return out


def fetch_event(cur, event_id: int) -> Optional[dict]:
    cur.execute(EVENT_SELECT + "WHERE e.id=%s GROUP BY e.id, u.name", (event_id,))
    row = cur.fetchone()
    if not row:
        return None
    data = event_dict(row)
    data["fields"] = load_fields(cur, [event_id]).get(event_id, [])
    return data


def count_registered(cur, event_id: int, exclude_reg_id: Optional[int] = None) -> int:
    if exclude_reg_id:
        cur.execute(
            "SELECT COUNT(*) AS c FROM registrations WHERE event_id=%s AND id<>%s",
            (event_id, exclude_reg_id),
        )
    else:
        cur.execute("SELECT COUNT(*) AS c FROM registrations WHERE event_id=%s", (event_id,))
    return cur.fetchone()["c"]


def save_fields(cur, event_id: int, fields: list[FieldIn]):
    kept_ids = [f.id for f in fields if f.id]
    if kept_ids:
        cur.execute(
            "DELETE FROM event_fields WHERE event_id=%s AND NOT (id = ANY(%s))",
            (event_id, kept_ids),
        )
    else:
        cur.execute("DELETE FROM event_fields WHERE event_id=%s", (event_id,))
    for pos, f in enumerate(fields):
        options = "|".join(o.strip() for o in f.options if o.strip())
        if f.id:
            cur.execute(
                "UPDATE event_fields SET label=%s, field_type=%s, required=%s, options=%s, position=%s "
                "WHERE id=%s AND event_id=%s",
                (f.label.strip(), f.field_type, f.required, options, pos, f.id, event_id),
            )
        else:
            cur.execute(
                "INSERT INTO event_fields (event_id, label, field_type, required, options, position) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (event_id, f.label.strip(), f.field_type, f.required, options, pos),
            )


def validate_answers(cur, event_id: int, answers: dict[str, str]) -> list[tuple[int, str]]:
    fields = load_fields(cur, [event_id]).get(event_id, [])
    values: list[tuple[int, str]] = []
    for f in fields:
        raw = str(answers.get(str(f["id"]), "")).strip()
        if f["required"] and not raw:
            raise HTTPException(status_code=422, detail=f"Pertanyaan \"{f['label']}\" wajib diisi")
        if f["field_type"] == "select" and raw and raw not in f["options"]:
            raise HTTPException(status_code=422, detail=f"Pilihan \"{f['label']}\" tidak valid")
        if f["field_type"] == "number" and raw:
            try:
                float(raw)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"\"{f['label']}\" harus berupa angka")
        if raw:
            values.append((f["id"], raw))
    return values


def visibility_clause(user: dict) -> tuple[str, list]:
    if user["role"] == "admin":
        return "TRUE", []
    if user["role"] == "organizer":
        return "(e.status='published' OR e.owner_id=%s)", [user["id"]]
    return "e.status='published'", []


@app.get("/api/events")
def list_events(
    user: dict = Depends(current_user),
    q: str = "",
    category: str = "",
    date_from: str = "",
    date_to: str = "",
    available: bool = False,
    page: int = 1,
    limit: int = 12,
):
    page = max(1, page)
    limit = max(1, min(limit, 100))
    vis, params = visibility_clause(user)
    where = [vis]
    if q.strip():
        where.append("(e.name ILIKE %s OR e.location ILIKE %s OR e.description ILIKE %s OR e.category ILIKE %s)")
        like = f"%{q.strip()}%"
        params += [like, like, like, like]
    if category.strip():
        where.append("e.category ILIKE %s")
        params.append(f"%{category.strip()}%")
    if date_from:
        where.append("e.event_date >= %s")
        params.append(date_from)
    if date_to:
        where.append("e.event_date <= %s")
        params.append(date_to)
    if available:
        where.append("e.quota > (SELECT COUNT(*) FROM registrations r WHERE r.event_id = e.id)")
    clause = " AND ".join(where)
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute(f"SELECT COUNT(*) AS c FROM events e WHERE {clause}", params)
        total = cur.fetchone()["c"]
        cur.execute(
            EVENT_SELECT + f"WHERE {clause} GROUP BY e.id, u.name ORDER BY e.event_date, e.id "
            "LIMIT %s OFFSET %s",
            params + [limit, (page - 1) * limit],
        )
        items = [event_dict(r) for r in cur.fetchall()]
        fields = load_fields(cur, [e["id"] for e in items])
    for e in items:
        e["fields"] = fields.get(e["id"], [])
    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": max(1, -(-total // limit)) if total else 1,
        "limit": limit,
    }


@app.get("/api/meta/categories")
def list_categories(user: dict = Depends(current_user)):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT DISTINCT category FROM events WHERE category <> '' ORDER BY category")
        return [r["category"] for r in cur.fetchall()]


@app.get("/api/stats")
def stats(user: dict = Depends(current_user)):
    with get_conn() as conn, cursor(conn) as cur:
        if user["role"] == "admin":
            cur.execute("SELECT COUNT(*) AS c FROM events")
            events_total = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM registrations")
            regs_total = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM users")
            users_total = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM users WHERE status='pending'")
            pending = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) AS c FROM waitlist WHERE status='waiting'")
            waiting = cur.fetchone()["c"]
            return {"events": events_total, "registrations": regs_total, "users": users_total,
                    "pending": pending, "waitlist": waiting}
        if user["role"] == "organizer":
            cur.execute("SELECT COUNT(*) AS c, COALESCE(SUM(quota), 0) AS cap FROM events WHERE owner_id=%s", (user["id"],))
            row = cur.fetchone()
            cur.execute(
                "SELECT COUNT(*) AS c FROM registrations r JOIN events e ON e.id = r.event_id WHERE e.owner_id=%s",
                (user["id"],),
            )
            reg = cur.fetchone()["c"]
            cur.execute(
                "SELECT COUNT(*) AS c FROM waitlist w JOIN events e ON e.id = w.event_id "
                "WHERE e.owner_id=%s AND w.status='waiting'",
                (user["id"],),
            )
            waiting = cur.fetchone()["c"]
            cap = row["cap"] or 0
            return {"my_events": row["c"], "my_registrations": reg, "capacity": cap, "registered": reg,
                    "fill": round(reg / cap * 100) if cap else 0, "waitlist": waiting}
        # peserta
        cur.execute(
            "SELECT COUNT(*) AS c FROM events WHERE status='published' "
            "AND quota > (SELECT COUNT(*) FROM registrations r WHERE r.event_id = events.id)"
        )
        available = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM registrations WHERE user_id=%s", (user["id"],))
        mine = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM waitlist WHERE user_id=%s AND status='waiting'", (user["id"],))
        waiting = cur.fetchone()["c"]
        return {"available": available, "my_registrations": mine, "waitlist": waiting}


@app.get("/api/events/{event_id}")
def get_event(event_id: int, user: dict = Depends(current_user)):
    with get_conn() as conn, cursor(conn) as cur:
        event = fetch_event(cur, event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        vis, params = visibility_clause(user)
        cur.execute(f"SELECT 1 FROM events e WHERE e.id=%s AND {vis}", (event_id, *params))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        my_reg = None
        if user["role"] == "user":
            cur.execute(
                "SELECT id, code, checked_in_at FROM registrations WHERE event_id=%s AND user_id=%s",
                (event_id, user["id"]),
            )
            row = cur.fetchone()
            my_reg = dict(row) if row else None
        cur.execute(
            "SELECT rv.id, rv.rating, rv.comment, rv.created_at, u.name AS user_name "
            "FROM reviews rv JOIN users u ON u.id = rv.user_id "
            "WHERE rv.event_id=%s ORDER BY rv.id DESC",
            (event_id,),
        )
        event["reviews"] = [dict(r) for r in cur.fetchall()]
        event["my_registration"] = my_reg
        event["can_review"] = bool(
            my_reg and user["role"] == "user" and event["event_date"] < datetime.now()
        )
    return event


@app.post("/api/events", status_code=201)
def create_event(body: EventIn, user: dict = Depends(require_roles("organizer"))):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute(
            "INSERT INTO events (name, description, event_date, location, quota, owner_id, "
            "latitude, longitude, category, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                body.name.strip(), body.description, body.event_date, body.location.strip(),
                body.quota, user["id"], body.latitude, body.longitude, body.category.strip(), body.status,
            ),
        )
        event_id = cur.fetchone()["id"]
        save_fields(cur, event_id, body.fields)
        result = fetch_event(cur, event_id)
    log.info("Kegiatan dibuat id=%d oleh pembuat acara id=%d", event_id, user["id"])
    return result


@app.put("/api/events/{event_id}")
def update_event(event_id: int, body: EventIn, user: dict = Depends(require_roles("organizer"))):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, owner_id FROM events WHERE id=%s FOR UPDATE", (event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        if event["owner_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Anda hanya dapat mengubah kegiatan milik sendiri")
        registered = count_registered(cur, event_id)
        if body.quota < registered:
            raise HTTPException(
                status_code=409,
                detail=f"Kuota tidak boleh kurang dari jumlah peserta saat ini ({registered})",
            )
        cur.execute(
            "UPDATE events SET name=%s, description=%s, event_date=%s, location=%s, quota=%s, "
            "latitude=%s, longitude=%s, category=%s, status=%s WHERE id=%s",
            (
                body.name.strip(), body.description, body.event_date, body.location.strip(), body.quota,
                body.latitude, body.longitude, body.category.strip(), body.status, event_id,
            ),
        )
        save_fields(cur, event_id, body.fields)
        result = fetch_event(cur, event_id)
    log.info("Kegiatan diubah id=%d", event_id)
    return result


@app.delete("/api/events/{event_id}", status_code=204)
def delete_event(event_id: int, user: dict = Depends(require_roles("admin", "organizer"))):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, owner_id, poster_url FROM events WHERE id=%s FOR UPDATE", (event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        if user["role"] == "organizer" and event["owner_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Anda hanya dapat menghapus kegiatan milik sendiri")
        cur.execute("DELETE FROM events WHERE id=%s", (event_id,))
    delete_upload(event["poster_url"])
    log.info("Kegiatan dihapus id=%d oleh %s id=%d (pendaftaran terkait ikut terhapus)", event_id, user["role"], user["id"])
    return Response(status_code=204)


@app.post("/api/events/{event_id}/poster")
async def upload_poster(
    event_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(require_roles("organizer", "admin")),
):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, owner_id, poster_url FROM events WHERE id=%s FOR UPDATE", (event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        if user["role"] == "organizer" and event["owner_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Anda hanya dapat mengubah kegiatan milik sendiri")
        old_url = event["poster_url"]
    ext = ALLOWED_IMAGE_TYPES.get(file.content_type or "")
    if not ext:
        raise HTTPException(status_code=400, detail="Format gambar harus JPG, PNG, WEBP, atau GIF")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Ukuran gambar maksimal {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(status_code=400, detail="Berkas kosong")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, name), "wb") as fh:
        fh.write(data)
    url = f"/api/uploads/{name}"
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("UPDATE events SET poster_url=%s WHERE id=%s", (url, event_id))
        result = fetch_event(cur, event_id)
    delete_upload(old_url)
    log.info("Poster kegiatan id=%d diperbarui (%d bytes)", event_id, len(data))
    return result


@app.delete("/api/events/{event_id}/poster")
def remove_poster(event_id: int, user: dict = Depends(require_roles("organizer", "admin"))):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, owner_id, poster_url FROM events WHERE id=%s FOR UPDATE", (event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        if user["role"] == "organizer" and event["owner_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Anda hanya dapat mengubah kegiatan milik sendiri")
        cur.execute("UPDATE events SET poster_url=NULL WHERE id=%s", (event_id,))
        result = fetch_event(cur, event_id)
    delete_upload(event["poster_url"])
    return result


@app.get("/api/events/{event_id}/participants")
def list_participants(event_id: int, user: dict = Depends(current_user)):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, owner_id FROM events WHERE id=%s", (event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        cur.execute(
            "SELECT r.id, r.name, r.email, r.phone, r.code, r.checked_in_at, r.created_at, "
            "COALESCE(json_agg(json_build_object('label', f.label, 'value', a.value)) "
            "FILTER (WHERE f.id IS NOT NULL), '[]') AS answers "
            "FROM registrations r "
            "LEFT JOIN registration_answers a ON a.registration_id = r.id "
            "LEFT JOIN event_fields f ON f.id = a.field_id "
            "WHERE r.event_id=%s GROUP BY r.id ORDER BY r.id",
            (event_id,),
        )
        rows = cur.fetchall()
    full_access = user["role"] == "admin" or (user["role"] == "organizer" and event["owner_id"] == user["id"])
    if full_access:
        return [dict(r) for r in rows]
    # Peran lain hanya melihat nama (email & telepon peserta disembunyikan)
    return [{"id": r["id"], "name": r["name"]} for r in rows]


@app.get("/api/events/{event_id}/participants.csv")
def participants_csv(event_id: int, user: dict = Depends(require_roles("organizer", "admin"))):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, owner_id, name FROM events WHERE id=%s", (event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        if user["role"] == "organizer" and event["owner_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Anda hanya dapat mengekspor kegiatan milik sendiri")
        cur.execute(
            "SELECT name, email, phone, code, checked_in_at, created_at FROM registrations "
            "WHERE event_id=%s ORDER BY id",
            (event_id,),
        )
        rows = cur.fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Nama", "Email", "Telepon", "Kode tiket", "Check-in", "Terdaftar"])
    for r in rows:
        writer.writerow([
            r["name"], r["email"], r["phone"], r["code"] or "",
            r["checked_in_at"].strftime("%Y-%m-%d %H:%M") if r["checked_in_at"] else "",
            r["created_at"].strftime("%Y-%m-%d %H:%M") if r["created_at"] else "",
        ])
    filename = f"peserta-{event_id}.csv"
    return Response(
        "\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/events/{event_id}/calendar.ics")
def event_calendar(event_id: int, user: dict = Depends(current_user)):
    with get_conn() as conn, cursor(conn) as cur:
        event = fetch_event(cur, event_id)
        if event:
            vis, vis_params = visibility_clause(user)
            cur.execute(f"SELECT 1 FROM events e WHERE e.id=%s AND {vis}", (event_id, *vis_params))
            if not cur.fetchone():
                event = None
    if not event:
        raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
    start = event["event_date"].replace(tzinfo=timezone.utc)
    end = start + timedelta(hours=2)
    stamp = datetime.now(timezone.utc)

    def fmt(dt: datetime) -> str:
        return dt.strftime("%Y%m%dT%H%M%SZ")

    def esc(text: str) -> str:
        return (text or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Karcis//Pendaftaran Kegiatan//ID",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:event-{event['id']}@karcis",
        f"DTSTAMP:{fmt(stamp)}",
        f"DTSTART:{fmt(start)}",
        f"DTEND:{fmt(end)}",
        f"SUMMARY:{esc(event['name'])}",
        f"LOCATION:{esc(event['location'])}",
        f"DESCRIPTION:{esc(event['description'])}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return Response(
        "\r\n".join(lines) + "\r\n",
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="kegiatan-{event_id}.ics"'},
    )


@app.get("/api/events/{event_id}/waitlist")
def event_waitlist(event_id: int, user: dict = Depends(current_user)):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, owner_id FROM events WHERE id=%s", (event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        if user["role"] == "user":
            clause, params = "AND w.user_id=%s", [event_id, user["id"]]
        elif user["role"] == "organizer":
            if event["owner_id"] != user["id"]:
                raise HTTPException(status_code=403, detail="Anda hanya dapat melihat kegiatan milik sendiri")
            clause, params = "", [event_id]
        else:
            clause, params = "", [event_id]
        cur.execute(
            "SELECT w.id, w.name, w.email, w.phone, w.status, w.created_at FROM waitlist w "
            f"WHERE w.event_id=%s {clause} ORDER BY w.id",
            params,
        )
        return [dict(r) for r in cur.fetchall()]


@app.post("/api/events/{event_id}/reviews", status_code=201)
def create_review(event_id: int, body: ReviewIn, user: dict = Depends(require_roles("user"))):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, event_date FROM events WHERE id=%s", (event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        if event["event_date"] >= datetime.now():
            raise HTTPException(status_code=409, detail="Ulasan hanya bisa ditulis setelah kegiatan selesai")
        cur.execute(
            "SELECT id FROM registrations WHERE event_id=%s AND user_id=%s",
            (event_id, user["id"]),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=403, detail="Hanya peserta kegiatan yang dapat memberi ulasan")
        try:
            cur.execute(
                "INSERT INTO reviews (event_id, user_id, rating, comment) VALUES (%s,%s,%s,%s) RETURNING id",
                (event_id, user["id"], body.rating, body.comment.strip()),
            )
        except errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="Anda sudah memberi ulasan untuk kegiatan ini")
        review_id = cur.fetchone()["id"]
    log.info("Ulasan dibuat id=%d event id=%d user id=%d", review_id, event_id, user["id"])
    return {"id": review_id, "rating": body.rating, "comment": body.comment.strip()}


# ---------- Pendaftaran ----------
REG_SELECT = (
    "SELECT r.id, r.event_id, e.name AS event_name, e.owner_id AS event_owner_id, "
    "e.event_date AS event_date, "
    "r.user_id, r.name, r.email, r.phone, r.code, r.checked_in_at, r.created_at "
    "FROM registrations r JOIN events e ON e.id = r.event_id"
)


def new_code() -> str:
    return secrets.token_hex(4).upper()


def promote_waitlist(cur, event_id: int) -> Optional[dict]:
    """Ketika satu kursi kosong, tawarkan ke pendaftar daftar tunggu paling awal."""
    cur.execute(
        "SELECT id, quota FROM events WHERE id=%s FOR UPDATE", (event_id,)
    )
    event = cur.fetchone()
    if not event:
        return None
    if count_registered(cur, event_id) >= event["quota"]:
        return None
    cur.execute(
        "SELECT id, user_id, name, email, phone FROM waitlist "
        "WHERE event_id=%s AND status='waiting' ORDER BY id LIMIT 1 FOR UPDATE",
        (event_id,),
    )
    entry = cur.fetchone()
    if not entry:
        return None
    # Pertanyaan wajib tidak dapat dijawab otomatis: beri tahu agar mendaftar sendiri
    # sehingga validasi pertanyaan wajib tetap berlaku (tidak melanggar invariant).
    cur.execute("SELECT COUNT(*) AS c FROM event_fields WHERE event_id=%s AND required=TRUE", (event_id,))
    if cur.fetchone()["c"]:
        cur.execute("UPDATE waitlist SET status='notified' WHERE id=%s", (entry["id"],))
        cur.execute("SELECT name FROM events WHERE id=%s", (event_id,))
        event_name = cur.fetchone()["name"]
        email_async(
            entry["email"],
            f"Kursi tersedia: {event_name}",
            f"Halo {entry['name']},\n\nKursi pada kegiatan \"{event_name}\" kini tersedia. Silakan "
            "mendaftar kembali melalui aplikasi agar pertanyaan wajib dapat Anda isi.\n\nKarcis",
        )
        log.info("Daftar tunggu diberi tahu (isi sendiri): waitlist id=%d event id=%d", entry["id"], event_id)
        return {"notified": entry["email"]}
    code = new_code()
    cur.execute(
        "INSERT INTO registrations (event_id, user_id, name, email, phone, code) "
        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (event_id, entry["user_id"], entry["name"], entry["email"], entry["phone"], code),
    )
    reg_id = cur.fetchone()["id"]
    cur.execute("UPDATE waitlist SET status='converted' WHERE id=%s", (entry["id"],))
    cur.execute("SELECT name FROM events WHERE id=%s", (event_id,))
    event_name = cur.fetchone()["name"]
    email_async(
        entry["email"],
        f"Kursi tersedia: {event_name}",
        f"Halo {entry['name']},\n\nKursi pada kegiatan \"{event_name}\" kini tersedia dan pendaftaran "
        f"Anda otomatis dibuat. Kode tiket Anda: {code}\n\nSampai jumpa!\nKarcis",
    )
    log.info("Daftar tunggu dikonversi: reg id=%d event id=%d", reg_id, event_id)
    return {"registration_id": reg_id, "email": entry["email"]}


@app.get("/api/registrations")
def list_registrations(user: dict = Depends(current_user)):
    with get_conn() as conn, cursor(conn) as cur:
        if user["role"] == "admin":
            cur.execute(REG_SELECT + " ORDER BY r.id DESC")
        elif user["role"] == "organizer":
            cur.execute(REG_SELECT + " WHERE e.owner_id=%s ORDER BY r.id DESC", (user["id"],))
        else:
            cur.execute(REG_SELECT + " WHERE r.user_id=%s ORDER BY r.id DESC", (user["id"],))
        return [dict(r) for r in cur.fetchall()]


@app.post("/api/registrations", status_code=201)
def create_registration(body: RegistrationIn, user: dict = Depends(require_roles("user"))):
    email = body.email.lower()
    with get_conn() as conn, cursor(conn) as cur:
        # Kunci baris kegiatan agar pengecekan kuota aman dari pendaftaran bersamaan
        cur.execute("SELECT id, quota, status, event_date FROM events WHERE id=%s FOR UPDATE", (body.event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        if event["status"] != "published":
            raise HTTPException(status_code=409, detail="Kegiatan belum dibuka atau sudah dibatalkan")
        if count_registered(cur, body.event_id) >= event["quota"]:
            log.warning("Pendaftaran ditolak, kuota penuh: event id=%d", body.event_id)
            raise HTTPException(status_code=409, detail="Kuota kegiatan sudah penuh")
        values = validate_answers(cur, body.event_id, body.answers)
        try:
            cur.execute(
                "INSERT INTO registrations (event_id, user_id, name, email, phone, code) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (body.event_id, user["id"], body.name.strip(), email, body.phone, new_code()),
            )
        except errors.UniqueViolation:
            log.warning("Pendaftaran ganda ditolak: event id=%d", body.event_id)
            raise HTTPException(status_code=409, detail="Email ini sudah terdaftar pada kegiatan tersebut")
        row = cur.fetchone()
        reg_id, code = row["id"], None
        cur.execute("SELECT code FROM registrations WHERE id=%s", (reg_id,))
        code = cur.fetchone()["code"]
        for field_id, value in values:
            cur.execute(
                "INSERT INTO registration_answers (registration_id, field_id, value) VALUES (%s,%s,%s)",
                (reg_id, field_id, value),
            )
        cur.execute("SELECT name, event_date, location FROM events WHERE id=%s", (body.event_id,))
        ev = cur.fetchone()
    log.info("Pendaftaran dibuat id=%d event id=%d user id=%d", reg_id, body.event_id, user["id"])
    email_async(
        email,
        f"Pendaftaran berhasil: {ev['name']}",
        f"Halo {body.name.strip()},\n\nPendaftaran Anda untuk \"{ev['name']}\" pada "
        f"{ev['event_date']:%d-%m-%Y %H:%M} di {ev['location']} berhasil.\nKode tiket Anda: {code}\n\n"
        f"Tunjukkan kode ini saat check-in.\n\nSampai jumpa!\nKarcis",
    )
    return {"id": reg_id, "event_id": body.event_id, "name": body.name.strip(), "email": email,
            "phone": body.phone, "code": code}


@app.put("/api/registrations/{reg_id}")
def update_registration(reg_id: int, body: RegistrationIn, user: dict = Depends(require_roles("user"))):
    email = body.email.lower()
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, user_id FROM registrations WHERE id=%s FOR UPDATE", (reg_id,))
        reg = cur.fetchone()
        if not reg:
            raise HTTPException(status_code=404, detail="Pendaftaran tidak ditemukan")
        if reg["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Anda hanya dapat mengubah pendaftaran milik sendiri")
        cur.execute("SELECT id, quota, status, event_date FROM events WHERE id=%s FOR UPDATE", (body.event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        if event["status"] != "published":
            raise HTTPException(status_code=409, detail="Kegiatan belum dibuka atau sudah dibatalkan")
        if count_registered(cur, body.event_id, exclude_reg_id=reg_id) >= event["quota"]:
            raise HTTPException(status_code=409, detail="Kuota kegiatan sudah penuh")
        values = validate_answers(cur, body.event_id, body.answers)
        try:
            cur.execute(
                "UPDATE registrations SET event_id=%s, name=%s, email=%s, phone=%s WHERE id=%s",
                (body.event_id, body.name.strip(), email, body.phone, reg_id),
            )
        except errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="Email ini sudah terdaftar pada kegiatan tersebut")
        cur.execute("DELETE FROM registration_answers WHERE registration_id=%s", (reg_id,))
        for field_id, value in values:
            cur.execute(
                "INSERT INTO registration_answers (registration_id, field_id, value) VALUES (%s,%s,%s)",
                (reg_id, field_id, value),
            )
    log.info("Pendaftaran diubah id=%d", reg_id)
    return {"id": reg_id, "event_id": body.event_id, "name": body.name.strip(), "email": email, "phone": body.phone}


@app.delete("/api/registrations/{reg_id}", status_code=204)
def delete_registration(reg_id: int, user: dict = Depends(current_user)):
    promoted = None
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute(
            "SELECT r.id, r.user_id, e.owner_id FROM registrations r "
            "JOIN events e ON e.id = r.event_id WHERE r.id=%s FOR UPDATE OF r",
            (reg_id,),
        )
        reg = cur.fetchone()
        if not reg:
            raise HTTPException(status_code=404, detail="Pendaftaran tidak ditemukan")
        allowed = (
            user["role"] == "admin"
            or (user["role"] == "organizer" and reg["owner_id"] == user["id"])
            or (user["role"] == "user" and reg["user_id"] == user["id"])
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Anda tidak berhak membatalkan pendaftaran ini")
        cur.execute("SELECT event_id FROM registrations WHERE id=%s", (reg_id,))
        event_id = cur.fetchone()["event_id"]
        cur.execute("DELETE FROM registrations WHERE id=%s", (reg_id,))
        promoted = promote_waitlist(cur, event_id)
    log.info("Pendaftaran dibatalkan id=%d oleh %s id=%d", reg_id, user["role"], user["id"])
    return Response(status_code=204, headers={"X-Waitlist-Promoted": "1"} if promoted else None)


@app.post("/api/registrations/checkin")
def checkin(body: CheckinIn, user: dict = Depends(require_roles("organizer", "admin"))):
    code = body.code.strip().upper()
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute(
            "SELECT r.id, r.name, r.email, r.checked_in_at, e.id AS event_id, e.name AS event_name, e.owner_id "
            "FROM registrations r JOIN events e ON e.id = r.event_id WHERE r.code=%s FOR UPDATE OF r",
            (code,),
        )
        reg = cur.fetchone()
        if not reg:
            raise HTTPException(status_code=404, detail="Kode tiket tidak ditemukan")
        if user["role"] == "organizer" and reg["owner_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Kode ini milik kegiatan Anda yang lain")
        if reg["checked_in_at"]:
            raise HTTPException(
                status_code=409,
                detail=f"Sudah check-in pada {reg['checked_in_at']:%d-%m-%Y %H:%M}",
            )
        cur.execute("UPDATE registrations SET checked_in_at=NOW() WHERE id=%s RETURNING checked_in_at", (reg["id"],))
        checked = cur.fetchone()["checked_in_at"]
    log.info("Check-in berhasil: reg id=%d event id=%d", reg["id"], reg["event_id"])
    return {
        "id": reg["id"], "name": reg["name"], "email": reg["email"],
        "event_id": reg["event_id"], "event_name": reg["event_name"],
        "checked_in_at": checked,
    }


# ---------- Daftar tunggu ----------
@app.post("/api/waitlist", status_code=201)
def join_waitlist(body: WaitlistIn, user: dict = Depends(require_roles("user"))):
    email = body.email.lower()
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, quota, status FROM events WHERE id=%s FOR UPDATE", (body.event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        if event["status"] != "published":
            raise HTTPException(status_code=409, detail="Kegiatan belum dibuka atau sudah dibatalkan")
        if count_registered(cur, body.event_id) < event["quota"]:
            raise HTTPException(status_code=400, detail="Masih ada kursi tersedia, silakan daftar langsung")
        try:
            cur.execute(
                "INSERT INTO waitlist (event_id, user_id, name, email, phone) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (body.event_id, user["id"], body.name.strip(), email, body.phone),
            )
        except errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="Email ini sudah ada di daftar tunggu")
        wait_id = cur.fetchone()["id"]
    log.info("Daftar tunggu: id=%d event id=%d user id=%d", wait_id, body.event_id, user["id"])
    return {"id": wait_id, "event_id": body.event_id, "status": "waiting"}


@app.get("/api/waitlist")
def list_waitlist(user: dict = Depends(current_user)):
    with get_conn() as conn, cursor(conn) as cur:
        if user["role"] == "admin":
            cur.execute(
                "SELECT w.id, w.event_id, e.name AS event_name, w.name, w.email, w.phone, w.status, w.created_at "
                "FROM waitlist w JOIN events e ON e.id = w.event_id ORDER BY w.id DESC"
            )
        elif user["role"] == "organizer":
            cur.execute(
                "SELECT w.id, w.event_id, e.name AS event_name, w.name, w.email, w.phone, w.status, w.created_at "
                "FROM waitlist w JOIN events e ON e.id = w.event_id WHERE e.owner_id=%s ORDER BY w.id DESC",
                (user["id"],),
            )
        else:
            cur.execute(
                "SELECT w.id, w.event_id, e.name AS event_name, w.name, w.email, w.phone, w.status, w.created_at "
                "FROM waitlist w JOIN events e ON e.id = w.event_id WHERE w.user_id=%s ORDER BY w.id DESC",
                (user["id"],),
            )
        return [dict(r) for r in cur.fetchall()]


@app.delete("/api/waitlist/{wait_id}", status_code=204)
def delete_waitlist(wait_id: int, user: dict = Depends(current_user)):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute(
            "SELECT w.id, w.user_id, e.owner_id FROM waitlist w JOIN events e ON e.id = w.event_id "
            "WHERE w.id=%s FOR UPDATE OF w",
            (wait_id,),
        )
        entry = cur.fetchone()
        if not entry:
            raise HTTPException(status_code=404, detail="Entri daftar tunggu tidak ditemukan")
        allowed = (
            user["role"] == "admin"
            or (user["role"] == "organizer" and entry["owner_id"] == user["id"])
            or (user["role"] == "user" and entry["user_id"] == user["id"])
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Anda tidak berhak menghapus entri ini")
        cur.execute("DELETE FROM waitlist WHERE id=%s", (wait_id,))
    return Response(status_code=204)
