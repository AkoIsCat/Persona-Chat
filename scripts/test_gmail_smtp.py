"""Gmail SMTP 로그인만 확인한다. 앱 비밀번호는 출력하지 않는다.

실행: backend 폴더에서
    .\\.venv\\Scripts\\python.exe scripts\\test_gmail_smtp.py
"""

from __future__ import annotations

import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import app.core.config  # noqa: F401  config.py와 동일하게 .env를 읽는다.


def _mask(secret: str, text: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "***")
    return text


class RecordingSMTP(smtplib.SMTP):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.error_replies: list[tuple[int, str]] = []

    def getreply(self):
        code, message = super().getreply()
        if code >= 400:
            text = message.decode("utf-8", "replace") if isinstance(message, bytes) else str(message)
            self.error_replies.append((code, text.splitlines()[0][:300] if text else ""))
        return code, message


def _settings() -> tuple[str, int, str, str]:
    host = (os.getenv("SMTP_HOST") or "smtp.gmail.com").strip() or "smtp.gmail.com"
    port = int((os.getenv("SMTP_PORT") or "587").strip())
    sender = (os.getenv("SMTP_EMAIL") or "").strip()
    password = (os.getenv("SMTP_APP_PASSWORD") or "").strip()
    return host, port, sender, password


def _print_auth_failure(password: str, smtp: RecordingSMTP, exc: Exception) -> None:
    print(f"인증 실패: {type(exc).__name__}")
    if isinstance(exc, smtplib.SMTPResponseException):
        detail = exc.smtp_error.decode("utf-8", "replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
        print(f"SMTP 응답 코드: {exc.smtp_code}")
        print(f"SMTP 응답: {_mask(password, detail).splitlines()[0][:300]}")
    else:
        print(f"예외 메시지: {_mask(password, str(exc))[:300]}")
    for code, message in smtp.error_replies:
        print(f"SMTP 응답 코드: {code}")
        print(f"SMTP 응답: {_mask(password, message)}")


def authenticate() -> bool:
    host, port, sender, password = _settings()
    print(f"SMTP 서버: {host}:{port}")
    print(f"SMTP 계정: {sender or '(없음)'}")
    print(f"앱 비밀번호: {'있음' if password else '없음'}, 길이 {len(password)}")
    if not sender or not password:
        print("인증 실패: SMTP_EMAIL 또는 SMTP_APP_PASSWORD가 없습니다.")
        return False

    smtp = RecordingSMTP(host, port, timeout=20)
    try:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(sender, password)
    except Exception as exc:
        _print_auth_failure(password, smtp, exc)
        return False
    finally:
        try:
            smtp.close()
        except Exception:
            pass

    print("인증 성공")
    return True


def _send_test_mail() -> None:
    host, port, sender, password = _settings()
    recipient = input("테스트 메일을 받을 주소: ").strip()
    if not recipient:
        print("받을 주소가 없어 발송하지 않습니다.")
        return
    message = EmailMessage()
    message["Subject"] = "[Persona Chat] SMTP 테스트"
    message["From"] = sender
    message["To"] = recipient
    message.set_content("Persona Chat Gmail SMTP 인증 테스트 메일입니다.")
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(sender, password)
        smtp.send_message(message)
    print(f"테스트 메일을 발송했습니다: {recipient}")


def main() -> int:
    if not authenticate():
        return 1
    answer = input("테스트 메일을 발송할까요? (y/N): ").strip().lower()
    if answer != "y":
        print("메일 발송은 건너뜁니다.")
        return 0
    try:
        _send_test_mail()
    except Exception as exc:
        password = (os.getenv("SMTP_APP_PASSWORD") or "").strip()
        print(f"메일 발송 실패: {type(exc).__name__}")
        if isinstance(exc, smtplib.SMTPResponseException):
            detail = exc.smtp_error.decode("utf-8", "replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
            print(f"SMTP 응답 코드: {exc.smtp_code}")
            print(f"SMTP 응답: {_mask(password, detail).splitlines()[0][:300]}")
        else:
            print(f"예외 메시지: {_mask(password, str(exc))[:300]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
