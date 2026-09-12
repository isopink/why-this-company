# -*- coding: utf-8 -*-
"""
judge.py — dart_client 출력(JSON)을 받아 룰 기반으로 문제를 판정한다.

원칙
- 판정은 결정론적 룰만 사용. LLM 관여 없음
- 판정된 문제뿐 아니라 미판정 유형도 이유와 함께 출력 (룰이 돈다는 증거)
- 값이 없거나 파싱 실패면 "판정 불가"로 정직하게 표기 (조건 미충족과 구분)
- 근거 문자열에 0/없음 값은 표기하지 않는다

임계값 (references/judgment-criteria 근거)
- 편중: 상위 1-2개 항목 비중 합 >= 70%
- 급변: 전년 대비 비중 +-10%p 이상
- 의존: 매입처 소수 지정 명시 (공급사 리스트 확인)
- 정체: 매출 2년 연속 감소
- 투자확대: R&D 비중 상승 또는 신규 시설투자 공시

사용:
  python dart_client.py "회사명" > out.json
  python judge.py out.json
"""
import json
import sys

import re
_MANY = re.compile(r"다수|다양한\s*(?:공급|매입|거래)|여러\s*(?:공급|매입|거래|업체)|복수의|다변화")

TH_CONC = 70.0    # 편중 임계 (%)
TH_SWING = 10.0   # 급변 임계 (%p)
TH_PROFIT = 10.0  # 수익성 악화 임계: 영업이익 전년 대비 감소율 (%)


def _fmt_eok(won):
    """원 → '14조 2,396억' / '1,839억' (render.money 와 같은 표기)"""
    if won is None:
        return None
    eok = int(round(won / 1e8))
    jo, rem = divmod(eok, 10000)
    return f"{jo}조 {rem:,}억" if jo else f"{eok:,}억"


def _shares_from(data):
    """{이름:{amounts, shares}} → 최신 비중 dict. shares 우선, 없으면 amounts로 계산."""
    named = {}
    for name, v in (data or {}).items():
        if v.get("shares"):
            named[name] = v["shares"][0]           # 표의 첫 % = 당기 비중으로 가정
        elif v.get("amounts"):
            named[name] = v["amounts"][0]          # 임시로 금액(뒤에서 정규화)
    if named and all(isinstance(x, (int, float)) and x > 100 for x in named.values()):
        total = sum(named.values())
        if total > 0:
            named = {k: round(v * 100.0 / total, 1) for k, v in named.items()}
    return named


def judge(src):
    problems, not_triggered = [], []

    fin = src.get("financials") or {}
    parse = ((src.get("sections") or {}).get("parse")) or {}
    rcp_url = src.get("dart_url", "")

    # ---------------- 정체/역성장 (구조화 API — 판정 불가 없음)
    rev = (fin.get("매출액") or {})
    r0, r1, r2 = rev.get("당기"), rev.get("전기"), rev.get("전전기")
    if None in (r0, r1, r2):
        not_triggered.append({"type": "정체/역성장", "status": "판정 불가",
                              "reason": "매출 3개년 데이터 불완전"})
    elif r0 < r1 < r2:
        problems.append({
            "type": "정체/역성장",
            "value": f"연결 매출 {_fmt_eok(r2)} → {_fmt_eok(r1)} → {_fmt_eok(r0)} (2년 연속 감소)",
            "basis": "자사 과거 대비 추이 (근거 3순위)", "source": rcp_url})
    else:
        not_triggered.append({"type": "정체/역성장", "status": "미판정",
                              "reason": "매출 2년 연속 감소 아님"})

    # ---------------- 수익성 악화 (영업이익 전년 대비 감소율 — 구조화 API)
    op = (fin.get("영업이익") or {})
    o0, o1 = op.get("당기"), op.get("전기")
    if o0 is None or o1 is None:
        not_triggered.append({"type": "수익성 악화", "status": "판정 불가",
                              "reason": "영업이익 2개년 데이터 불완전"})
    elif o1 > 0 and (o1 - o0) / o1 * 100 >= TH_PROFIT:
        rate = (o1 - o0) / o1 * 100
        problems.append({
            "type": "수익성 악화",
            "value": f"영업이익 {_fmt_eok(o1)} → {_fmt_eok(o0)} (전년 대비 -{rate:.1f}%)",
            "basis": f"영업이익 전년 대비 {TH_PROFIT:.0f}% 이상 감소 (자사 과거 대비 추이, 근거 3순위)",
            "source": rcp_url})
    elif o1 > 0 and o0 < o1:
        not_triggered.append({"type": "수익성 악화", "status": "미판정",
                              "reason": f"영업이익 감소폭 {(o1 - o0) / o1 * 100:.1f}%로 {TH_PROFIT:.0f}% 미만"})
    else:
        not_triggered.append({"type": "수익성 악화", "status": "미판정",
                              "reason": "영업이익 전년 대비 감소 아님"})

    # ---------------- 편중 (부문 / 지역)
    for label, key in (("편중(부문)", "부문별"), ("편중(지역)", "지역별")):
        node = parse.get(key) or {}
        shares = _shares_from(node.get("data"))
        # 항목이 1개뿐이면 표를 온전히 읽지 못한 것이다. 그 1개가 70% 미만이라는
        # 이유로 "쏠림 없음"이라고 답하면 거짓 안심을 주므로 판정 불가로 돌린다.
        total = sum(v for v in shares.values() if v is not None)
        if node.get("ok") and len(shares) >= 2 and total > 105:
            # 소계·품목 행이 겹쳐 잡히면 합이 100%를 넘는다 — 그 표는 신뢰하지 않는다
            not_triggered.append({"type": label, "status": "판정 불가",
                                  "reason": f"{key} 항목 비중 합이 {total:.1f}%로 100%를 넘어 표 구조를 신뢰할 수 없음 — 원문에서 직접 확인: II. 사업의 내용 > 4. 매출 및 수주상황",
                                  "source": rcp_url})
            continue
        if not node.get("ok") or len(shares) < 2:
            detail = (f" (읽어낸 항목이 {', '.join(shares)} 하나뿐)"
                      if len(shares) == 1 else "")
            not_triggered.append({"type": label, "status": "판정 불가",
                                  "reason": f"{key} 표 파싱 실패{detail} — 원문에서 직접 확인: II. 사업의 내용 > 4. 매출 및 수주상황",
                                  "source": rcp_url})
            continue
        # 항목이 2개 이하면 상위 2개 합이 항상 ~100%라 무의미 → 1위만 판정
        k_top = 2 if len(shares) >= 3 else 1
        top = sorted(shares.items(), key=lambda x: -x[1])[:k_top]
        s = round(sum(v for _, v in top), 1)
        names = " + ".join(f"{k} {v}%" for k, v in top)
        if s >= TH_CONC:
            problems.append({
                "type": label,
                "value": f"{names} = {s}%",
                "basis": (f"상위 1-2개 합 {s}% >= {TH_CONC:.0f}% "
                          + ("(비율 열이 없어 합계 대비 계산) "
                             if any((v or {}).get("derived") for v in (node.get("data") or {}).values())
                             else "")
                          + "(참조: 공정거래법 시장지배적 추정 기준)"),
                "source": rcp_url})
        else:
            not_triggered.append({"type": label, "status": "미판정",
                                  "reason": f"상위 1-2개 합 {s}% < {TH_CONC:.0f}% ({names})"})

    # ---------------- 급변 (부문/지역 비중의 전년 대비)
    def _valid(data):
        """표를 온전히 읽은 경우만 급변 근거로 쓴다: 항목 2개 이상, 비중 합 105% 이하, 2개년 이상"""
        if len(data) < 2:
            return False
        tot = sum((v.get("shares") or [0])[0] or 0 for v in data.values())
        return tot <= 105 and any(len((v.get("shares") or [])) >= 2 for v in data.values())
    swung = False
    for key in ("부문별", "지역별"):
        data = ((parse.get(key) or {}).get("data")) or {}
        if not _valid(data):                     # 겹친 표(합>105%)·항목 1개 표는 급변 근거로 쓰지 않는다
            continue
        for name, v in data.items():
            sh = v.get("shares") or []
            if len(sh) >= 2:
                delta = round(sh[0] - sh[1], 1)
                if abs(delta) >= TH_SWING:
                    swung = True
                    kind = "급변(확대)" if delta > 0 else "급변(축소)"
                    problems.append({
                        "type": kind,
                        "value": f"{name} 비중 {sh[1]}% → {sh[0]}% ({delta:+.1f}%p)",
                        "basis": f"전년 대비 {TH_SWING:.0f}%p 이상 변동",
                        "source": rcp_url})
    if not swung:
        # 표를 온전히 읽은 경우(항목 2개 이상)에만 "변동 없음"이라고 말한다.
        # 항목 1개에 2개년만 있으면 편중은 판정 불가인데 급변만 해당 없음이 되는 모순이 생긴다
        has_series = any(_valid(((parse.get(key) or {}).get("data")) or {}) for key in ("부문별", "지역별"))
        not_triggered.append({
            "type": "급변(확대/축소)",
            "status": "미판정" if has_series else "판정 불가",
            "reason": "±10%p 이상 변동 항목 없음" if has_series
                      else "2개년 비중 시계열 파싱 실패 — 원문에서 직접 확인: II. 사업의 내용 > 4. 매출 및 수주상황"})

    # ---------------- 의존
    raw_node = parse.get("원재료") or {}
    sup = raw_node.get("suppliers") or []
    if not raw_node.get("ok"):
        not_triggered.append({"type": "의존", "status": "판정 불가",
                              "reason": "원재료 섹션 파싱 실패 — 원문에서 직접 확인: II. 사업의 내용 > 3. 원재료 및 생산설비", "source": rcp_url})
    elif sup and _MANY.search(raw_node.get("raw_head") or ""):
        # 이름이 몇 개 뽑혔더라도 원문이 "다수·여러 공급처"라고 서술하면 소수 지정이 아니다.
        # 농심: "대한제분, CJ제일제당, 사조동아원 등 국내의 주요한 다수의 제분사와 거래중"
        not_triggered.append({"type": "의존", "status": "미판정",
                              "reason": "매입처가 다수로 서술됨 — 소수 지정 아님"})
    elif sup:
        problems.append({
            "type": "의존",
            "value": f"주요 매입처 소수 지정: {', '.join(sup[:4])}",
            "basis": "매입처 소수 지정 명시 (참조: K-IFRS 1108호 주요고객 공시 취지)",
            "source": rcp_url})
    else:
        not_triggered.append({"type": "의존", "status": "미판정",
                              "reason": "매입처 소수 지정 정황 없음"})

    # ---------------- 투자 확대
    rnd = parse.get("연구개발") or {}
    ratios = [x for x in (rnd.get("ratios") or []) if 0 < x <= 30]  # R&D 비중 상식 범위 밖(오인식) 제거
    facility = "시설" in ((parse.get("위험") or {}).get("raw_head", "")) or \
               "증설" in ((raw_node.get("raw_head") or ""))
    if not rnd.get("ok"):
        not_triggered.append({"type": "투자 확대", "status": "판정 불가",
                              "reason": "연구개발 섹션 파싱 실패 — 원문에서 직접 확인: II. 사업의 내용 > 6. 주요계약 및 연구개발활동", "source": rcp_url})
    elif len(ratios) >= 2 and ratios[0] > ratios[1]:
        problems.append({
            "type": "투자 확대",
            "value": f"R&D 비중 {ratios[1]}% → {ratios[0]}% 상승",
            "basis": "R&D 비중 전년 대비 상승", "source": rcp_url})
    elif facility:
        problems.append({
            "type": "투자 확대",
            "value": "신규 시설투자 정황 (원문 확인)",
            "basis": "시설투자 언급 — 수치는 원문 확인 필요", "source": rcp_url})
    else:
        not_triggered.append({"type": "투자 확대", "status": "미판정",
                              "reason": "R&D 비중 상승·시설투자 정황 없음"})

    return {
        "company": (src.get("corp") or {}).get("name", src.get("input")),
        "fs_basis": fin.get("fs_basis"),
        "rcp_no": src.get("rcp_no"),
        "dart_url": rcp_url,
        "problems": problems,
        "not_triggered": not_triggered,
    }


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "-"
    src = json.load(open(path, encoding="utf-8")) if path != "-" else json.load(sys.stdin)
    print(json.dumps(judge(src), ensure_ascii=False, indent=2))
