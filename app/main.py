# FastAPI 앱 실행 및 라우터 등록
from fastapi import FastAPI, HTTPException, Depends
from app.core.dependencies import get_current_user_id

from app.database import get_connection
from app.schemas import UserCreate, UserLogin
from app.core.security import hash_password, verify_password
from app.routers.auth import router as auth_router

app = FastAPI(
    title="Persona Chat API",
    description="원작 자료 기반 AI 캐릭터 챗봇 API",
    version="0.1.0",
)
app.include_router(auth_router)


@app.get("/")
def read_root():
    return {"message" : "PersonaChat API is running!"}

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/health/db")
def database_health_check():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_user;")
                db_name, db_user = cur.fetchone()

                return {
                    "status": "ok",
                    "database": db_name,
                    "user": db_user,
                }

    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed",
        )

@app.get("/auth/me")
def read_current_user(
    user_id: int = Depends(get_current_user_id),
):
    return {"user_id" : user_id}