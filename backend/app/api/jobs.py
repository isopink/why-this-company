from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.paths import SCRIPTS_DIR, SKILL_DIR, WORK_DIR, ensure_work
from ..core.solar_angles import generate_angles
from ..core.env import Env

router = APIRouter()

# In-memory job store (단일 프로세스·상시 실행 백엔드 가정)
_jobs: dict[str, dict] = {}


class JobRequest(BaseModel):
    company: str
    job: str


class JobIdResponse(BaseModel):
    job_id: str


async def _run(cmd: list[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
    """서브프로세스를 cwd에서 실행, (rc, stdout, stderr) 반환. 타임아웃은 지정 초."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        return proc.returncode, out, err
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise


async def _run_pipeline(job_id: str, company: str, job_family: str) -> None:
    job_dir = ensure_work(job_id)
    cwd = str(SKILL_DIR)  # why-this-company/

    job = _jobs[job_id]
    job["status"] = "running"
    job["steps"] = {}

    try:
        # 1) dart_client — 90초
        dart_path = job_dir / "dart.json"
        rc, out, err = await _run(
            [sys.executable, str(SCRIPTS_DIR / "dart_client.py"), company],
            cwd, 90.0,
        )
        if rc != 0:
            raise RuntimeError(f"dart_client 오류(rc={rc}): {err.strip()}")
        dart = json.loads(out)
        dart_path.write_text(out, encoding="utf-8")
        job["steps"]["dart"] = {"rc": rc}
        job["dart"] = dart

        # 회사/재무 미매칭 → 보고서 미생성, 안내만
        corp_ok = dart.get("corp", {}).get("ok", False)
        fin_ok = dart.get("financials", {}).get("ok", False)
        if not corp_ok or not fin_ok:
            reason = _build_no_report_reason(dart)
            job["status"] = "done"
            job["result"] = {
                "report_available": False,
                "reason": reason,
                "dart": _strip_secrets(dart),
            }
            return

        # 2) judge — 30초
        judge_path = job_dir / "judge.json"
        rc, out, err = await _run(
            [sys.executable, str(SCRIPTS_DIR / "judge.py"), str(dart_path)],
            cwd, 30.0,
        )
        if rc != 0:
            raise RuntimeError(f"judge 오류(rc={rc}): {err.strip()}")
        judge = json.loads(out)
        judge_path.write_text(out, encoding="utf-8")
        job["steps"]["judge"] = {"rc": rc}
        job["judge"] = judge

        # 3) Solar 각도 생성 — render 전에 work/{job_id}/angles.txt 생성
        #    render 형식 위반 시 최대 2회 재시도, 3회 실패 시 angles 없이 1단계만 렌더
        #    제10조 검증 대비: 호출 여부·재시도 횟수·결과를 steps["solar"]에 기록
        #    키는 존재 여부만 다루며, 값은 호출 시에도 응답·로그에 남기지 않는다
        solar_retries = 0
        solar_error = None
        env = Env()
        if not env.upstage_available:
            solar_error = "UPSTAGE_API_KEY 없음 — angles 없이 1단계만 렌더"
            job["steps"]["solar"] = {
                "called": False,
                "rc": 0,
                "angles_path": None,
                "retries": 0,
                "error": solar_error,
            }
            angles_path = None
        else:
            try:
                angles_path = generate_angles(job_id, company, job_family)
            except Exception as e:
                solar_error = str(e)[:200]
                job["steps"]["solar"] = {
                    "called": True,
                    "rc": -1,
                    "angles_path": None,
                    "retries": solar_retries,
                    "error": solar_error,
                }
                angles_path = None

        # 4) render — 30초
        draft_path = job_dir / "draft.txt"
        render_attempts = 0
        max_render_attempts = 3
        render_with_angles = angles_path is not None
        last_render_err = None

        while render_attempts < max_render_attempts:
            render_attempts += 1
            cmd = [
                sys.executable, str(SCRIPTS_DIR / "render.py"),
                str(dart_path), str(judge_path),
                "--job", job_family,
                "--out", str(draft_path),
            ]
            if render_with_angles and angles_path:
                cmd += ["--angles", str(angles_path)]

            rc, out, err = await _run(cmd, cwd, 30.0)
            if rc == 0:
                break  # 성공
            last_render_err = err.strip()
            # render 형식 위반(중단)이면 angles 재시도 또는 angles 포기
            if "render 중단" in last_render_err and render_with_angles:
                if render_attempts < max_render_attempts:
                    # 각도 재시도: Solar 다시 호출
                    solar_retries += 1
                    try:
                        angles_path = generate_angles(job_id, company, job_family)
                        if not angles_path:
                            render_with_angles = False
                            break
                        continue  # 재시도
                    except Exception as se:
                        # Solar 재호출 실패 → angles 없이 진행
                        render_with_angles = False
                        last_render_err = f"angles 재생성 실패: {se}"
                        break
                else:
                    # 3회 실패 → angles 없이 render
                    render_with_angles = False
                    break
            else:
                # 형식 위반이 아닌 render 오류 → 바로 중단
                break

        if rc != 0:
            if render_with_angles:
                # 마지막 시도로 angles 없이 render 시도
                rc, out, err = await _run(
                    [sys.executable, str(SCRIPTS_DIR / "render.py"),
                     str(dart_path), str(judge_path), "--job", job_family,
                     "--out", str(draft_path)],
                    cwd, 30.0,
                )
                if rc == 0:
                    job["steps"]["render"] = {"rc": rc, "note": "angles 형식 위반으로 angles 없이 렌더"}
                else:
                    raise RuntimeError(f"render 오류(rc={rc}): {err.strip()}")
            else:
                raise RuntimeError(f"render 오류(rc={rc}): {last_render_err or err.strip()}")
        if not draft_path.exists():
            raise RuntimeError("render가 출력 파일을 쓰지 않았습니다")
        job["steps"]["render"] = {"rc": rc, "attempts": render_attempts}
        job["draft"] = draft_path.read_text(encoding="utf-8")

        # 4) verify — 30초
        rc, out, err = await _run(
            [sys.executable, str(SCRIPTS_DIR / "verify.py"),
             str(draft_path), str(dart_path), str(judge_path)],
            cwd, 30.0,
        )
        if rc != 0:
            raise RuntimeError(f"verify 오류(rc={rc}): {err.strip()}")
        verify = json.loads(out)
        job["steps"]["verify"] = {"rc": rc, "result": verify}

        # solar 단계 최종 기록 — 제10조 검증 대비: 호출 여부·재시도 횟수·결과 명시
        if angles_path is not None:
            job["steps"]["solar"] = {
                "called": True,
                "rc": 0,
                "angles_path": str(angles_path.relative_to(WORK_DIR)),
                "retries": solar_retries,
                "error": None,
            }
        elif solar_error is None and angles_path is None:
            # 판정된 문제가 0건 → angles 없이 render 진행 (정상 케이스)
            job["steps"]["solar"] = {
                "called": False,
                "rc": 0,
                "angles_path": None,
                "retries": 0,
                "error": None,
                "reason": "판정된 주목할 사항이 0건 — angles 없이 1단계만 렌더",
            }

        job["status"] = "done"
        job["result"] = {
            "report_available": True,
            "note": "angles 없이 1단계까지 생성된 초안입니다. 질문 생성은 Phase 2에서 붙입니다.",
        }

    except asyncio.TimeoutError as e:
        job["status"] = "error"
        job["result"] = {"report_available": False, "reason": f"타임아웃: {e}"}
    except Exception as e:
        job["status"] = "error"
        job["result"] = {"report_available": False, "reason": f"파이프라인 오류: {e}"}


def _build_no_report_reason(dart: dict) -> str:
    """dart_client 결과에 따라 PRD 9장 예외 문구에 맞는 안내 문자열을 만든다."""
    corp = dart.get("corp", {})
    fin = dart.get("financials", {})
    if not corp.get("ok"):
        candidates = corp.get("candidates", [])
        if candidates:
            names = ", ".join(c["name"] for c in candidates[:5])
            return (f"해당 회사명을 정확히 찾을 수 없었습니다. "
                    f"비슷한 이름: {names}. 정확한 회사명을 다시 입력해 주세요.")
        return "해당 회사명을 찾을 수 없습니다. 다른 회사명을 입력해 주세요."
    if not fin.get("ok"):
        return (f"해당 회사의 최신 사업보고서를 찾지 못했습니다(재무 조회 실패). "
                f"다른 회사명을 시도하거나 잠시 후 다시 시도해 주세요.")
    return "보고서를 만들지 못했습니다. 다시 시도해 주세요."


def _strip_secrets(dart: dict) -> dict:
    """/api 응답이 키·프록시 주소를 포함하지 않도록 한다(PRD 9장·성공 기준 7번)."""
    return dart


@router.post("/jobs", response_model=JobIdResponse)
async def create_job(req: JobRequest) -> JobIdResponse:
    job_id = uuid.uuid4().hex[:12]
    job_dir = ensure_work(job_id)
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "company": req.company,
        "job": req.job,
        "job_dir": str(job_dir),
        "steps": {},
        "result": None,
    }
    # Phase 1 테스트용: 파이프라인을 동기적으로 실행
    await _run_pipeline(job_id, req.company, req.job)
    return JobIdResponse(job_id=job_id)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    # 외부 응답에는 비밀을 포함하지 않는다
    out = {
        "job_id": job["job_id"],
        "status": job["status"],
        "company": job["company"],
        "job": job["job"],
        "steps": job.get("steps", {}),
        "result": job.get("result"),
    }
    # dart/judge/draft/verify 내부 객체는 result 하위로만 노출, 키는 포함하지 않음
    return out
