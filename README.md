# why-this-company

예선 당선 스킬 기반 서비스 MVP 프로젝트.

## 구조

```
.
├── README.md
├── .gitignore
├── skill-to-service/
│   └── SKILL.md            # 나만의 스킬 → 모두의 서비스 MVP 전환 킷
└── why-this-company/
    ├── assets/
    ├── references/         # 판정 기준 · 방향성 매핑 테이블
    ├── scripts/            # dart_client, judge, kipris, render, verify
    └── SKILL.md            # why-this-company 스킬 명세
```

## 스킬: why-this-company

기업명과 지원 직무(둘 다 필수)를 입력받아, DART 공시에서 회사의 주목할 사항(리스크·변화)을 숫자로 찾아 직무별 지원동기 방향성을 제시한다. 출력은 주목할 사항·직무 접점·방향성의 3단계 리포트 txt 파일(작업 공간에 저장)이며 채팅에는 요약과 다운로드 링크만 남긴다.

- 예선 제출 스킬. 결선 과제: 이 스킬 기반 서비스 MVP를 Vercel에 배포.
- 공개 데이터 출처: OpenDART API, KIPRIS (특허 키워드).
- API 키는 환경변수로 분리. 소스코드·배포물에 키 값 노출 금지.

## 결선 제출 관련

- 제출물: 공개 URL / PRD / 소스코드 / 발표자료 / 포스터 / 데모 영상
- 마감: 2026-09-16(수) 18:00 KST

## 규정 요약

- LLM: Solar Pro 4만 허용 (타 모델 외부 호출 실격)
- 개발 도구: 타임리 또는 Hermes Agent만 허용
- MCP 사용 허용 (저작권 표기 필수)
- API 키·토큰 소스코드·배포물 노출 금지
- Hermes 데이터 적재 동의 필요 (미동의 시 참가 불가)

## 참고

- 계약·업무 위탁 관련 데이터(DART 공시 등) 활용 시 출처 표기
- 개인정보 수집 시 관련 법령 준수
