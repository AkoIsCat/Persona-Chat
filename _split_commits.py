import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed\n{result.stdout}\n{result.stderr}")
    return result


def show(path: str) -> str:
    return run(["show", f"HEAD:{path}"]).stdout


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def stage_text(path: str, content: str) -> None:
    temporary = ROOT / "_stage_blob.txt"
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    temporary.write_bytes(normalized.encode("utf-8"))
    try:
        oid = run(["hash-object", "-w", "--path", path, str(temporary)]).stdout.strip()
        run(["update-index", "--cacheinfo", f"100644,{oid},{path}"])
    finally:
        temporary.unlink(missing_ok=True)


def between(text: str, start: str, end: str) -> str:
    start_at = text.index(start)
    end_at = text.index(end, start_at)
    return text[start_at:end_at]


def commit(message: str, expected: set[str]) -> None:
    staged = [line for line in run(["diff", "--cached", "--name-only"]).stdout.splitlines() if line]
    if set(staged) != expected:
        raise SystemExit(f"{message}\nexpected {sorted(expected)}\nstaged {staged}")
    forbidden = [name for name in staged if name == ".env" or name.startswith("uploads/") or name.endswith(".dump")]
    if forbidden:
        raise SystemExit(f"refusing sensitive files: {forbidden}")
    diff = run(["diff", "--cached"]).stdout
    print(f"\n===== {message} =====")
    print(run(["diff", "--cached", "--stat"]).stdout)
    markers = {
        "feat: 로그인 상태 유지 기능 추가": ("remember: bool = False", "class MemoryUpdate"),
        "feat: 캐릭터 답변 길이 및 장기기억 프롬프트 개선": ("1~3문장으로 답하세요", "update_user_memory"),
        "refactor: 장기기억 문장 생성 방식 개선": ("주어를 생략하고", "def update_user_memory"),
        "feat: 캐릭터 삭제 API 추가": ("def delete_character(", "def upload_character_image"),
        "feat: 채팅방 삭제 API 추가": ("def delete_chat_room(", "def upload_character_image"),
        "feat: 캐릭터 이미지 등록 API 추가": ("def upload_character_image(", "def update_character_memory"),
        "feat: 장기기억 수정 및 삭제 API 추가": ("def update_character_memory(", "def upload_character_image"),
    }
    required, absent = markers[message]
    if required not in diff or absent in diff:
        raise SystemExit(f"marker check failed for {message}")
    run(["commit", "-m", message])


characters = read("app/routers/characters.py")
delete_route = between(characters, '@router.delete("/{character_id}")\n', '@router.post("/{character_id}/image")\n')
image_route = between(characters, '@router.post("/{character_id}/image")\n', '@router.post("", status_code=201)\n')
head_characters = show("app/routers/characters.py")
insert_at = head_characters.index('@router.post("", status_code=201)\n')
delete_only = head_characters[:insert_at] + delete_route + head_characters[insert_at:]

image_characters = delete_only.replace(
    "from fastapi import APIRouter, Depends, HTTPException\n",
    "from fastapi import APIRouter, Depends, File, HTTPException, UploadFile\n",
    1,
).replace(
    "from app.schemas import CharacterCreate\n",
    "from app.schemas import CharacterCreate\n"
    "from app.services.character_image import (\n"
    "    read_image,\n"
    "    remove_file,\n"
    "    remove_managed_image,\n"
    "    save_character_image,\n"
    ")\n",
    1,
)
image_insert = image_characters.index('@router.post("", status_code=201)\n')
image_characters = image_characters[:image_insert] + image_route + image_characters[image_insert:]

schemas = read("app/schemas.py")
login_schemas = schemas[: schemas.index("\nclass MemoryUpdate")].replace(
    "from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator\n",
    "from pydantic import BaseModel, EmailStr, Field, HttpUrl\n",
    1,
)
if not login_schemas.endswith("\n"):
    login_schemas += "\n"

memory = read("app/services/memory.py")
prompt_only = memory[: memory.index("\n\ndef _memory_row")]
if not prompt_only.endswith("\n"):
    prompt_only += "\n"

run(["add", "app/core/security.py", "app/routers/auth.py"])
stage_text("app/schemas.py", login_schemas)
commit("feat: 로그인 상태 유지 기능 추가", {"app/core/security.py", "app/routers/auth.py", "app/schemas.py"})

run(["add", "app/services/chat_service.py"])
commit("feat: 캐릭터 답변 길이 및 장기기억 프롬프트 개선", {"app/services/chat_service.py"})

stage_text("app/services/memory.py", prompt_only)
commit("refactor: 장기기억 문장 생성 방식 개선", {"app/services/memory.py"})

stage_text("app/routers/characters.py", delete_only)
commit("feat: 캐릭터 삭제 API 추가", {"app/routers/characters.py"})

run(["add", "app/routers/chat.py"])
commit("feat: 채팅방 삭제 API 추가", {"app/routers/chat.py"})

run(["add", ".gitignore", "requirements.txt", "app/services/character_image.py", "app/main.py"])
stage_text("app/routers/characters.py", image_characters)
commit(
    "feat: 캐릭터 이미지 등록 API 추가",
    {".gitignore", "requirements.txt", "app/services/character_image.py", "app/main.py", "app/routers/characters.py"},
)

run(["add", "app/schemas.py", "app/services/memory.py", "app/routers/characters.py"])
commit(
    "feat: 장기기억 수정 및 삭제 API 추가",
    {"app/schemas.py", "app/services/memory.py", "app/routers/characters.py"},
)

Path(__file__).unlink(missing_ok=True)
status = run(["status", "--short"]).stdout
if status.strip():
    raise SystemExit(f"working tree not clean:\n{status}")
print("SPLIT_OK")
