# PostgreSQL 연결
import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

def get_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "personachat"),
        user=os.getenv("DB_USER", "persona"),
        password=os.getenv("DB_PASSWORD"),
        connect_timeout=5,
    )