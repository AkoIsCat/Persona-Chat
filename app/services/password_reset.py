"""이메일 인증번호로 비밀번호를 재설정한다."""

from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from app.core.security import hash_password, verify_password
from app.database import get_connection
from app.services.mailer import MailDeliveryError, send_password_reset_code

CODE_TTL = timedelta(minutes=5)
RESEND_WAIT = timedelta(seconds=60)
RESET_TOKEN_TTL = timedelta(minutes=10)
MAX_CODE_ATTEMPTS = 5
REQUEST_MESSAGE = (
    "입력하신 이메일로 인증번호 발송을 요청했습니다. "
    "가입된 이메일이라면 인증번호를 확인해 주세요."
)


class PasswordResetError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class _WindowLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [
                hit
                for hit in self._hits.get(key, [])
                if now - hit < self.window_seconds
            ]
            if len(recent) >= self.limit:
                self._hits[key] = recent
                return False
            recent.append(now)
            self._hits[key] = recent
            return True


class _Cooldown:
    def __init__(self, seconds: int):
        self.seconds = seconds
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def ready(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if now < self._until.get(key, 0):
                return False
            self._until[key] = now + self.seconds
            return True


_request_window = _WindowLimiter(5, 15 * 60)
_verify_window = _WindowLimiter(10, 15 * 60)
_unknown_resend = _Cooldown(60)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _too_many() -> PasswordResetError:
    return PasswordResetError(429, "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.")


def _resend_wait() -> PasswordResetError:
    return PasswordResetError(429, "인증번호는 60초 후에 다시 요청할 수 있습니다.")


def request_password_reset(email: str) -> str:
    normalized = _normalize_email(email)
    if not _request_window.allow(normalized):
        raise _too_many()

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM users
                WHERE lower(email) = %s
                """,
                (normalized,),
            )
            user = cur.fetchone()

    if user is None:
        if not _unknown_resend.ready(normalized):
            raise _resend_wait()
        return REQUEST_MESSAGE

    code = f"{secrets.randbelow(1_000_000):06d}"
    issued_at = _now()
    _store_new_code(user[0], normalized, code, issued_at)
    try:
        send_password_reset_code(normalized, code)
    except MailDeliveryError:
        _clear_undelivered_code(normalized)
        raise PasswordResetError(
            502,
            "인증번호 메일을 보내지 못했습니다. 잠시 후 다시 시도해 주세요.",
        )
    return REQUEST_MESSAGE


def _store_new_code(user_id: int, email: str, code: str, issued_at: datetime) -> None:
    code_hash = hash_password(code)
    expires_at = issued_at + CODE_TTL
    resend_at = issued_at + RESEND_WAIT
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT resend_available_at
                FROM password_reset_challenges
                WHERE email = %s
                FOR UPDATE
                """,
                (email,),
            )
            existing = cur.fetchone()
            if existing is not None and _as_utc(existing[0]) > issued_at:
                raise _resend_wait()

            cur.execute(
                """
                INSERT INTO password_reset_challenges (
                    email,
                    user_id,
                    code_hash,
                    code_expires_at,
                    attempt_count,
                    resend_available_at,
                    verified,
                    reset_token_hash,
                    reset_token_expires_at,
                    consumed_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, 0, %s, FALSE, NULL, NULL, NULL, NOW())
                ON CONFLICT (email) DO UPDATE
                SET user_id = EXCLUDED.user_id,
                    code_hash = EXCLUDED.code_hash,
                    code_expires_at = EXCLUDED.code_expires_at,
                    attempt_count = 0,
                    resend_available_at = EXCLUDED.resend_available_at,
                    verified = FALSE,
                    reset_token_hash = NULL,
                    reset_token_expires_at = NULL,
                    consumed_at = NULL,
                    updated_at = NOW()
                """,
                (email, user_id, code_hash, expires_at, resend_at),
            )


def _clear_undelivered_code(email: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE password_reset_challenges
                SET code_hash = NULL,
                    code_expires_at = NOW(),
                    resend_available_at = NOW(),
                    verified = FALSE,
                    reset_token_hash = NULL,
                    reset_token_expires_at = NULL,
                    updated_at = NOW()
                WHERE email = %s
                """,
                (email,),
            )


def verify_password_reset(email: str, code: str) -> str:
    normalized = _normalize_email(email)
    if not _verify_window.allow(normalized):
        raise _too_many()
    if len(code) != 6 or not code.isdigit():
        raise PasswordResetError(400, "인증번호는 6자리 숫자여야 합니다.")

    now = _now()
    reset_token = secrets.token_urlsafe(32)
    token_hash = hash_password(reset_token)
    failure: PasswordResetError | None = None
    accepted = False
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT code_hash, code_expires_at, attempt_count, consumed_at
                FROM password_reset_challenges
                WHERE email = %s
                FOR UPDATE
                """,
                (normalized,),
            )
            row = cur.fetchone()
            if row is None or row[3] is not None:
                failure = PasswordResetError(
                    400,
                    "인증번호가 올바르지 않거나 만료되었습니다.",
                )
            else:
                code_hash, expires_at, attempts, _consumed = row
                if attempts >= MAX_CODE_ATTEMPTS or code_hash is None:
                    failure = PasswordResetError(
                        400,
                        "인증번호 입력 횟수를 초과했습니다. 인증번호를 다시 요청해 주세요.",
                    )
                elif _as_utc(expires_at) is None or _as_utc(expires_at) <= now:
                    cur.execute(
                        """
                        UPDATE password_reset_challenges
                        SET code_hash = NULL, updated_at = NOW()
                        WHERE email = %s
                        """,
                        (normalized,),
                    )
                    failure = PasswordResetError(
                        400,
                        "인증번호가 만료되었습니다. 다시 요청해 주세요.",
                    )
                else:
                    next_attempts = attempts + 1
                    if not verify_password(code, code_hash):
                        cur.execute(
                            """
                            UPDATE password_reset_challenges
                            SET attempt_count = %s,
                                code_hash = CASE WHEN %s >= %s THEN NULL ELSE code_hash END,
                                updated_at = NOW()
                            WHERE email = %s
                            """,
                            (next_attempts, next_attempts, MAX_CODE_ATTEMPTS, normalized),
                        )
                        if next_attempts >= MAX_CODE_ATTEMPTS:
                            failure = PasswordResetError(
                                400,
                                "인증번호 입력 횟수를 초과했습니다. 인증번호를 다시 요청해 주세요.",
                            )
                        else:
                            failure = PasswordResetError(400, "인증번호가 올바르지 않습니다.")
                    else:
                        cur.execute(
                            """
                            UPDATE password_reset_challenges
                            SET code_hash = NULL,
                                attempt_count = %s,
                                verified = TRUE,
                                reset_token_hash = %s,
                                reset_token_expires_at = %s,
                                consumed_at = NULL,
                                updated_at = NOW()
                            WHERE email = %s
                            """,
                            (next_attempts, token_hash, now + RESET_TOKEN_TTL, normalized),
                        )
                        accepted = True
    if failure is not None:
        raise failure
    if not accepted:
        raise PasswordResetError(400, "인증번호가 올바르지 않거나 만료되었습니다.")
    return reset_token


def confirm_password_reset(email: str, reset_token: str, new_password: str) -> str:
    normalized = _normalize_email(email)
    now = _now()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, verified, reset_token_hash, reset_token_expires_at, consumed_at
                FROM password_reset_challenges
                WHERE email = %s
                FOR UPDATE
                """,
                (normalized,),
            )
            row = cur.fetchone()
            if row is None:
                raise PasswordResetError(
                    400,
                    "비밀번호 재설정 인증이 유효하지 않습니다. 다시 인증해 주세요.",
                )

            user_id, verified, token_hash, token_expires_at, consumed_at = row
            if consumed_at is not None:
                raise PasswordResetError(
                    400,
                    "이미 사용된 재설정 인증입니다. 다시 인증해 주세요.",
                )
            if (
                not verified
                or token_hash is None
                or not verify_password(reset_token, token_hash)
            ):
                raise PasswordResetError(
                    400,
                    "비밀번호 재설정 인증이 유효하지 않습니다. 다시 인증해 주세요.",
                )
            if _as_utc(token_expires_at) is None or _as_utc(token_expires_at) <= now:
                raise PasswordResetError(
                    400,
                    "재설정 인증 시간이 만료되었습니다. 다시 인증해 주세요.",
                )

            cur.execute(
                """
                UPDATE users
                SET password_hash = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (hash_password(new_password), user_id),
            )
            cur.execute(
                """
                UPDATE password_reset_challenges
                SET code_hash = NULL,
                    verified = FALSE,
                    reset_token_hash = NULL,
                    reset_token_expires_at = NULL,
                    consumed_at = NOW(),
                    updated_at = NOW()
                WHERE email = %s
                """,
                (normalized,),
            )
            cur.execute(
                """
                INSERT INTO user_token_revocations (user_id, valid_after, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (user_id) DO UPDATE
                SET valid_after = EXCLUDED.valid_after,
                    updated_at = NOW()
                """,
                (user_id, now),
            )
    return "비밀번호가 변경되었습니다. 새 비밀번호로 로그인해 주세요."
