# -*- coding: utf-8 -*-
"""
kipris_client.py — KIPRIS에서 기업의 최근 특허 출원 키워드를 가져온다.

역할 (references/reference.md 데이터 소스 표 그대로)
- "판정은 DART, 살은 KIPRIS": 판정에는 쓰지 않는다.
- "투자 확대" 판정의 실체 보강 + R&D 계열 방향성의 기술명 구체화용.
- 실패(키 없음/검색 0건/파싱 실패)는 스킬을 멈추지 않는다 —
  {"ok": False, ...}를 돌려주고 리포트에는 "특허 정보 확인 불가"로 표기.

원칙
- 키는 환경변수 KIPRIS_API_KEY 에서만 읽는다 (하드코딩 금지)
- 기업당 호출 1회 (월 1,000건 한도 보호)
- 실패 시 5초 후 1회 재시도

사용:
  python kipris_client.py "삼성바이오로직스"
  → {"ok": true, "count": N, "keywords": [...], "titles_sample": [...]}
"""
import collections
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE = ("http://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice"
        "/getAdvancedSearch")

# 발명의명칭에서 걸러낼 일반어 (기술 키워드가 아닌 것)
STOP = {"방법", "장치", "시스템", "제조", "조성물", "및", "이를", "위한", "용",
        "그", "제조방법", "포함하는", "이용한", "기반", "용도", "the", "and",
        "of", "for", "method", "apparatus", "system",
        "또는", "또한", "등", "상기", "하는", "있는", "통한", "따른", "이의", "그의",
        "구비한", "갖는", "형", "유형", "with", "using", "same", "thereof", "having",
        "복수", "단수", "개의", "제어", "구성", "동작", "처리", "수행", "제공", "생성",
        "기반", "관련", "다수", "일부", "전체", "부분", "복합", "단일", "구조", "형태"}


def _api_key():
    return os.environ.get("KIPRIS_API_KEY")


_CORP_WORDS = ("주식회사", "유한회사", "합자회사", "재단법인", "사단법인", "농업회사법인")


def _norm_applicant(name):
    """출원인명에서 공백·괄호·법인격 표기를 지워 비교용 문자열을 만든다."""
    s = (name or "")
    for w in _CORP_WORDS:
        s = s.replace(w, "")
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
    return "".join(out).lower()


def _same_company(applicant, query):
    """출원인명이 검색한 회사명으로 시작하면 같은 회사로 본다."""
    a = _norm_applicant(applicant)
    q = _norm_applicant(query)
    if not a or not q:
        return False
    return a.startswith(q)


def _get(params):
    url = BASE + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt == 1:
                time.sleep(5)
    raise RuntimeError(f"KIPRIS 호출 실패(2회): {last}")


def _keywords(titles, top=5):
    """발명의명칭들에서 2글자 이상 토큰 빈도 상위 추출."""
    cnt = collections.Counter()
    for t in titles:
        for tok in re.findall(r"[가-힣A-Za-z]{2,}", t):
            if tok in STOP or tok.lower() in STOP:
                continue
            if tok[-1] in "의는한된을를이가과와로써서":
                continue
            if tok.endswith(("하는", "되는", "위한", "따른", "관한", "대한", "갖는", "있는")):
                continue
            cnt[tok] += 1
    return [w for w, c in cnt.most_common(top) if c >= 2] or \
           [w for w, _ in cnt.most_common(3)]


def fetch(company_name, years=3):
    key = _api_key()
    if not key:
        return {"ok": False, "reason": "KIPRIS_API_KEY 환경변수 없음 — 특허 정보 확인 불가"}
    try:
        # 출원인명 검색, 최근 출원일 내림차순
        xml_text = _get({
            "applicant": company_name,
            "numOfRows": "80",
            "pageNo": "1",
            "sortSpec": "AD",       # 출원일자
            "descSort": "true",
            "ServiceKey": key,
        })
        root = ET.fromstring(xml_text)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"호출/파싱 실패 — {e}"}

    # 응답 항목: item > inventionTitle / applicationDate
    import datetime
    cutoff = datetime.date.today().year - years
    titles = []
    applicants = collections.Counter()
    for item in root.iter():
        if item.tag.lower().endswith("item"):
            title = None
            year_ok = True
            appl = None
            for ch in item:
                tag = ch.tag.lower()
                if "inventiontitle" in tag:
                    title = (ch.text or "").strip()
                if "applicantname" in tag and ch.text:
                    appl = ch.text.strip()
                if "applicationdate" in tag and ch.text:
                    m = re.match(r"(\d{4})", ch.text.strip())
                    if m and int(m.group(1)) < cutoff:
                        year_ok = False
            if title and year_ok and appl and _same_company(appl, company_name):
                titles.append(title)
                applicants[appl] += 1
    if not titles:
        return {"ok": False,
                "reason": "출원인명 검색 0건 — 법인명 표기 차이 가능성, 특허 정보 확인 불가"}
    return {
        "ok": True,
        "count": len(titles),
        "years": years,
        "keywords": _keywords(titles),
        "applicants": [a for a, _ in applicants.most_common(3)],
        "titles_sample": titles[:5],
        "note": "판정에는 미사용 — 투자 확대 보강 및 R&D 방향성 구체화용",
    }


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "삼성바이오로직스"
    print(json.dumps(fetch(name), ensure_ascii=False, indent=2))
