# 회원가입, 로그인, 인증
from fastapi import APIRouter, HTTPException
from psycopg.errors import UniqueViolation

from app.database import get_connection
from app.schemas import UserCreate, UserLogin
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

        access_token = create_access_token(db_user[0])

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
