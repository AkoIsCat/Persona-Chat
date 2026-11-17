"""비밀번호 재설정. SMTP는 호출하지 않고, 테스트 사용자는 끝나면 삭제한다."""

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core.dependencies import get_current_user_id
from app.core.security import create_access_token, hash_password, verify_password
from app.database import get_connection
from app.routers.auth import login
from app.schemas import PasswordResetConfirm, UserLogin
from app.services import password_reset as reset
from app.services.mailer import send_password_reset_code
from app.services.password_reset import PasswordResetError


def _apply_schema():
    statements = [
        """
        CREATE TABLE IF NOT EXISTS password_reset_challenges (
            email VARCHAR(255) PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            code_hash TEXT,
            code_expires_at TIMESTAMPTZ,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            resend_available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            verified BOOLEAN NOT NULL DEFAULT FALSE,
            reset_token_hash TEXT,
            reset_token_expires_at TIMESTAMPTZ,
            consumed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_token_revocations (
            user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            valid_after TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ]
    with get_connection() as conn:
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)


class PasswordResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _apply_schema()

    def setUp(self):
        self.email = f"pwreset-{uuid.uuid4().hex}@example.com"
        self.password = "old-password-1"
        self.sent = []
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (email, password_hash, nickname)
                    VALUES (%s, %s, %s)
                    RETURNING id
                    """,
                    (self.email, hash_password(self.password), "재설정테스트"),
                )
                self.user_id = cur.fetchone()[0]
        self.mailer = patch(
            "app.services.password_reset.send_password_reset_code",
            side_effect=self._capture_mail,
        )
        self.mailer.start()

    def tearDown(self):
        self.mailer.stop()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE id = %s", (self.user_id,))

    def _capture_mail(self, recipient, code):
        self.sent.append((recipient, code))

    def _challenge(self):
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT code_hash, attempt_count, verified, consumed_at
                    FROM password_reset_challenges
                    WHERE email = %s
                    """,
                    (self.email,),
                )
                return cur.fetchone()

    def test_unknown_email_does_not_send_or_store(self):
        message = reset.request_password_reset("missing-" + self.email)
        self.assertIn("가입된 이메일이라면", message)
        self.assertEqual(self.sent, [])

    def test_invalid_email_is_rejected_by_schema(self):
        from app.schemas import PasswordResetRequest

        with self.assertRaises(Exception):
            PasswordResetRequest(email="not-an-email")

    def test_registered_email_sends_six_digit_code_as_hash(self):
        reset.request_password_reset(self.email)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], self.email)
        self.assertRegex(self.sent[0][1], r"^\d{6}$")
        stored = self._challenge()
        self.assertNotEqual(stored[0], self.sent[0][1])
        self.assertTrue(verify_password(self.sent[0][1], stored[0]))

    def test_resend_is_blocked_for_60_seconds(self):
        reset.request_password_reset(self.email)
        with self.assertRaises(PasswordResetError) as caught:
            reset.request_password_reset(self.email)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertIn("60초", caught.exception.detail)
        self.assertEqual(len(self.sent), 1)

    def test_wrong_code_and_fifth_attempt_invalidates_it(self):
        reset.request_password_reset(self.email)
        for index in range(4):
            with self.assertRaises(PasswordResetError) as caught:
                reset.verify_password_reset(self.email, "000000")
            self.assertEqual(caught.exception.detail, "인증번호가 올바르지 않습니다.")
            self.assertEqual(self._challenge()[1], index + 1)
        with self.assertRaises(PasswordResetError) as caught:
            reset.verify_password_reset(self.email, "000000")
        self.assertIn("횟수를 초과", caught.exception.detail)
        self.assertIsNone(self._challenge()[0])
        with self.assertRaises(PasswordResetError) as caught:
            reset.verify_password_reset(self.email, self.sent[0][1])
        self.assertIn("횟수를 초과", caught.exception.detail)

    def test_expired_code_cannot_be_used(self):
        reset.request_password_reset(self.email)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE password_reset_challenges
                    SET code_expires_at = %s
                    WHERE email = %s
                    """,
                    (datetime.now(timezone.utc) - timedelta(minutes=1), self.email),
                )
        with self.assertRaises(PasswordResetError) as caught:
            reset.verify_password_reset(self.email, self.sent[0][1])
        self.assertIn("만료", caught.exception.detail)

    def test_verify_then_confirm_replaces_password_and_blocks_reuse(self):
        old_token = create_access_token(self.user_id, remember=True)
        reset.request_password_reset(self.email)
        token = reset.verify_password_reset(self.email, self.sent[0][1])
        with self.assertRaises(PasswordResetError):
            reset.verify_password_reset(self.email, self.sent[0][1])

        with self.assertRaises(PasswordResetError) as caught:
            reset.confirm_password_reset(self.email, "not-a-valid-reset-token-value", "new-password-1")
        self.assertIn("유효하지 않습니다", caught.exception.detail)

        message = reset.confirm_password_reset(self.email, token, "new-password-1")
        self.assertIn("변경", message)
        with self.assertRaises(PasswordResetError) as caught:
            reset.confirm_password_reset(self.email, token, "new-password-1")
        self.assertIn("이미 사용된", caught.exception.detail)

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT password_hash FROM users WHERE id = %s", (self.user_id,))
                stored = cur.fetchone()[0]
        self.assertNotEqual(stored, "new-password-1")
        self.assertTrue(verify_password("new-password-1", stored))
        self.assertFalse(verify_password(self.password, stored))

        login(UserLogin(email=self.email, password="new-password-1", remember=False))
        with self.assertRaises(HTTPException) as caught:
            login(UserLogin(email=self.email, password=self.password, remember=False))
        self.assertEqual(caught.exception.status_code, 401)

        with self.assertRaises(HTTPException) as caught:
            get_current_user_id(HTTPAuthorizationCredentials(scheme="Bearer", credentials=old_token))
        self.assertEqual(caught.exception.status_code, 401)

    def test_confirm_without_verification_is_blocked(self):
        with self.assertRaises(PasswordResetError) as caught:
            reset.confirm_password_reset(self.email, "x" * 32, "new-password-1")
        self.assertIn("유효하지 않습니다", caught.exception.detail)

    def test_password_mismatch_is_rejected_by_schema(self):
        with self.assertRaises(Exception):
            PasswordResetConfirm(
                email=self.email,
                reset_token="x" * 32,
                new_password="new-password-1",
                new_password_confirm="other-password",
            )

    def test_mailer_accepts_gmail_and_naver_recipients(self):
        captured = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout):
                captured["host"] = host
                captured["port"] = port

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def ehlo(self):
                return None

            def starttls(self):
                captured["tls"] = True

            def login(self, sender, password):
                captured["login"] = True

            def send_message(self, message):
                captured["to"] = message["To"]
                captured["body"] = message.get_content()

        with patch.dict(
            "os.environ",
            {
                "SMTP_HOST": "smtp.gmail.com",
                "SMTP_PORT": "587",
                "SMTP_EMAIL": "sender@gmail.com",
                "SMTP_APP_PASSWORD": "app-password",
            },
        ):
            with patch("app.services.mailer.smtplib.SMTP", FakeSMTP):
                send_password_reset_code("person@gmail.com", "123456")
                self.assertEqual(captured["to"], "person@gmail.com")
                send_password_reset_code("person@naver.com", "654321")
                self.assertEqual(captured["to"], "person@naver.com")
        self.assertEqual(captured["host"], "smtp.gmail.com")
        self.assertEqual(captured["port"], 587)
        self.assertTrue(captured["tls"])
        self.assertIn("Persona Chat", captured["body"])
        self.assertIn("654321", captured["body"])
        self.assertIn("5분", captured["body"])


if __name__ == "__main__":
    unittest.main()
