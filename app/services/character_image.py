from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile

UPLOAD_ROOT = Path(__file__).resolve().parents[2] / "uploads"
MAX_IMAGE_BYTES = 5 * 1024 * 1024

CONTENT_TYPES = {
    ".jpg": {"image/jpeg", "image/jpg"},
    ".png": {"image/png"},
    ".webp": {"image/webp"},
}


def read_image(upload: UploadFile) -> tuple[bytes, str]:
    if upload.filename is None or not upload.filename.strip():
        raise HTTPException(status_code=400, detail="이미지 파일을 선택해 주세요.")

    content = upload.file.read(MAX_IMAGE_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="이미지 파일을 선택해 주세요.")
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="이미지 크기는 5MB 이하여야 합니다.")

    extension = detect_image_extension(content)
    if extension is None:
        raise HTTPException(status_code=400, detail="지원하지 않는 이미지 형식입니다.")

    content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if content_type not in CONTENT_TYPES[extension]:
        raise HTTPException(status_code=400, detail="지원하지 않는 이미지 형식입니다.")

    return content, extension


def detect_image_extension(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    return None


def save_character_image(character_id: int, content: bytes, extension: str) -> tuple[Path, str]:
    directory = UPLOAD_ROOT / "characters" / str(character_id)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{extension}"
    path = directory / filename
    path.write_bytes(content)
    return path, f"/uploads/characters/{character_id}/{filename}"


def remove_file(path: Path | None) -> None:
    if path is not None and path.is_file():
        path.unlink()


def remove_managed_image(image_url: str | None, character_id: int) -> None:
    prefix = f"/uploads/characters/{character_id}/"
    if not image_url or not image_url.startswith(prefix):
        return

    relative = image_url.removeprefix("/uploads/")
    path = (UPLOAD_ROOT / relative).resolve()
    root = UPLOAD_ROOT.resolve()
    if root not in path.parents or not path.is_file():
        return
    path.unlink()
