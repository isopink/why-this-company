# -*- coding: utf-8 -*-
"""
solar_angles.py — render 호출 전, Solar Pro 4로 work/{job_id}/angles.txt 생성.

왜-this-company SKILL.md의 angles 형식 규칙과 references/reference.md의
매핑 테이블을 그대로 쓰되, 3단계는 방향성 명사구가 아니라 질문형으로 낸다.

호출: SolarPro4로 work/{job_id}/angles.txt 생성 후 render.py --angles로 넘김.
모델: solar-pro4, 키: UPSTAGE_API_KEY, httpx 직접 호출, 타임아웃 30초.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

# 레포 루트 기준
ROOT = Path(__file__).resolve().parents[3]  # backend/app/core/ → 레포 루트
SKILL_DIR = ROOT / "why-this-company"
WORK_DIR = ROOT / "work"
SCRIPTS_DIR = SKILL_DIR / "scripts"
REFERENCES_DIR = SKILL_DIR / "references"

UPSTAGE_API_KEY = os.environ.get("UPSTAGE_API_KEY")
if not UPSTAGE_API_KEY:
    # subprocess 실행 시 .env가 프로세스 환경에 없으므로 파일에서 직접 읽는다
    try:
        env_text = (ROOT / ".env").read_text(encoding="utf-8", errors="replace")
        for line in env_text.splitlines():
            line = line.strip()
            if line.startswith("UPSTAGE_API_KEY="):
                val = line.split("=", 1)[1].strip()
                val = val.strip('"').strip("'")
                if val:
                    UPSTAGE_API_KEY = val
                break
    except FileNotFoundError:
        pass

UPSTAGE_BASE = "https://api.upstage.ai/v1"
UPSTAGE_MODEL = "solar-pro4"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _job_family(job: str) -> str:
    """직무명 → 6계열. render.py FAMILY_KEYWORDS와 동일하게."""
    jl = job.lower()
    rules = [
        ("R&D", ("연구", "r&d", "rnd", "소재", "신약", "배터리연구")),
        ("개발(SW·데이터)", ("데이터", "sw", "소프트웨어", "개발", "ai", "it", "인공지능", "머신러닝", "프로그래", "시스템")),
        ("공정·생산", ("공정", "생산", "품질", "제조", "설비", "qa", "qc")),
        ("기획·전략", ("기획", "전략", "pm", "사업개발", "상품기획", "경영기획")),
        ("영업·마케팅", ("영업", "마케팅", "세일즈", "bd", "브랜드", "md")),
        ("재무·구매(SCM)", ("재무", "회계", "구매", "scm", "자금", "원가", "물류")),
    ]
    for fam, keys in rules:
        if any(k in jl for k in keys):
            return fam
    return "R&D"  # 기본값


def _build_prompt(company: str, job: str, dart: dict, judge: dict, reference_md: str) -> str:
    """Solar 호출용 프롬프트 구성. angles 형식 규칙 + 매핑 테이블 + 질문형 3단계."""
    problems = judge.get("problems", [])
    job_family = _job_family(job)

    # 1단계 문제 목록 — Solar가 2·3단계 쓸 때 사용할 값만 발췌
    problem_blocks = []
    for i, p in enumerate(problems, 1):
        t = p.get("type", "")
        value = p.get("value", "")
        basis = p.get("basis", "")
        problem_blocks.append(f"주목 {i}. [{t}] {value} (근거: {basis})")
    problem_text = "\n".join(problem_blocks) if problem_blocks else "판정된 주목할 사항이 없습니다."

    prompt = f"""당신은 'why-this-company' 스킬의 각도(angles) 작성 에이전트입니다.
아래 정보를 바탕으로 work/angles.txt 를 작성합니다.

## 입력 데이터
- 회사: {company}
- 지원 직무: {job} (직무 계열: {job_family})
- 주목할 사항(1단계 문제 목록, render가 만든 것):
{problem_text}

## angles.txt 형식 규칙 (반드시 지킬 것)
angles.txt 는 아래 두 헤더로 구성합니다. render.py 가 이 형식을 검사하며,
위반 시 중단하고 이유를 출력합니다. 손으로 작성했을 때 95자 초과로 한 번
튕긴 사례가 있으니 특히 길이 제한을 엄격히 지키세요.

```
## 2단계
<직무> 직무 -> 문제 N(<문제명>). <접점 1~2문장>
## 3단계
주목 N. <1단계 항목명 그대로>
이 사항을 지원동기로 쓴다면
  - <방향성 질문> (매핑 테이블: [<유형> × <직무계열>])
  - <방향성 질문> (매핑 테이블: [...])
구체화 참고 — <KIPRIS 키워드·회사 숫자로 좁힌 한 문단, 있을 때만>
```

## 반드시 지킬 것
1. **2단계**: 문제당 한 줄, 전체 260자 이내. 방향성 목록은 쓰지 않는다.
2. **3단계**: "주목 N." 제목은 1단계 항목명을 그대로 쓴다.
3. **3단계 방향성**: 각 항목 95자 이내. **질문형**으로 작성한다 —
   "~에 기여하고 싶다", "~하겠다" 같은 1인칭 희망 표현이 아니라,
   "왜 ~해야 하는가?", "어떻게 ~할 것인가?" 같은 질문 문장으로 쓴다.
   예시: "- 대체 소재·내재화 기술 연구는 수입 원재료 의존도를 어떻게 낮출 수 있는가? (매핑 테이블: [의존 × R&D])"
4. **매핑 좌표**: 지원 직무 계열({job_family})의 칸만 쓴다.
   옆 계열을 끌어오지 않는다. render.py 가 검사해 중단한다.
   형식: (매핑 테이블: [유형 × 직무계열]) — 예: [의존 × R&D]
5. **수위 원칙**: 신입 관점 — 수립·확보·조달이 아니라 분석·검증·지원·모니터링.
6. **구체화**: 매핑 테이블 골격 문구 + 회사 숫자·고유명사를 덧붙인다.
   테이블 밖 방향성 창작 금지. 숫자는 1단계 문제 목록에 있는 것만 쓴다.
7. **KIPRIS/회사 숫자**: 있을 때만 "구체화 참고" 한 문단으로.
8. render.py 는 3단계 길이(95자), 1인칭 표현, 매핑 좌표를 검사한다.
   위반하면 렌더링이 중단되고, 최대 2회 재시도 후에도 실패하면 angles 없이
   1단계만 렌더한다.
9. 3단계는 방향성 명사구가 아니라 **질문형**으로 내야 한다.
   각 주목할 사항마다 답해야 할 질문 하나를 만든다.
10. "미판정", "판정 불가", "파싱" 같은 내부 용어는 쓰지 않는다.

## 참조: 매핑 테이블 (reference.md 4절)
{reference_md}

## 작업 지시
1. 문제 목록(1단계)에서 지원 직무 계열({job_family})과 닿는 문제만 골라
   2단계·3단계를 작성한다.
2. 2단계는 직무와 닿는 지점을 1~2문장으로.
3. 3단계는 각 문제별로 매핑 테이블에서 해당 칸의 방향성 골격 2~3개를
   질문형으로 바꾸고, 회사 숫자로 구체화한다.
4. 각 3단계 항목은 반드시 95자 이내로, 질문형으로.
5. 결과물을 아래 형식으로 work/angles.txt 에 쓴다 (헤더 포함):
```
## 2단계
...
## 3단계
...
구체화 참고 — ...
```

출력은 angles.txt 내용만 반환한다. 다른 설명은 쓰지 않는다.
"""
    return prompt


def generate_angles(job_id: str, company: str, job: str) -> Path:
    """Solar Pro 4로 work/{job_id}/angles.txt 생성 후 경로 반환."""
    job_dir = WORK_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    dart_path = job_dir / "dart.json"
    judge_path = job_dir / "judge.json"

    if not dart_path.exists():
        raise FileNotFoundError(f"dart.json 없음: {dart_path}")
    if not judge_path.exists():
        raise FileNotFoundError(f"judge.json 없음: {judge_path}")

    dart = _load_json(dart_path)
    judge = _load_json(judge_path)
    reference_md = _load_text(REFERENCES_DIR / "reference.md")

    company_name = judge.get("company") or dart.get("input") or company
    problems = judge.get("problems", [])

    if not problems:
        # 판정된 문제가 없으면 angles 없이 render 하도록 빈 파일 생성하지 않음
        # jobs.py에서 처리
        return None  # type: ignore

    prompt = _build_prompt(company_name, job, dart, judge, reference_md)

    # Solar 호출
    if not UPSTAGE_API_KEY:
        raise RuntimeError("UPSTAGE_API_KEY가 설정되지 않았습니다")

    system_prompt = "당신은 'why-this-company' 스킬의 각도 작성 에이전트입니다. angles.txt 형식 규칙을 엄격히 지키고, 3단계는 질문형으로 작성하세요."

    try:
        resp = httpx.post(
            f"{UPSTAGE_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {UPSTAGE_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": UPSTAGE_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 2048,
                "temperature": 0.3,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Solar API 오류({e.response.status_code}): {e.response.text[:500]}")
    except httpx.TimeoutException:
        raise RuntimeError("Solar API 타임아웃 (30초)")
    except Exception as e:
        raise RuntimeError(f"Solar API 호출 실패: {e}")

    # angles.txt 작성
    angles_path = job_dir / "angles.txt"
    try:
        angles_path.write_text(content, encoding="utf-8")
    except Exception as wte:
        import sys as _sys
        _sys.stderr.write(
            f"ERROR angles_path.write_text 실패: path={angles_path}, "
            f"exists={angles_path.exists()}, parent_exists={angles_path.parent.exists()}, "
            f"error={wte!r}, content_len={len(content)}\n"
        )
        raise RuntimeError(f"angles.txt 작성 실패({wte})") from wte
    print(f"angles.txt 작성 완료: {angles_path}", file=sys.stderr)
    return angles_path


if __name__ == "__main__":
    # 직접 실행 시: python solar_angles.py <job_id> <company> <job>
    if len(sys.argv) < 4:
        print("사용법: python solar_angles.py <job_id> <company> <job>", file=sys.stderr)
        sys.exit(1)
    job_id, company, job = sys.argv[1], sys.argv[2], sys.argv[3]
    path = generate_angles(job_id, company, job)
    if path:
        print(str(path))
    else:
        print("판정된 문제 없음 — angles 없이 render 진행", file=sys.stderr)
