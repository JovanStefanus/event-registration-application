import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import bcrypt
import jwt
import psycopg2
import psycopg2.extras
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
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

PHONE_RE = r"^\+?\d{9,15}$"

app = FastAPI(title="Event Registration API", version="2.0.0")


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
# (kolom status/owner_id ditambahkan, constraint peran diperbarui) tanpa menghapus data.
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
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
ALTER TABLE events ADD COLUMN IF NOT EXISTS owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE;

CREATE TABLE IF NOT EXISTS registrations (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(20) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (event_id, email)
);
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


@app.on_event("startup")
def on_startup():
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET belum diatur di environment")
    init_db()


# ---------- Error handler & middleware ----------
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
    return response


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


class EventIn(BaseModel):
    name: str = Field(..., min_length=3, max_length=100)
    description: str = Field("", max_length=500)
    event_date: datetime
    location: str = Field(..., min_length=2, max_length=150)
    quota: int = Field(..., ge=1, le=10000)


class RegistrationIn(BaseModel):
    event_id: int
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    phone: str = Field(..., pattern=PHONE_RE)


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


# ---------- Manajemen pengguna (admin) ----------
@app.get("/api/users")
def list_users(user: dict = Depends(require_roles("admin"))):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute(
            "SELECT id, name, email, phone, role, status, created_at FROM users "
            "ORDER BY (status = 'pending') DESC, id"
        )
        return [dict(r) for r in cur.fetchall()]


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
    "SELECT e.*, u.name AS owner_name, COUNT(r.id)::int AS registered FROM events e "
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
    }


def fetch_event(cur, event_id: int) -> Optional[dict]:
    cur.execute(EVENT_SELECT + "WHERE e.id=%s GROUP BY e.id, u.name", (event_id,))
    row = cur.fetchone()
    return event_dict(row) if row else None


def count_registered(cur, event_id: int, exclude_reg_id: Optional[int] = None) -> int:
    if exclude_reg_id:
        cur.execute(
            "SELECT COUNT(*) AS c FROM registrations WHERE event_id=%s AND id<>%s",
            (event_id, exclude_reg_id),
        )
    else:
        cur.execute("SELECT COUNT(*) AS c FROM registrations WHERE event_id=%s", (event_id,))
    return cur.fetchone()["c"]


@app.get("/api/events")
def list_events(user: dict = Depends(current_user)):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute(EVENT_SELECT + "GROUP BY e.id, u.name ORDER BY e.event_date, e.id")
        return [event_dict(r) for r in cur.fetchall()]


@app.post("/api/events", status_code=201)
def create_event(body: EventIn, user: dict = Depends(require_roles("organizer"))):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute(
            "INSERT INTO events (name, description, event_date, location, quota, owner_id) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (body.name.strip(), body.description, body.event_date, body.location.strip(), body.quota, user["id"]),
        )
        event_id = cur.fetchone()["id"]
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
            "UPDATE events SET name=%s, description=%s, event_date=%s, location=%s, quota=%s WHERE id=%s",
            (body.name.strip(), body.description, body.event_date, body.location.strip(), body.quota, event_id),
        )
        result = fetch_event(cur, event_id)
    log.info("Kegiatan diubah id=%d", event_id)
    return result


@app.delete("/api/events/{event_id}", status_code=204)
def delete_event(event_id: int, user: dict = Depends(require_roles("admin", "organizer"))):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, owner_id FROM events WHERE id=%s FOR UPDATE", (event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        if user["role"] == "organizer" and event["owner_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Anda hanya dapat menghapus kegiatan milik sendiri")
        cur.execute("DELETE FROM events WHERE id=%s", (event_id,))
    log.info("Kegiatan dihapus id=%d oleh %s id=%d (pendaftaran terkait ikut terhapus)", event_id, user["role"], user["id"])
    return Response(status_code=204)


@app.get("/api/events/{event_id}/participants")
def list_participants(event_id: int, user: dict = Depends(current_user)):
    with get_conn() as conn, cursor(conn) as cur:
        cur.execute("SELECT id, owner_id FROM events WHERE id=%s", (event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        cur.execute(
            "SELECT id, name, email, phone FROM registrations WHERE event_id=%s ORDER BY id",
            (event_id,),
        )
        rows = cur.fetchall()
    full_access = user["role"] == "admin" or (user["role"] == "organizer" and event["owner_id"] == user["id"])
    if full_access:
        return [dict(r) for r in rows]
    # Peran lain hanya melihat nama (email & telepon peserta disembunyikan)
    return [{"id": r["id"], "name": r["name"]} for r in rows]


# ---------- Pendaftaran ----------
REG_SELECT = (
    "SELECT r.id, r.event_id, e.name AS event_name, e.owner_id AS event_owner_id, "
    "r.user_id, r.name, r.email, r.phone, r.created_at "
    "FROM registrations r JOIN events e ON e.id = r.event_id"
)


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
        cur.execute("SELECT id, quota FROM events WHERE id=%s FOR UPDATE", (body.event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        if count_registered(cur, body.event_id) >= event["quota"]:
            log.warning("Pendaftaran ditolak, kuota penuh: event id=%d", body.event_id)
            raise HTTPException(status_code=409, detail="Kuota kegiatan sudah penuh")
        try:
            cur.execute(
                "INSERT INTO registrations (event_id, user_id, name, email, phone) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (body.event_id, user["id"], body.name.strip(), email, body.phone),
            )
        except errors.UniqueViolation:
            log.warning("Pendaftaran ganda ditolak: event id=%d", body.event_id)
            raise HTTPException(status_code=409, detail="Email ini sudah terdaftar pada kegiatan tersebut")
        reg_id = cur.fetchone()["id"]
    log.info("Pendaftaran dibuat id=%d event id=%d user id=%d", reg_id, body.event_id, user["id"])
    return {"id": reg_id, "event_id": body.event_id, "name": body.name.strip(), "email": email, "phone": body.phone}


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
        cur.execute("SELECT id, quota FROM events WHERE id=%s FOR UPDATE", (body.event_id,))
        event = cur.fetchone()
        if not event:
            raise HTTPException(status_code=404, detail="Kegiatan tidak ditemukan")
        if count_registered(cur, body.event_id, exclude_reg_id=reg_id) >= event["quota"]:
            raise HTTPException(status_code=409, detail="Kuota kegiatan sudah penuh")
        try:
            cur.execute(
                "UPDATE registrations SET event_id=%s, name=%s, email=%s, phone=%s WHERE id=%s",
                (body.event_id, body.name.strip(), email, body.phone, reg_id),
            )
        except errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="Email ini sudah terdaftar pada kegiatan tersebut")
    log.info("Pendaftaran diubah id=%d", reg_id)
    return {"id": reg_id, "event_id": body.event_id, "name": body.name.strip(), "email": email, "phone": body.phone}


@app.delete("/api/registrations/{reg_id}", status_code=204)
def delete_registration(reg_id: int, user: dict = Depends(current_user)):
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
        cur.execute("DELETE FROM registrations WHERE id=%s", (reg_id,))
    log.info("Pendaftaran dibatalkan id=%d oleh %s id=%d", reg_id, user["role"], user["id"])
    return Response(status_code=204)
