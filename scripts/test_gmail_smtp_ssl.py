"""Gmail SMTP_SSL 465 포트 로그인만 확인한다. 앱 비밀번호는 출력하지 않는다.

실행: backend 폴더에서
    .\\.venv\\Scripts\\python.exe scripts\\test_gmail_smtp_ssl.py
"""

from __future__ import annotations

import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import app.core.config  # noqa: F401  config.py와 동일하게 .env를 읽는다.

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def _mask(secret: str, text: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "***")
    return text


class RecordingSMTPSSL(smtplib.SMTP_SSL):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.error_replies: list[tuple[int, str]] = []

    def getreply(self):
        code, message = super().getreply()
        if code >= 400:
            text = message.decode("utf-8", "replace") if isinstance(message, bytes) else str(message)
            self.error_replies.append((code, text.splitlines()[0][:300] if text else ""))
        return code, message


def _credentials() -> tuple[str, str]:
    sender = (os.getenv("SMTP_EMAIL") or "").strip()
    password = (os.getenv("SMTP_APP_PASSWORD") or "").strip()
    return sender, password


def _print_auth_failure(password: str, smtp: RecordingSMTPSSL, exc: Exception) -> None:
    print(f"인증 결과: 실패 ({type(exc).__name__})")
    if isinstance(exc, smtplib.SMTPResponseException):
        print(f"SMTP 응답 코드: {exc.smtp_code}")
    for code, _message in smtp.error_replies:
        print(f"SMTP 응답 코드: {code}")
    if not isinstance(exc, smtplib.SMTPResponseException) and not smtp.error_replies:
        print("SMTP 응답 코드: 없음")
        print(f"예외 메시지: {_mask(password, str(exc))[:300]}")


def authenticate() -> bool:
    sender, password = _credentials()
    print(f"SMTP 서버: {SMTP_HOST}:{SMTP_PORT}")
    print(f"연결 방식: SMTP_SSL")
    print(f"SMTP 계정: {sender or '(없음)'}")
    print(f"앱 비밀번호: {'있음' if password else '없음'}, 길이 {len(password)}")
    if not sender or not password:
        print("인증 결과: 실패")
        print("SMTP 응답 코드: 없음")
        return False

    context = ssl.create_default_context()
    smtp = RecordingSMTPSSL(SMTP_HOST, SMTP_PORT, timeout=20, context=context)
    try:
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

    print("인증 결과: 성공")
    return True


def _send_test_mail() -> None:
    sender, password = _credentials()
    recipient = input("테스트 메일을 받을 주소: ").strip()
    if not recipient:
        print("받을 주소가 없어 발송하지 않습니다.")
        return
    message = EmailMessage()
    message["Subject"] = "[Persona Chat] SMTP SSL 테스트"
    message["From"] = sender
    message["To"] = recipient
    message.set_content("Persona Chat Gmail SMTP_SSL 465 인증 테스트 메일입니다.")
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20, context=context) as smtp:
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
            print(f"SMTP 응답 코드: {exc.smtp_code}")
        else:
            print(f"예외 메시지: {_mask(password, str(exc))[:300]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
