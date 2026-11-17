"""SMTP 설정 진단. 앱 비밀번호 원문은 출력하지 않는다."""

from __future__ import annotations

import os
import smtplib
from datetime import datetime
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = BACKEND_ROOT / "app" / "core" / "config.py"


def _mask(secret: str, text: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "***")
    return text


def _char_flags(value: str) -> str:
    return (
        f"len={len(value)} "
        f"leading_ws={len(value) - len(value.lstrip())} "
        f"trailing_ws={len(value) - len(value.rstrip())} "
        f"spaces={value.count(' ')} "
        f"cr={value.count(chr(13))} "
        f"lf={value.count(chr(10))} "
        f"quotes={bool(value[:1]) and value[:1] in chr(34) + chr(39)} "
        f"ascii_letters_only={value.isascii() and value.isalpha()}"
    )


def _file_assignments(path: Path) -> list[tuple[int, str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("SMTP_"):
            found.append((number, key, value))
    return found


def _process_started_before_env(env_path: Path) -> None:
    try:
        import subprocess

        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.CommandLine -like '*uvicorn*' -and $_.CommandLine -like '*app.main:app*' } | "
                "Select-Object ProcessId,ParentProcessId,CreationDate | "
                "Format-List",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        print(f"running_process=unavailable {type(exc).__name__}")
        return
    print("running_uvicorn")
    print((result.stdout or "").strip() or "(none)")
    env_mtime = datetime.fromtimestamp(env_path.stat().st_mtime)
    print(f"env_mtime={env_mtime.isoformat(timespec='seconds')}")
    print("note=uvicorn은 .env 변경만으로는 재시작되지 않는다. 프로세스 시작 시각이 env_mtime보다 이르면 이전 값을 사용 중이다.")


def _classify_login(host: str, port: int, sender: str, password: str) -> None:
    print(f"login_args host={host} port={port} sender={sender} password={_char_flags(password)}")

    class RecordingSMTP(smtplib.SMTP):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.auth_replies: list[tuple[int, str]] = []

        def getreply(self):
            code, msg = super().getreply()
            text = msg.decode("utf-8", "replace") if isinstance(msg, bytes) else str(msg)
            if code >= 400:
                self.auth_replies.append((code, _mask(password, text)[:300]))
            return code, msg

    try:
        with RecordingSMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            try:
                smtp.login(sender, password)
                print("login_result=ok")
            except smtplib.SMTPAuthenticationError as exc:
                message = exc.smtp_error.decode("utf-8", "replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
                print(f"login_result=SMTPAuthenticationError smtp_code={exc.smtp_code} message={_mask(password, message)[:300]}")
            except smtplib.SMTPServerDisconnected as exc:
                print(f"login_result=SMTPServerDisconnected message={_mask(password, str(exc))[:300]}")
            except smtplib.SMTPException as exc:
                print(f"login_result={type(exc).__name__} message={_mask(password, str(exc))[:300]}")
            print("server_errors")
            for code, message in smtp.auth_replies:
                print(f"  smtp_code={code} message={message.splitlines()[0]}")
    except Exception as exc:
        print(f"login_result={type(exc).__name__} message={_mask(password, str(exc))[:300]}")


def _walk_from(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for directory in [current, *current.parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def main() -> int:
    before = {key: os.environ.get(key) for key in ("SMTP_HOST", "SMTP_PORT", "SMTP_EMAIL", "SMTP_APP_PASSWORD")}
    discovered = find_dotenv(usecwd=True)
    from_script = _walk_from(Path(__file__))
    from_config = _walk_from(CONFIG_FILE)
    print(f"cwd={Path.cwd()}")
    print(f"backend_root={BACKEND_ROOT}")
    print(f"config_file={CONFIG_FILE}")
    print(f"find_dotenv_usecwd={Path(discovered).resolve() if discovered else '(not found)'}")
    print(f"env_walk_from_script={from_script}")
    print(f"env_walk_from_config={from_config}")

    env_path = from_config or (BACKEND_ROOT / ".env")
    print(f"env_exists={env_path.exists()} bytes={env_path.stat().st_size if env_path.exists() else 0}")
    if not env_path.exists():
        return 1

    print("file_smtp_entries")
    for number, key, raw_value in _file_assignments(env_path):
        if key == "SMTP_APP_PASSWORD":
            print(f"  line={number} {key} {_char_flags(raw_value)}")
        else:
            print(f"  line={number} {key}={raw_value!r} {_char_flags(raw_value)}")

    print("process_env_before_dotenv")
    for key, value in before.items():
        if key == "SMTP_APP_PASSWORD":
            print(f"  {key} present={value is not None} {_char_flags(value or '')}")
        else:
            print(f"  {key}={value!r}")

    load_dotenv(env_path, override=False)
    host = os.getenv("SMTP_HOST")
    port = os.getenv("SMTP_PORT")
    sender = os.getenv("SMTP_EMAIL")
    password = os.getenv("SMTP_APP_PASSWORD")
    print("loaded_without_override")
    print(f"  SMTP_HOST={host!r} code_default=smtp.gmail.com")
    print(f"  SMTP_PORT={port!r} code_default=587")
    print(f"  SMTP_EMAIL={sender!r}")
    print(f"  SMTP_APP_PASSWORD present={bool(password)} {_char_flags(password or '')}")
    overridden = []
    for key, previous in before.items():
        if previous not in (None, "") and previous != os.getenv(key):
            overridden.append(key)
    print(f"preexisting_process_env_kept={overridden or 'none'}")

    used_host = (host or "smtp.gmail.com").strip() or "smtp.gmail.com"
    used_port = int((port or "587").strip())
    used_sender = (sender or "").strip()
    used_password = (password or "").strip()
    print("values_passed_to_smtp_login")
    print(f"  host={used_host} port={used_port}")
    print(f"  sender={used_sender!r} strip_changed={used_sender != (sender or '')}")
    print(f"  password {_char_flags(used_password)} strip_changed={used_password != (password or '')}")
    _process_started_before_env(env_path)
    if used_sender and used_password:
        _classify_login(used_host, used_port, used_sender, used_password)
    else:
        print("login_result=skipped missing sender or password")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
