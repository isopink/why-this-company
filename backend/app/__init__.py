# FastAPI 백엔드 진입점
#
# 왜-this-company 서비스 MVP 백엔드.
# Vercel 함수 단위 서버리스가 아닌 상시 실행 호스팅.
# 규정 제8조 6항은 배포 플랫폼 제한 없음, Vercel은 가이드 기준.
#
# 파이프라인은 why-this-company/scripts/ 를 그대로 호출하며
# 작업 디렉터리(캐시에는 영향을 주지 않음)와 산출물 저장 폴더를 분리한다.
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .core.env import setup_logging

from .api.jobs import router as jobs_router
from .api.ir import router as ir_router

setup_logging()
logger = logging.getLogger("why-this-company.backend")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시 환경변수·경로 검증 로그만 남긴다. 값은 출력하지 않는다.
    from .core.env import Env
    env = Env()
    logger.info(
        "backend.started: open_dart_key=%s, kipris_key=%s, upstage_key=%s",
        "set" if env.open_dart_key else "missing",
        "set" if env.kippris_key else "missing",
        "set" if env.upstage_key else "missing",
    )
    yield
    logger.info("backend.stopped")


app = FastAPI(
    title="why-this-company",
    description=(
        "지원 산업 경험이 거의 없는 학사 신입 취준생을 위한 "
        "공시(DART) 기반 회사별 지원동기 초안 도구 백엔드. "
        "예선 스킬 why-this-company의 스크립트를 수정 없이 호출해 "
        "판정·렌더·검증 엔진으로 사용한다."
    ),
    version="1.0.0",
)

# 정적 파일: index.html은 루트에서, 기타 자산은 /static에서 서빙
app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR), html=True),
    name="static",
)

app.include_router(jobs_router, prefix="/api")
app.include_router(ir_router, prefix="/api")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
