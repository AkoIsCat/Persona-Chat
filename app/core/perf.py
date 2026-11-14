# 성능 진단용 소요 시간 로그. 응답과 호출 횟수는 바꾸지 않는다.
import logging
import time
from contextlib import contextmanager
from datetime import datetime
from functools import wraps

_logger = logging.getLogger("personachat.perf")
if not _logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def _log(message: str) -> None:
    try:
        _logger.info(message)
    except Exception:
        return


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


@contextmanager
def perf_span(label: str):
    """구간의 시작 시각, 종료 시각, 소요 시간을 로그로 남긴다."""
    started = time.perf_counter()
    _log(f"[PERF] {label} 시작: {_timestamp()}")
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        _log(f"[PERF] {label} 종료: {_timestamp()}")
        _log(f"[PERF] {label}: {elapsed:.2f}초")


def perf_timed(label: str):
    """함수 전체를 하나의 성능 구간으로 기록한다."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with perf_span(label):
                return func(*args, **kwargs)

        return wrapper

    return decorator


class RequestPerfMiddleware:
    """HTTP 요청이 앱을 통과하는 전체 시간을 기록한다."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        with perf_span(f"전체 요청 {method} {path}"):
            await self.app(scope, receive, send)
