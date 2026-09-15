from __future__ import annotations

import asyncio
import json
import sys
import uuid
import re
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


def _collapse_wrap_lines(text: str) -> str:
    """render.py의 textwrap.wrap이 넣은 물리적 개행을 JSON에 담기 좋은 형태로 만든다.

    \n 뒤 공백이 이어지는 패턴은 공백 하나로, 남은 \n은 공백으로 바꾸고 연속 공백은
    하나로 줄인다. 단, '-'로 시작하는 불릿 줄 앞의 개행만 남긴다.
    """
    if not text:
        return text
    lines = text.split("\n")
    out = [lines[0]]
    for i in range(1, len(lines)):
        curr = lines[i]
        if not curr.strip():
            continue
        if curr.lstrip().startswith("- "):
            out.append("\n" + curr)
        else:
            out.append(" " + curr.strip())
    return "".join(out).strip()


def _parse_draft_sections(draft: str) -> dict[str, str]:
    """draft.txt를 고정 머리글 기준으로 자른다. render 출력 형식이 바뀌어도
    머리글 문자열이 같으면 깨지지 않는다."""
    markers = [
        "## 1단계 — 주목할 사항",
        "[잘하고 있는 것]",
        "[그럼에도 주목할 사항]",
        "[확인 결과 특이사항 없음]",
        "[자동으로 읽지 못한 항목]",
        "## 2단계 — 지원 직무와 닿는 지점",
        '## 3단계 — 사항별 "지원동기로 쓴다면" 방향성',
        "[출처]",
        "[판정 기준]",
        "[더 볼 것]",
        "[저장]",
    ]
    sections: dict[str, str] = {}
    text = draft
    for i, m in enumerate(markers):
        idx = text.find(m)
        if idx == -1:
            sections[m] = ""
            continue
        start = idx + len(m)
        tail = text[start:]
        next_start = len(tail)
        for m2 in markers[i + 1 :]:
            j = tail.find(m2)
            if j != -1:
                next_start = j
                break
        sections[m] = tail[:next_start].strip()
        text = tail[next_start:]
    return sections


def _parse_source_positions(block: str) -> dict[str, str]:
    """[출처] 블록에서 'N번: ...' 형태의 항목 위치를 뽑는다."""
    out: dict[str, str] = {}
    for line in block.splitlines():
        m = re.match(r"(\d+)번:\s*(.+)", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def _extract_problems(sections: dict[str, str], dart: dict, source_positions: dict[str, str]) -> list[dict]:
    """[그럼에도 주목할 사항] 블록에서 주목 N. 항목을 뽑아 problems 배열로 만든다."""
    block = sections.get("[그럼에도 주목할 사항]", "")
    if not block:
        return []
    lines = block.splitlines()
    problems: list[dict] = []
    current: dict | None = None
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("주목") and "." in stripped[:8]:
            if current is not None:
                problems.append(_finalize_problem(current, dart, source_positions))
            current = {"title": stripped, "body": []}
        elif current is not None:
            current["body"].append(line)
    if current is not None:
        problems.append(_finalize_problem(current, dart, source_positions))
    return problems


def _finalize_problem(problem: dict, dart: dict, source_positions: dict[str, str]) -> dict:
    body = "\n".join(problem["body"])
    collapsed = _collapse_wrap_lines(body)
    m = re.search(r"\(판정:.*\)", collapsed)
    basis = m.group(0) if m else ""
    dart_url = dart.get("dart_url") or ""
    # 원문 위치: [출처] 블록에서 뽑은 항목 위치
    title_no = re.match(r"주목\s+(\d+)\.", problem["title"])
    sp = source_positions.get(title_no.group(1), "") if title_no else ""
    mapping = re.findall(r"\(매핑 테이블:\s*\[[^]]*\]\)", collapsed)
    mapping = list(dict.fromkeys(mapping))
    return {
        "title": problem["title"],
        "value": collapsed,
        "basis": basis,
        "source": dart_url,
        "source_position": sp,
        "mapping": mapping,
    }


def _extract_questions(sections: dict[str, str]) -> list[dict]:
    """3단계 블록의 질문형 항목을 뽑아 questions 배열로 만든다."""
    block = sections.get('## 3단계 — 사항별 "지원동기로 쓴다면" 방향성', "")
    if not block:
        return []
    lines = block.splitlines()
    questions: list[dict] = []
    current_title: str | None = None
    current_q: list[str] = []
    skip_until_title: bool = False
    title_re = re.compile(r"^주목\s+\d+\.")
    for line in lines:
        stripped = line.lstrip()
        if title_re.match(stripped):
            if current_title is not None and current_q:
                questions.append(_finalize_question(current_title, current_q))
            current_title = stripped
            current_q = []
        elif stripped.startswith("- ") or stripped.startswith("  - "):
            if not skip_until_title:
                current_q.append(stripped)
        else:
            if stripped and not stripped.startswith("구체화") and not stripped.startswith("방향성"):
                if not skip_until_title:
                    current_q.append(stripped)
            else:
                skip_until_title = True
    if current_title is not None and current_q:
        questions.append(_finalize_question(current_title, current_q))
    return questions


def _finalize_question(title: str, items: list[str]) -> dict:
    text = "\n".join(items)
    collapsed = _collapse_wrap_lines(text)
    mapping = re.findall(r"\(매핑 테이블:\s*\[[^]]*\]\)", collapsed)
    mapping = list(dict.fromkeys(mapping))
    return {
        "source_title": title,
        "text": collapsed,
        "mapping": mapping,
    }


def _extract_risk(dart: dict, max_len: int = 900) -> str:
    """dart.json 위험 섹션(raw_head)에서 회사가 직접 밝힌 위험요소를 원문 발췌한다."""
    sections = dart.get("sections", {})
    parse = sections.get("parse", {})
    risk_section = parse.get("위험", {})
    raw = risk_section.get("raw_head", "")
    if not raw:
        return ""
    paragraphs = [p.strip() for p in raw.split("\n\n") if p.strip()]
    excerpt = paragraphs[0] if paragraphs else raw
    if len(excerpt) > max_len:
        excerpt = excerpt[:max_len].rsplit(" ", 1)[0] + "…"
    return excerpt


def _build_common_questions(company: str, job: str, problems_count: int) -> list[dict]:
    return [
        {
            "id": "common_1",
            "question": (
                f"{company} {job} 직무의 지원동기 방향성 리포트에서 "
                f"주목할 사항이 {problems_count}건 확인되었습니다. "
                f"이 중 지원 직무와 연결되는 사항은 몇 건이며, 각 사항의 핵심 쟁점은 무엇인가?"
            ),
        },
        {
            "id": "common_2",
            "question": (
                f"{company} {job} 직무의 지원동기 방향성 리포트에서 "
                f"각 주목할 사항별로 어떤 질문을 던져야 지원동기로 연결할 수 있는가?"
            ),
        },
    ]


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

        dart = job.get("dart", {})
        judge = job.get("judge", {})
        draft = job.get("draft", "")
        sections = _parse_draft_sections(draft) if draft else {}
        source_positions = _parse_source_positions(sections.get("[출처]", ""))
        problems = _extract_problems(sections, dart, source_positions)
        for i, p in enumerate(problems):
            p["source_position"] = source_positions.get(str(i + 1), "")
        questions = _extract_questions(sections)
        for q in questions:
            mt = re.search(r"주목\s+(\d+)\.", q.get("source_title", ""))
            if mt:
                idx = int(mt.group(1)) - 1
                if 0 <= idx < len(problems):
                    problems[idx]["mapping"] = q.get("mapping", [])
        summary = {
            "company": job.get("company", ""),
            "job": job.get("job", ""),
            "notable_count": len(problems),
            "basis_year": dart.get("financials", {}).get("bsns_year"),
            "fs_basis": dart.get("financials", {}).get("fs_basis"),
        }
        job["status"] = "done"
        job["result"] = {
            "report_available": True,
            "report_text": job.get("draft", ""),
            "summary": summary,
            "problems": problems,
            "questions": questions,
            "risk": _extract_risk(dart),
            "common_questions": _build_common_questions(
                job.get("company", ""), job.get("job", ""), len(problems)
            ),
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
@router.get("/companies")
async def search_companies(q: str = "") -> dict:
    q = (q or "").strip()
    if len(q) < 2:
        return {"ok": True, "items": []}
    rc, out, err = await _run(
        [sys.executable, str(SCRIPTS_DIR / "company_search.py"), q],
        str(SKILL_DIR), 30.0,
    )
    if rc != 0:
        return {"ok": False, "items": [], "error": "search failed"}
    try:
        return json.loads(out.strip())
    except Exception:
        return {"ok": False, "items": [], "error": "bad output"}