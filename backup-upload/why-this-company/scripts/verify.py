# -*- coding: utf-8 -*-
"""
verify.py — 최종 리포트 텍스트의 모든 수치를 dart_client 원본과 재대조한다.

검증 3층
1) 직접값: 원본 JSON의 모든 숫자 (원 단위·억 환산 ±1억 허용)
2) 파생값: 원본 두 수치의 비율/합/증감으로 ±0.5%p(또는 ±1억) 안에서 재현되는 값
3) 그 외: "원본 미존재" — 해당 문장은 삭제 대상

사용:
  python verify.py report.txt source.json
  → 이슈 목록 JSON. 이슈 0건이면 통과.
"""
import itertools
import json
import re
import sys

EOK = 1e8


def _collect_numbers(obj, out):
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_numbers(v, out)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.append(float(obj))
        if 0 < float(obj) <= 100:          # 비중·비율 후보
            out.append(("pct", round(float(obj), 1)))
    elif isinstance(obj, str):
        # 근거 문자열 안의 "45,473억", "68.1%" 도 원본으로 인정
        for m in re.finditer(r"(-?\d[\d,]*(?:\.\d+)?)\s*(억|%|원)?", obj):
            try:
                v = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            unit = m.group(2)
            if unit == "억":
                out.append(v * EOK)
            elif unit == "%":
                out.append(("pct", v))
            elif unit == "원":
                out.append(v)


def build_source_sets(src):
    raw = []
    _collect_numbers(src, raw)
    won = sorted({x for x in raw if isinstance(x, float) and abs(x) >= 1e4})
    pcts = sorted({x[1] for x in raw if isinstance(x, tuple)})
    eok = sorted({round(x / EOK) for x in won if abs(x) >= 1e7})

    # 파생 %: 큰 값들 쌍의 비율 + 비중 합/차
    big = sorted([x for x in won if x >= 1e10], reverse=True)[:12]
    # 파생 비율의 분모는 최대값 3개(=매출 3개년)로 제한 — 무관한 값끼리의
    # 우연한 비율 일치(오탐 통과)를 막는다
    denoms = big[:3]
    derived_pct = set(pcts)
    for a in big:
        for b in denoms:
            if b and a <= b:
                derived_pct.add(round(a / b * 100.0, 1))
    # 합/차 파생은 만들지 않는다 — 필요한 합계·증감은 judge 근거 문자열에
    # 이미 존재하며, 임의 %쌍의 합을 인정하면 우연 일치 오탐 통과가 생긴다
    return {"won": set(won), "eok": set(eok), "pct": derived_pct}


REPORT_NUM = re.compile(
    r"(?:(\d+)\s*조\s*)?(-?\d[\d,]*(?:\.\d+)?)\s*(억|%p|%|원)")


# 검증에서 제외하는 블록. [최신동향]은 웹 검색 출처라 DART 원본과 대조할 수 없다.
# 대신 SKILL.md가 그 블록의 모든 수치에 출처 URL을 붙이도록 강제한다.
# [판정 기준]은 render.py 가 내는 고정 문장(70%, ±10%p, 10%)이라 원본과 대조할 수치가 아니다.
SKIP_BLOCKS = ("[최신동향]", "[판정 기준]")


def _strip_skipped(text):
    """SKIP_BLOCKS 헤더부터 다음 [헤더] 직전까지를 제거한 본문을 돌려준다."""
    for tag in SKIP_BLOCKS:
        while tag in text:
            s = text.index(tag)
            m = re.search(r"\n\[[^\]\n]+\]", text[s + len(tag):])
            e = s + len(tag) + m.start() if m else len(text)
            text = text[:s] + text[e:]
    return text


def check(report_text, src):
    S = build_source_sets(src)
    issues, checked = [], 0
    original_len = len(report_text)
    report_text = _strip_skipped(report_text)
    skipped_chars = original_len - len(report_text)
    # "3조 원", "4조" 처럼 억 단위 없이 조로 반올림한 표기는 원본과 대조가 불가능하고
    # 정보를 잃는다(3조 4,106억→"3조"). SKILL.md가 조+억 표기를 강제하므로 이슈로 잡는다.
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*조(?!\s*\d)", report_text):
        checked += 1
        issues.append({"token": m.group(0).strip(),
                       "issue": "조 단위 반올림 표기 — '3조 4,106억' 처럼 억 단위까지 쓸 것"})
    for m in REPORT_NUM.finditer(report_text):
        jo, num_s, unit = m.groups()
        try:
            v = float(num_s.replace(",", ""))
        except ValueError:
            continue
        checked += 1
        token = m.group(0).strip()
        if unit == "억":
            eokv = v + (float(jo) * 10000 if jo else 0)
            ok = any(abs(eokv - e) <= 1 for e in S["eok"])
            if not ok:
                issues.append({"token": token, "value_eok": eokv,
                               "issue": "원본 미존재(직접·파생 불일치)"})
        elif unit in ("%", "%p"):
            ok = any(abs(v - p) <= 0.5 for p in S["pct"])
            if not ok:
                issues.append({"token": token, "value_pct": v,
                               "issue": "원본에서 재현 불가한 비율"})
        elif unit == "원":
            ok = any(abs(v - w) <= 1 for w in S["won"])
            if not ok:
                issues.append({"token": token, "issue": "원본 미존재 금액"})
    return {"checked": checked, "issues": issues,
            "skipped_blocks": list(SKIP_BLOCKS) if skipped_chars else [],
            "pass": len(issues) == 0,
            "action": "이슈 토큰이 포함된 문장은 원본 값으로 교체하거나 삭제할 것"}


if __name__ == "__main__":
    report = open(sys.argv[1], encoding="utf-8").read()
    srcs = [json.load(open(p, encoding="utf-8")) for p in sys.argv[2:]]
    src = {"merged": srcs}   # dart JSON + judge JSON 등 복수 소스 병합
    print(json.dumps(check(report, src), ensure_ascii=False, indent=2))
