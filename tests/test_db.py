
import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

try:
    with psycopg.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        connect_timeout=5,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user;")
            db_name, db_user = cur.fetchone()

            print("데이터베이스 연결 성공!")
            print(f"DB 이름: {db_name}")
            print(f"DB 사용자: {db_user}")

except Exception as e:
    print("데이터베이스 연결 실패!")
    print(f"오류: {e}")
