from fastapi import APIRouter, Depends, HTTPException, Response, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
import hashlib, secrets, os
from database import get_db
from models import AppSettings

router = APIRouter()

SESSION_COOKIE = "mm_session"
SESSION_STORE: dict[str, str] = {}  # token -> username (in-memory, sufficient for single-user)


def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def get_or_create_settings(db: Session) -> AppSettings:
    s = db.query(AppSettings).first()
    if not s:
        s = AppSettings(
            username=os.getenv("ADMIN_USERNAME", "admin"),
            password_hash=hash_pw(os.getenv("ADMIN_PASSWORD", "Mali2024!")),
        )
        db.add(s)
        db.commit()
        db.refresh(s)
    return s


def require_auth(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if not token or token not in SESSION_STORE:
        raise HTTPException(status_code=401, detail="Oturum açmanız gerekiyor.")
    return SESSION_STORE[token]


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangeCredentials(BaseModel):
    current_password: str
    new_username: str
    new_password: str


@router.post("/login")
def login(data: LoginRequest, response: Response, db: Session = Depends(get_db)):
    s = get_or_create_settings(db)
    if data.username != s.username or hash_pw(data.password) != s.password_hash:
        raise HTTPException(status_code=401, detail="Kullanıcı adı veya şifre hatalı.")
    token = secrets.token_hex(32)
    SESSION_STORE[token] = data.username
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True, samesite="lax",
        max_age=60 * 60 * 24 * 30,  # 30 gün
    )
    return {"ok": True, "username": data.username}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        SESSION_STORE.pop(token, None)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
def me(username: str = Depends(require_auth)):
    return {"username": username, "authenticated": True}


@router.post("/change-credentials")
def change_credentials(
    data: ChangeCredentials,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    s = get_or_create_settings(db)
    if hash_pw(data.current_password) != s.password_hash:
        raise HTTPException(status_code=400, detail="Mevcut şifre hatalı.")
    if len(data.new_password) < 6:
        raise HTTPException(status_code=400, detail="Yeni şifre en az 6 karakter olmalıdır.")
    if not data.new_username.strip():
        raise HTTPException(status_code=400, detail="Kullanıcı adı boş olamaz.")
    s.username = data.new_username.strip()
    s.password_hash = hash_pw(data.new_password)
    db.commit()
    # Invalidate all sessions on credential change
    SESSION_STORE.clear()
    return {"ok": True, "message": "Bilgiler güncellendi. Tekrar giriş yapın."}
