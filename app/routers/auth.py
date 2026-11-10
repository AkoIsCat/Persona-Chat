# 회원가입, 로그인, 인증
from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation

from app.core.dependencies import get_current_user_id
from app.database import get_connection
from app.schemas import UserCreate, UserLogin, UserNicknameUpdate, UserPasswordUpdate
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
)

router = APIRouter()

@router.post("/users", status_code=201)
def create_user(user: UserCreate):
    hashed_password = hash_password(user.password)

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (email, password_hash, nickname)
                    VALUES (%s, %s, %s)
                    RETURNING id, email, nickname, created_at
                    """,
                    (
                        user.email,
                        hashed_password,
                        user.nickname,
                    ),
                )

                new_user = cur.fetchone()

            return {
                "id": new_user[0],
            "email": new_user[1],
            "nickname": new_user[2],
            "created_at": new_user[3],
            }

    except UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="이미 가입된 이메일입니다.",
        )

@router.post("/login")
def login(user: UserLogin):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, email, nickname ,password_hash
                FROM users
                WHERE email = %s
                """,
                (user.email,),
            )

            db_user = cur.fetchone()

        if db_user is None:
            raise HTTPException(
                status_code=401,
                detail="이메일 또는 비밀번호가 올바르지 않습니다.",
            )

        is_valid = verify_password(
            user.password,
            db_user[3],
        )

        if not is_valid:
            raise HTTPException(
                status_code=401,
                detail="이메일 또는 비밀번호가 올바르지 않습니다.",
            )

        access_token = create_access_token(db_user[0], remember=user.remember)

        return {
            "message" : "로그인 성공",
            "access_token": access_token,
            "token_type": "bearer",
            "user" : {
                "id" : db_user[0],
                "email" : db_user[1],
                "nickname" : db_user[2],
            },
        }

@router.patch("/users/me")
def update_current_user(
    payload: UserNicknameUpdate,
    user_id: int = Depends(get_current_user_id),
):
    nickname = payload.nickname.strip()
    if not nickname:
        raise HTTPException(
            status_code=422,
            detail="닉네임을 입력해 주세요.",
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET nickname = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING id, email, nickname
                """,
                (nickname, user_id),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="사용자를 찾을 수 없습니다.",
        )

    return {
        "id": row[0],
        "email": row[1],
        "nickname": row[2],
    }

@router.put("/users/me/password")
def update_current_user_password(
    payload: UserPasswordUpdate,
    user_id: int = Depends(get_current_user_id),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT password_hash
                FROM users
                WHERE id = %s
                """,
                (user_id,),
            )
            row = cur.fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="사용자를 찾을 수 없습니다.",
                )

            if not verify_password(payload.current_password, row[0]):
                raise HTTPException(
                    status_code=400,
                    detail="기존 비밀번호가 올바르지 않습니다.",
                )

            cur.execute(
                """
                UPDATE users
                SET password_hash = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING id
                """,
                (hash_password(payload.new_password), user_id),
            )
            updated = cur.fetchone()

    return {
        "user_id": updated[0],
        "message": "비밀번호가 변경되었습니다.",
    }

@router.delete("/users/me")
def delete_current_user(
    user_id: int = Depends(get_current_user_id),
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM users
                WHERE id = %s
                RETURNING id
                """,
                (user_id,),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="사용자를 찾을 수 없습니다.",
        )

    return {
        "deleted": True,
        "user_id": row[0],
    }
