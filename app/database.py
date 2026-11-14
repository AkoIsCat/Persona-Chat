# PostgreSQL 연결
import os
import re

import psycopg
from dotenv import load_dotenv

from app.core.perf import perf_span

load_dotenv()

_SQL_LITERAL = re.compile(r"'([^']|'')*'")


def _query_label(query) -> str:
    """바인딩 값과 문자열 리터럴은 남기지 않고 문장 형태만 표시한다."""
    if isinstance(query, (bytes, bytearray)):
        text = query.decode("utf-8", errors="replace")
    else:
        text = str(query)
    text = _SQL_LITERAL.sub("'?'", text)
    compact = " ".join(text.split())
    if len(compact) > 90:
        compact = compact[:90] + "..."
    return f"DB 쿼리 {compact}"


class _TimedCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._cursor.__exit__(exc_type, exc, tb)

    def execute(self, query, params=None, **kwargs):
        with perf_span(_query_label(query)):
            if params is None:
                result = self._cursor.execute(query, **kwargs)
            else:
                result = self._cursor.execute(query, params, **kwargs)
        if result is self._cursor:
            return self
        return result


class _TimedConnection:
    def __init__(self, connection):
        self._connection = connection
        self._transaction = None

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def cursor(self, *args, **kwargs):
        return _TimedCursor(self._connection.cursor(*args, **kwargs))

    def __enter__(self):
        self._connection.__enter__()
        self._transaction = perf_span("DB 트랜잭션")
        self._transaction.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        closing = "DB 롤백" if exc_type else "DB 커밋"
        try:
            with perf_span(closing):
                return self._connection.__exit__(exc_type, exc, tb)
        finally:
            if self._transaction is not None:
                self._transaction.__exit__(None, None, None)
                self._transaction = None


def get_connection():
    return _TimedConnection(
        psycopg.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            dbname=os.getenv("DB_NAME", "personachat"),
            user=os.getenv("DB_USER", "persona"),
            password=os.getenv("DB_PASSWORD"),
            connect_timeout=5,
        )
    )