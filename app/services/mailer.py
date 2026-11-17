"""Gmail SMTP로 비밀번호 재설정 인증번호를 보낸다."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


class MailDeliveryError(RuntimeError):
    """메일 발송에 실패했을 때 발생한다."""


def send_password_reset_code(recipient: str, code: str) -> None:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip() or "smtp.gmail.com"
    port = int(os.getenv("SMTP_PORT", "587"))
    sender = os.getenv("SMTP_EMAIL", "").strip()
    app_password = os.getenv("SMTP_APP_PASSWORD", "").strip()
    if not sender or not app_password:
        raise MailDeliveryError("smtp_not_configured")

    message = EmailMessage()
    message["Subject"] = "[Persona Chat] 비밀번호 재설정 인증번호"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "\n".join(
            [
                "Persona Chat 비밀번호 재설정 인증번호입니다.",
                "",
                f"인증번호: {code}",
                "유효시간: 5분",
                "",
                "본인이 요청하지 않았다면 이 메일을 무시해 주세요.",
            ]
        )
    )

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(sender, app_password)
            smtp.send_message(message)
    except Exception as exc:
        raise MailDeliveryError("smtp_failed") from exc
