# 백엔드 환경변수 읽기.
#
# 값은 로그·응답·소스코드에 절대 출력하지 않는다. 존재 여부만 다룬다.
from __future__ import annotations

import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"   # 레포 루트 /.env


def _env() -> dict[str, str]:
    """.env 파일(있으면)과 프로세스 환경변수를 합쳐서 읽는다.
    .env 는 프로세스 환경에 없는 키만 보충하며, 이미 설정된 값을 덮어쓰지 않는다."""
    out: dict[str, str] = {}
    if _ENV_PATH.is_file():
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                out[key] = value
    return out


def _get(key: str) -> str | None:
    val = os.environ.get(key)
    if val:
        return val
    return _env().get(key)


class Env:
    """존재 여부만 다룬다. 값은 노출하지 않는다."""

    def __init__(self) -> None:
        self._open_dart_key: str | None = _get("OPEN_DART_API_KEY")
        self._kippris_key: str | None = _get("KIPRIS_API_KEY")
        self._upstage_key: str | None = _get("UPSTAGE_API_KEY")

    @property
    def open_dart_key(self) -> str | None:
        return self._open_dart_key

    @property
    def kippris_key(self) -> str | None:
        return self._kippris_key

    @property
    def upstage_key(self) -> str | None:
        return self._upstage_key

    @property
    def open_dart_available(self) -> bool:
        return bool(self._open_dart_key)

    @property
    def kippris_available(self) -> bool:
        return bool(self._kippris_key)

    @property
    def upstage_available(self) -> bool:
        return bool(self._upstage_key)


def setup_logging() -> None:
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def log_env_summary() -> str:
    """시작 로그에 남길 요약. 값은 출력하지 않고 존재 여부만."""
    env = Env()
    return (
        f"OPEN_DART_API_KEY={'set' if env.open_dart_available else 'missing'}, "
        f"KIPRIS_API_KEY={'set' if env.kippris_available else 'missing'}, "
        f"UPSTAGE_API_KEY={'set' if env.upstage_available else 'missing'}"
    )
