# why-this-company

DART 사업보고서의 숫자로 "왜 이 회사인가"를 스스로 답하게 돕는 웹 서비스입니다.
Upstage MABC 해커톤 예선 스킬 `why-this-company`를 결선에서 서비스 MVP로 확장했습니다.

- 서비스 주소: https://why-this-company.onrender.com
- LLM: Upstage Solar Pro 4 (`solar-pro4`)만 사용

## 무엇을 하나

1. **탐색**: 지구본에서 직무와 회사를 고릅니다.
2. **준비 (선택)**: 회사의 IR 자료 PDF를 올립니다.
3. **리포트**: DART 공시에서 규칙으로 찾은 주목할 사항을 수치와 원문 위치로 보여줍니다.
4. **질문**: 주목 사항과 IR 자료를 바탕으로 3~7개의 면접형 질문을 던지고, 마지막은 지원 이유로 모읍니다.
5. **모아보기**: 내 답변을 정리해 내려받습니다.

모범답안이나 자기소개서 문장은 만들지 않습니다. 고르고 쓰는 것은 사용자 몫입니다.

## 설계: 매핑표를 하네스로 쓰는 구조

| 단계 | 담당 | 내용 |
|---|---|---|
| 판정 | `judge.py` (규칙) | DART 수치로 판정 유형 결정 (편중, 급변, 의존, 정체/역성장, 수익성 악화, 투자 확대) |
| 칸 선택 | 매핑표 | 판정 유형(IR은 주제) × 지원 직무 계열이 만나는 칸만 사용 |
| 작성 | Solar Pro 4 | 칸의 내용을 회사 숫자와 고유명사로 구체화 |
| 검사 | `render.py`, `verify.py`, `ir.py` | 매핑 좌표, 수치, 인용문 확인 |
| 표시 | 프론트엔드 | 결과와 원문 근거 표시 |

- LLM은 작성 단계에서만 사용합니다. 판정은 결정론적 코드가 합니다.
- 공시 매핑표: `why-this-company/references/reference.md` 4장 (판정 유형 × 직무 6계열, 42칸)
- IR 매핑표: `backend/app/api/ir.py`의 `MAPPING` (IR 주제 6개 × 직무 6계열, 36칸)
- 직무 6계열: 공정·생산 / R&D / 기획·전략 / 개발(SW·데이터) / 영업·마케팅 / 재무·구매(SCM)

### 검사와 실패 처리

- 공시: 지원 직무가 아닌 열의 칸을 쓰면 `render.py`가 리포트 생성을 멈춥니다.
- 공시: 리포트의 금액·비율을 DART 원문과 대조합니다 (허용 오차 1억 원, 0.5%p). 결과는 기록으로 남깁니다.
- 공시: Solar 출력이 형식 검사를 통과하지 못하면 최대 2회 다시 호출하고, 그래도 실패하면 판정 결과만으로 리포트를 만듭니다.
- IR: 주제가 매핑표에 없거나, 인용문이 해당 쪽 원문에서 확인되지 않거나, 질문에 수치·1인칭 표현이 있으면 그 질문을 제외합니다.
- IR: PDF는 브라우저에서 쪽별 텍스트로만 추출해 전송합니다. 원본 파일은 서버로 보내지 않습니다.

## 폴더 구조

```
.
├── backend/
│   ├── app/
│   │   ├── __init__.py          # FastAPI 앱, 정적 파일, /api/health
│   │   ├── api/
│   │   │   ├── jobs.py          # 공시 분석 파이프라인 (/api/jobs, /api/companies)
│   │   │   └── ir.py            # IR 질문 생성·검사 (/api/ir-questions)
│   │   ├── core/
│   │   │   ├── env.py           # 환경변수 로드 (값은 로그에 남기지 않음)
│   │   │   ├── paths.py         # 스크립트·작업 폴더 경로
│   │   │   └── solar_angles.py  # Solar Pro 4 호출 (리포트 2·3단계)
│   │   └── static/index.html    # 프론트엔드 (단일 파일)
│   └── requirements.txt
├── why-this-company/            # 예선 스킬 = 서비스의 분석 엔진
│   ├── SKILL.md
│   ├── references/reference.md  # 판정 기준, 공시 매핑표
│   ├── scripts/                 # dart_client, judge, render, verify, company_search
│   └── .cache/corpCode.zip      # DART 회사코드 목록 (회사 검색용)
├── skill-to-service/SKILL.md    # 결선 진행에 사용한 안내 스킬 (서비스 코드와 무관)
└── Docs/                        # PRD 등 문서
```

`why-this-company/scripts/kipris_client.py`는 예선 스킬의 선택 보강 기능이며, 현재 서비스 파이프라인에서는 호출하지 않습니다.

## 로컬 실행

Python 3.12 기준입니다 (`.python-version`).

```
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt
.venv/Scripts/python -m uvicorn backend.app:app --host 127.0.0.1 --port 9527
```

브라우저에서 http://127.0.0.1:9527 을 엽니다.
macOS·Linux에서는 `.venv/Scripts/python` 대신 `.venv/bin/python`을 씁니다.

### 환경변수

레포 루트의 `.env` 또는 시스템 환경변수로 넣습니다. `.env`는 커밋하지 않습니다.

| 이름 | 용도 | 필수 |
|---|---|---|
| `OPEN_DART_API_KEY` | DART 사업보고서 조회 | 예 |
| `UPSTAGE_API_KEY` | Solar Pro 4 호출 | 예 (없으면 판정 결과만 제공) |
| `KIPRIS_API_KEY` | 특허 보강 (현재 서비스에서 미사용) | 아니오 |

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/health` | 상태 확인 |
| GET | `/api/companies?q=` | 회사명 검색 |
| POST | `/api/jobs` | 공시 분석 시작 `{company, job}` |
| GET | `/api/jobs/{job_id}` | 분석 진행 상태와 결과 |
| POST | `/api/ir-questions` | IR 쪽별 텍스트로 질문 생성 `{company, job, pages}` |

분석은 회사에 따라 최대 1분 정도 걸립니다.

## 측정 결과

2026-09-16 배포본 기준, 상장사 200곳을 3건씩 동시에 요청해 측정했습니다.

- 리포트 속 수치 2,666개 중 97.3%가 DART 원문과 일치
- 평균 소요 27.4초, 중앙값 21.4초 (동시 3건 기준)

## 데이터 출처와 규정

- 공시 데이터: 금융감독원 전자공시시스템 OpenDART (https://opendart.fss.or.kr)
- IR 자료: 사용자가 직접 올린 파일이며 서버에 저장하지 않습니다.
- LLM은 Upstage Solar Pro 4만 사용합니다.
- API 키는 환경변수로만 다루며 소스코드와 배포물에 포함하지 않습니다.

## 알려진 한계

- 분석 작업 상태는 서버 메모리에 저장되므로, 서버가 재시작되면 진행 중인 작업이 사라집니다.
- 공시 구조가 회사마다 달라 일부 회사는 파싱에 실패할 수 있습니다. 이 경우 원문 링크로 안내합니다.
- 수치 대조 결과는 기록으로 남기며, 대조에 실패한 수치를 화면에서 자동으로 제거하지는 않습니다.
