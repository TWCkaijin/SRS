import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import HTTPException, Request
from sqlalchemy import func, select

from srs.models import LoginAttempt, SessionRecord, iso, now

COOKIE = "srs_session"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Auth:
    def __init__(self, settings, factory):
        self.settings = settings
        self.factory = factory
        self.hasher = PasswordHasher()
        self.password_hash = self.hasher.hash(settings.web_admin_password.get_secret_value())
        # Revokes old sessions when the operator rotates the environment password.
        self.revision = digest(settings.web_admin_username + "\x00" + settings.web_admin_password.get_secret_value())

    def login(self, username: str, password: str, client: str) -> tuple[str, SessionRecord]:
        if not self.settings.configured:
            raise HTTPException(503, "請先在 .env.web 設定至少 12 字元的 WEB_ADMIN_PASSWORD")
        with self.factory() as db:
            cutoff = iso(datetime.now(UTC) - timedelta(minutes=10))
            failures = db.scalar(select(func.count()).select_from(LoginAttempt).where(
                LoginAttempt.client == client, LoginAttempt.created_at >= cutoff,
            ))
            if failures >= 10:
                raise HTTPException(429, "登入失敗次數過多，請稍後再試")
            valid = False
            try:
                valid = self.hasher.verify(self.password_hash, password)
            except VerificationError:
                pass
            if not valid or not hmac.compare_digest(username, self.settings.web_admin_username):
                db.add(LoginAttempt(client=client))
                db.commit()
                raise HTTPException(401, "帳號或密碼不正確")
            token = secrets.token_urlsafe(48)
            session = SessionRecord(
                token_hash=digest(token), username=username, csrf=secrets.token_urlsafe(32),
                auth_revision=self.revision,
                expires_at=iso(datetime.now(UTC) + timedelta(hours=self.settings.session_hours)),
            )
            from srs.service import audit
            audit(db, username, "session.login", username)
            db.add(session)
            db.commit()
            return token, session

    def authenticate(self, request: Request, permission: str = "read") -> SessionRecord:
        token = request.cookies.get(COOKIE, "")
        with self.factory() as db:
            session = db.get(SessionRecord, digest(token)) if token else None
            if session is None or session.expires_at <= now() or session.auth_revision != self.revision:
                raise HTTPException(401, "請先登入")
            if permission not in self.settings.permission_set:
                raise HTTPException(403, f"缺少操作權限：{permission}")
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                csrf = request.headers.get("X-CSRF-Token", "")
                if not hmac.compare_digest(csrf, session.csrf):
                    raise HTTPException(403, "CSRF 驗證失敗，請重新整理頁面")
            return session


def check_origin(request: Request, settings) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in {x.strip() for x in settings.allowed_origins.split(",")}:
        raise HTTPException(403, "不允許的來源")
