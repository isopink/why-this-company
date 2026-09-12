# 왜-this-company 스킬 기준 경로.
#
# 백엔드(cwd)가 왜-this-company/ 폴더가 되도록 실행하며,
# 스크립트는 SKILL_DIR 기준 상대 경로로 그대로 호출한다.
# 산출물(dart.json, judge.json, kipris.json, draft.txt 등)은
# WORK_DIR / {job_id} 아래에 쓰되, 스크립트 캐시는 cwd(.cache/) 기준이다.
from __future__ import annotations

from pathlib import Path

# 이 파일이 속한 app/ 의 부모를 기준으로 프로젝트 루트를 잡는다.
_APP_DIR = Path(__file__).resolve().parent          # backend/app/
_BACKEND_DIR = _APP_DIR.parent                      # backend/
ROOT = _BACKEND_DIR.parent                           # 레포 루트

# 예선 스킬 폴더 — 복사하지 않고 그대로 호출한다.
SKILL_DIR = ROOT / "why-this-company"
SCRIPTS_DIR = SKILL_DIR / "scripts"
REFERENCES_DIR = SKILL_DIR / "references"
ASSETS_DIR = SKILL_DIR / "assets"

# 백엔드 산출물·캐시
WORK_DIR = ROOT / "work"
REPORTS_DIR = ROOT / "reports"


def ensure_work(job_id: str) -> Path:
    d = WORK_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def job_report_path(job_id: str) -> Path:
    return REPORTS_DIR / f"{job_id}.txt"


def sanitize_filename_segment(s: str) -> str:
    """리포트 파일명의 회사·직무 세그먼트에 들어갈 수 없는 문자를 제거한다."""
    out = []
    for ch in s:
        if ch.isalnum() or ch in "-_·":
            out.append(ch)
        else:
            out.append ("_")
    return "".join(out).strip("_") or "unknown"
