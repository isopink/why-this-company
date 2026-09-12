#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render.py — 리포트 뼈대 생성 (출력 형식 보장)

1단계·[출처]·[판정 기준]·[특허 동향]·[더 볼 것]·[저장]은 dart.json / judge.json /
kipris.json 에서 기계적으로 만든다. Solar 는 2단계·3단계 문장만 work/angles.txt 에 쓴다.
판정된 문제가 0건이면 그 형식도 여기서 고정한다 — 판정 불가 항목을 문제로 포장하지 않는다.

사용:
  python3 scripts/render.py work/dart.json work/judge.json [work/kipris.json] \
      --job "연구개발" --angles work/angles.txt --out work/draft.txt
  (--job 은 필수, --angles 없으면 2·3단계는 안내문으로 채운다)

angles.txt 형식 (Solar 가 쓴다):
  ## 2단계
  <직무와 닿는 지점 1~2문장>
  ## 3단계
  문제 1. <문제명>
  이 문제를 지원동기로 쓴다면
    - <방향성> (매핑 테이블: [유형 × 직무계열])
    ...
  구체화 참고 — ...
"""
import argparse
import json
import re
import sys
import textwrap
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
WIDTH = 58   # 한 줄 글자 수 상한 — 에디터에서 자동 줄바꿈 없이 읽히는 폭

EASY = {                       # judge 유형명 → 리포트 표기
    "편중(부문)": "부문 쏠림", "편중(지역)": "지역 쏠림",
    "급변(확대)": "급한 변화(확대)", "급변(축소)": "급한 변화(축소)",
    "급변(확대/축소)": "급한 변화", "의존": "소수 거래처 의존",
    "정체/역성장": "성장 정체", "투자 확대": "투자 확대", "수익성 악화": "수익성 악화",
}
PROBLEM_TITLE = {
    "편중(부문)": "매출이 특정 부문에 쏠림", "편중(지역)": "매출이 특정 지역에 쏠림",
    "급변(확대)": "특정 항목 비중이 급격히 확대", "급변(축소)": "특정 항목 비중이 급격히 축소",
    "의존": "원재료를 소수 거래처에 의존", "정체/역성장": "매출 성장 정체",
    "투자 확대": "연구개발·설비 투자 확대", "수익성 악화": "영업이익이 전년보다 크게 감소",
}
SECTION = {                    # 유형 → 원문 위치
    "편중(부문)": "II. 사업의 내용 > 4. 매출 및 수주상황",
    "편중(지역)": "II. 사업의 내용 > 4. 매출 및 수주상황",
    "급변(확대)": "II. 사업의 내용 > 4. 매출 및 수주상황",
    "급변(축소)": "II. 사업의 내용 > 4. 매출 및 수주상황",
    "급변(확대/축소)": "II. 사업의 내용 > 4. 매출 및 수주상황",
    "의존": "II. 사업의 내용 > 3. 원재료 및 생산설비",
    "정체/역성장": "III. 재무에 관한 사항 (연결 재무제표)",
    "투자 확대": "II. 사업의 내용 > 6. 주요계약 및 연구개발활동",
    "수익성 악화": "III. 재무에 관한 사항 (연결 손익계산서)",
}
CRITERIA = [                   # [판정 기준] 표준 문장 — 다른 문구로 바꾸지 않는다
    "쏠림: 상위 1~2개 항목 비중 합이 70% 이상인 경우 (공정거래법 시장지배력 추정 기준 참조)",
    "급한 변화: 전년 대비 비중이 ±10%p 이상 변동한 경우",
    "소수 거래처 의존: 주요 매입처가 소수로 명시된 경우 (K-IFRS 1108호 주요 고객 공시 취지 참조). 원문이 다수 거래처라고 서술하면 해당 없음",
    "성장 정체: 매출이 2년 연속 감소한 경우",
    "투자 확대: 연구개발 비중이 전년보다 오르거나 신규 시설투자 정황이 있는 경우",
    "수익성 악화: 영업이익이 전년 대비 10% 이상 감소한 경우",
]
UNREADABLE = "보고서의 표 형식이 회사마다 달라 자동으로 읽지 못했습니다."


FAMILY_KEYWORDS = [   # 직무명 → 6계열 (SKILL.md 의 직무 사전과 같다). 먼저 맞는 것이 우선
    ("R&D", ("연구", "r&d", "rnd", "소재", "신약", "배터리연구")),
    ("개발(SW·데이터)", ("데이터", "sw", "소프트웨어", "개발", "ai", "it", "인공지능", "머신러닝", "프로그래", "시스템")),
    ("공정·생산", ("공정", "생산", "품질", "제조", "설비", "qa", "qc")),
    ("기획·전략", ("기획", "전략", "pm", "사업개발", "상품기획", "경영기획")),
    ("영업·마케팅", ("영업", "마케팅", "세일즈", "bd", "브랜드", "md")),
    ("재무·구매(SCM)", ("재무", "회계", "구매", "scm", "자금", "원가", "물류")),
]


def normalize_job(job):
    """'영업직무' → '영업', ' 데이터 직무 ' → '데이터'. 리포트 제목이 '영업직무 직무'가 되는 것을 막는다."""
    j = re.sub(r"\s+", " ", job or "").strip()
    j = re.sub(r"(직무|직군|파트|담당|포지션)\s*$", "", j).strip(" /·-")
    return j or "전체"


def job_family(job):
    jl = job.lower()
    for fam, keys in FAMILY_KEYWORDS:
        if any(k in jl for k in keys):
            return fam
    return None


def money(v):
    """원 → '3조 5,143억' / '1,839억'. 원본 그대로 억 단위 반올림만 한다."""
    if v is None:
        return "확인 불가"
    eok = int(round(v / 1e8))
    jo, rem = divmod(eok, 10000)
    return f"{jo}조 {rem:,}억" if jo else f"{eok:,}억"


def wrap(text, indent="", first=None):
    """WIDTH 기준 줄바꿈. 이미 짧은 줄은 그대로."""
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
            continue
        out.extend(textwrap.wrap(para, width=WIDTH, initial_indent=first if first is not None else indent,
                                 subsequent_indent=indent, break_long_words=False, break_on_hyphens=False))
    return out


def strip_internal(s):
    """judge 의 reason 문구에서 내부 용어를 걷어낸다."""
    s = re.sub(r"\s*—\s*원문에서 직접 확인:.*$", "", s)
    s = s.replace("표 파싱 실패", "표를 자동으로 읽지 못함").replace("파싱 실패", "자동으로 읽지 못함")
    s = s.replace("표 구조를 신뢰할 수 없음", "표를 정확히 읽지 못함")
    s = s.replace("2개년 비중 시계열 자동으로 읽지 못함", "2개년 비중 표를 자동으로 읽지 못함")
    return s.strip()


def build(dart, judge, kipris, job, angles, fname):
    fin = dart.get("financials") or {}
    sales = fin.get("매출액") or {}
    op = fin.get("영업이익") or {}
    url = judge.get("dart_url") or dart.get("dart_url") or ""
    company = judge.get("company") or (dart.get("corp") or {}).get("name") or dart.get("input") or ""
    year = fin.get("bsns_year")
    period = dart.get("period_no")
    basis_label = "연결 기준" if (fin.get("fs_basis") or judge.get("fs_basis")) == "연결" else "별도 기준"
    doc = "사업보고서 (" + basis_label + (f", 제{period}기" if period else "") + (f", {year}년" if year else "") + ")"

    L = []
    L.append(f"{company} / {job} 직무 — 지원동기 방향성 리포트")
    L.append("")
    L.append("## 1단계 — 주목할 사항")
    L.append("")
    # ---- 잘하고 있는 것
    # 긍정 지표만 [잘하고 있는 것]에, 감소·정체는 [참고 수치]로 — 감소를 "잘하는 것"에 두지 않는다
    good, neutral = [], []
    s3 = [sales.get("전전기"), sales.get("전기"), sales.get("당기")]
    if all(v is not None for v in s3):
        seq = f"{money(s3[0])} → {money(s3[1])} → {money(s3[2])}"
        if s3[0] < s3[1] < s3[2]:
            good.append(f"{basis_label[:2]} 매출 3개년 연속 증가: {seq}")
        elif s3[1] < s3[2]:
            good.append(f"{basis_label[:2]} 매출 당기 증가: {seq}")
        else:
            neutral.append(f"{basis_label[:2]} 매출 3개년: {seq}")
    if op.get("당기") is not None:
        cur = f"영업이익 {money(op['당기'])} ({year or '당기'}, {basis_label[:2]})"
        if op.get("전기") is not None:
            d = op["당기"] - op["전기"]
            judged_profit = any(p.get("type") == "수익성 악화" for p in (judge.get("problems") or []))
            if d > 0:
                good.append(cur + f" — 전기 {money(op['전기'])} 대비 개선")
            elif judged_profit:
                pass                             # 문제 목록에 수익성 악화로 올라가므로 여기엔 안 쓴다
            else:
                rate = (op['전기'] - op['당기']) / op['전기'] * 100 if op['전기'] else 0
                neutral.append(cur + f" — 전기 {money(op['전기'])} 대비 {rate:.1f}% 감소 (10% 미만이라 문제로 세지 않음)")
        else:
            neutral.append(cur)
    L.append("[잘하고 있는 것]")
    if good:
        for g in good:
            L += wrap(g)
    else:
        L.append("두드러진 긍정 지표 없음 (매출·영업이익 모두 전기 대비 감소 또는 정체)")
    if neutral:
        L.append("")
        L.append("[참고 수치]")
        for x in neutral:
            L += wrap(x)
    L.append("")
    # ---- 그럼에도 남는 문제
    L.append("[그럼에도 주목할 사항]")
    probs = judge.get("problems") or []
    if not probs:
        L += wrap("자동 판정으로 잡힌 주목 사항이 없습니다. 아래 [자동으로 읽지 못한 항목]에 "
                  "표시된 표를 원문에서 직접 확인해 주십시오 — 그 표가 읽히지 않아 "
                  "판정하지 못한 유형이 있을 수 있습니다.")
    for i, p in enumerate(probs, 1):
        t = p.get("type", "")
        L += wrap(f"주목 {i}. {PROBLEM_TITLE.get(t, t)}", indent="   ", first="")
        if p.get("value"):
            L += wrap(p["value"], indent="   ")
        L += wrap(f"(판정: {EASY.get(t, t)} — {strip_internal(p.get('basis', ''))})", indent="    ", first="   ")
    L.append("")
    # ---- 특이사항 없음 / 읽지 못한 항목
    nt = judge.get("not_triggered") or []
    L.append("[확인 결과 특이사항 없음]")
    none_ = [n for n in nt if n.get("status") == "미판정"]
    if none_:
        for n in none_:
            L += wrap(f"- {EASY.get(n['type'], n['type'])}: 해당 없음 — {strip_internal(n.get('reason', ''))}", indent="  ", first="")
    else:
        L.append("- (검토한 유형이 모두 문제로 잡히거나 자동으로 읽지 못했습니다)")
    L.append("")
    L.append("[자동으로 읽지 못한 항목]")
    unread = [n for n in nt if n.get("status") == "판정 불가"]
    if unread:
        seen = set()
        for n in unread:
            sec = SECTION.get(n["type"], "II. 사업의 내용")
            key = (EASY.get(n["type"], n["type"]), sec)
            if key in seen:
                continue
            seen.add(key)
            L += wrap(f"- {key[0]}: {UNREADABLE} 원문에서 직접 확인해 주십시오: {sec}", indent="  ", first="")
        L.append(f"  원문: {url}")
    else:
        L.append("- 없음")
    L.append("")
    # ---- 2·3단계
    L.append("## 2단계 — 지원 직무와 닿는 지점")
    L.append("")
    L.append("## 3단계 — 사항별 \"지원동기로 쓴다면\" 방향성")
    L.append("")
    if probs and not angles:
        sys.exit("render 중단: 판정된 주목 사항이 있는데 --angles 가 없습니다. work/angles.txt 를 쓰고 다시 실행하세요.")
    if probs and angles:
        a2, a3 = split_angles(angles)
        if not a3.strip():
            sys.exit("render 중단: work/angles.txt 에 '## 3단계' 절이 없거나 비어 있습니다. "
                     "'## 2단계' 아래 1~2문장, '## 3단계' 아래 문제별 방향성을 쓰고 다시 실행하세요.")
        if len(a2.splitlines()) > 4 or len(a2) > 260:
            sys.exit("render 중단: '## 2단계'가 너무 깁니다(문제당 한 줄, 전체 260자 이내). 방향성은 '## 3단계'로.")
        long_bullets = [b for b in a3.splitlines() if b.strip().startswith("-") and len(b.strip()) > 95]
        if long_bullets:
            sys.exit("render 중단: 3단계 방향성 항목이 너무 깁니다(항목당 95자 이내, 매핑 테이블 골격 + 회사 숫자 한 구절). "
                     "'~하고 싶다', '~기여하겠다' 같은 자소서 문장이 아니라 방향 명사구로 쓰세요. 예: " + long_bullets[0].strip()[:60] + "…")
        if re.search(r"(하고 싶다|기여하겠다|다지겠다|되고 싶다)", a3):
            sys.exit("render 중단: 3단계에 1인칭 희망 표현이 있습니다('~하고 싶다' 등). 방향성은 명사구로만 씁니다 — 문장은 사용자가 씁니다.")
        fam = job_family(job)
        if fam:
            wrong = [m.group(0) for m in re.finditer(r"\[[^\]]*×\s*([^\]]+)\]", a3)
                     if re.sub(r"\s+", "", m.group(1)) != re.sub(r"\s+", "", fam)]
            if wrong:
                sys.exit(f"render 중단: 3단계에 지원 직무 계열({fam})이 아닌 매핑 칸이 있습니다: {', '.join(wrong[:3])}. "
                         f"매핑 테이블에서 [{fam}] 열의 칸만 씁니다. 옆 열을 끌어오지 않습니다.")
        idx2 = L.index("## 2단계 — 지원 직무와 닿는 지점") + 1
        a2 = re.sub(r"문제\s*(\d+)\(", r"주목 \1(", a2)
        a3 = a3.replace("이 문제를 지원동기로 쓴다면", "이 사항을 지원동기로 쓴다면")
        L[idx2:idx2] = wrap(a2)
        # 3단계 "문제 N." 제목은 1단계 문제명으로 강제 통일 — Solar 가 제목을 다시 쓰는 것을 막는다
        titles = {i: PROBLEM_TITLE.get(p.get("type"), p.get("type", "")) for i, p in enumerate(probs, 1)}
        fixed = []
        for line in a3.splitlines():
            m = re.match(r"^\s*(?:문제|주목|사항)\s*(\d+)\s*[.．]", line)
            if m and int(m.group(1)) in titles:
                fixed.append(f"주목 {m.group(1)}. {titles[int(m.group(1))]}")
            else:
                fixed.append(line)
        L += wrap("\n".join(fixed))
        L.append("")
        L += wrap("방향성의 선택은 지원자 본인의 몫입니다. 직접 고른 방향성이어야 "
                  "\"왜 그 방향이었는가\"라는 질문에 답할 수 있습니다.")
    else:
        idx2 = L.index("## 2단계 — 지원 직무와 닿는 지점") + 1
        L[idx2:idx2] = wrap("판정된 주목 사항이 없어 직무 접점을 적지 않습니다.")
        L += wrap("판정된 주목 사항이 없어 방향성을 제시하지 않습니다. 위 [자동으로 읽지 못한 항목]의 "
                  "표를 원문에서 확인한 뒤 다시 요청해 주십시오. 판정되지 않은 유형으로 "
                  "방향성을 만들지 않습니다.")
    L.append("")
    # ---- 출처
    L.append("[출처]")
    L += wrap(f"{doc} — DART 원문: {url}", indent="  ", first="")
    for i, p in enumerate(probs, 1):
        L.append(f"  {i}번: {SECTION.get(p.get('type'), 'II. 사업의 내용')}")
    if kipris and kipris.get("ok"):
        L.append(f"KIPRIS 특허 검색 — 최근 3년 출원 {kipris.get('count', 0)}건 (분석 판단에는 사용하지 않음)")
    elif kipris is not None:
        L.append("KIPRIS 특허 검색 — 확인 불가 (분석 판단에는 사용하지 않음)")
    L.append("")
    L.append("[판정 기준] (이번 리포트에 확인·적용된 것)")
    for c in CRITERIA:
        L += wrap(c, indent="  ", first="")
    L.append("")
    if kipris and kipris.get("ok") and kipris.get("keywords"):
        L.append("[특허 동향]")
        L += wrap(f"최근 3년 출원 {kipris.get('count', 0)}건 — 키워드: {', '.join(kipris['keywords'][:5])}")
        L.append("(KIPRIS, 분석 판단에는 미사용)")
        L.append("")
    L.append("[더 볼 것] 지속가능경영보고서 (자율공시) — 별도 공시라 접수번호가 다릅니다.")
    L += wrap("DART(https://dart.fss.or.kr)에서 회사명 + \"지속가능경영보고서\"로 검색해 주십시오.")
    L.append("")
    L.append(f"[저장] {fname}")
    return "\n".join(L) + "\n"


def split_angles(text):
    m2 = re.search(r"##\s*2단계.*?\n", text)
    m3 = re.search(r"##\s*3단계.*?\n", text)
    if m2 and m3:
        return text[m2.end():m3.start()].strip(), text[m3.end():].strip()
    return text.strip(), ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dart"); ap.add_argument("judge"); ap.add_argument("kipris", nargs="?")
    ap.add_argument("--job", required=True, help="지원 직무 (필수)")
    ap.add_argument("--angles")
    ap.add_argument("--out")
    a = ap.parse_args()
    dart = json.load(open(a.dart, encoding="utf-8"))
    judge = json.load(open(a.judge, encoding="utf-8"))
    kipris = json.load(open(a.kipris, encoding="utf-8")) if a.kipris else None
    angles = open(a.angles, encoding="utf-8").read() if a.angles else ""
    company = judge.get("company") or (dart.get("corp") or {}).get("name") or dart.get("input") or "회사"
    job = normalize_job(a.job)
    fname = f"why-this-company_{company}_{job}_{datetime.now(KST).strftime('%Y%m%d')}.txt"
    text = build(dart, judge, kipris, job, angles, fname)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
        print(a.out)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
