# -*- coding: utf-8 -*-
"""
dart_client.py — OpenDART에서 why-this-company 스킬에 필요한 데이터만 수집한다.

원칙
- 키는 환경변수 OPEN_DART_API_KEY 에서만 읽는다 (하드코딩 금지)
- corpCode ZIP / document ZIP 은 .cache/ 에 저장하고 재다운로드하지 않는다
- 요청 실패 시 5초 대기 후 1회만 재시도, 그래도 실패면 사실대로 보고
- 재무는 반드시 fs_div == "CFS"(연결) 우선, 없을 때만 OFS 폴백 + 기준 명시
- 파싱 실패는 숨기지 않는다: 섹션별 parse_ok 플래그와 원문 링크(rcpNo)를 남긴다

사용:
  python dart_client.py "삼성바이오로직스"
  → 표준출력으로 JSON (judge.py 의 입력)
"""
import json
import os
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

BASE = "https://opendart.fss.or.kr/api"
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
CACHE_DIR = SCRIPT_DIR.parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------- 공통
def _api_key():
    key = os.environ.get("OPEN_DART_API_KEY")
    if not key:
        # env.py와 동일한 로직으로 .env 보충
        # 스크립트 위치와는 달리 dart_client.py 기준 레포 루트는 parent.parent
        env_path = SCRIPT_DIR.parent.parent / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k == "OPEN_DART_API_KEY" and v:
                    key = v
                    break
        if not key:
            raise RuntimeError(
                "OPEN_DART_API_KEY 환경변수가 없습니다. 타임리 설정 > 환경변수 확인."
            )
    return key


def _get(url, params, binary=False):
    """GET + 실패 시 5초 후 1회 재시도. 그래도 실패면 예외."""
    params = dict(params, crtfc_key=_api_key())
    full = url + "?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(full, timeout=30) as r:
                data = r.read()
            return data if binary else data.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt == 1:
                print(f"[dart_client] 호출 실패({e}) — 5초 후 1회 재시도", file=sys.stderr)
                time.sleep(5)
    raise RuntimeError(f"OpenDART 호출 실패(재시도 포함 2회): {last_err}")


def _check_json_status(obj, ctx):
    st = obj.get("status")
    if st and st != "000":
        raise RuntimeError(f"{ctx}: DART 오류 status={st} message={obj.get('message')}")


NUM = re.compile(r"-?\d[\d,]*")


def _to_int(s):
    """'4,547,322' -> 4547322. 숫자 없으면 None."""
    if s is None:
        return None
    m = NUM.search(str(s))
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _unit_multiplier(text):
    """섹션/표 머리의 '(단위: 백만원)' 등을 감지해 원 단위 환산 배수를 돌려준다."""
    m = re.search(r"단\s*위\s*[::]?\s*([천백십]?\s*만?\s*억?\s*원|USD|천원|백만원|억원|원)", text)
    if not m:
        return 1
    u = m.group(1).replace(" ", "")
    return {"원": 1, "천원": 1_000, "백만원": 1_000_000, "억원": 100_000_000}.get(u, 1)


# ---------------------------------------------------------------- 1. corpCode
INDEX_PATH = CACHE_DIR / "corp_index.tsv"

def _build_corp_index(zpath):
    # corpCode.zip -> corp_index.tsv (회사명 TAB 고유번호 TAB 종목코드), 스트리밍 파싱
    TAB, NL = chr(9), chr(10)
    tmp = INDEX_PATH.with_name(INDEX_PATH.name + ".tmp" + str(os.getpid()))
    with zipfile.ZipFile(zpath) as z, open(tmp, "w", encoding="utf-8", newline="") as out:
        with z.open(z.namelist()[0]) as f:
            nm = code = stock = ""
            for _ev, el in ET.iterparse(f, events=("end",)):
                tag = el.tag
                if tag == "corp_name":
                    nm = (el.text or "").strip().replace(TAB, " ").replace(NL, " ")
                elif tag == "corp_code":
                    code = (el.text or "").strip()
                elif tag == "stock_code":
                    stock = (el.text or "").strip()
                elif tag == "list":
                    out.write(nm + TAB + code + TAB + stock + NL)
                    el.clear()
                    nm = code = stock = ""
    os.replace(tmp, INDEX_PATH)

def ensure_corp_index():
    # corpCode.zip 과 TSV 인덱스를 확보하고 인덱스 경로를 돌려준다
    zpath = CACHE_DIR / "corpCode.zip"
    if not zpath.exists():
        data = _get(f"{BASE}/corpCode.xml", {}, binary=True)
        if data[:2] != b"PK":
            head = data[:300].decode("utf-8", errors="replace")
            raise RuntimeError(f"corpCode 응답이 ZIP이 아님(키/차단 의심): {head}")
        zpath.write_bytes(data)
    if (not INDEX_PATH.exists()) or INDEX_PATH.stat().st_mtime < zpath.stat().st_mtime:
        _build_corp_index(zpath)
    return INDEX_PATH

def iter_corp_index():
    # (회사명, 고유번호, 종목코드) 를 한 줄씩 돌려준다
    TAB, NL = chr(9), chr(10)
    with open(ensure_corp_index(), encoding="utf-8", newline="") as f:
        for line in f:
            parts = line.rstrip(NL).split(TAB)
            if len(parts) == 3:
                yield parts[0], parts[1], parts[2]

def get_corp_code(company_name):
    """기업명 -> 고유번호. corpCode ZIP은 캐시. 미매칭 시 유사 후보 반환."""
    zpath = CACHE_DIR / "corpCode.zip"
    if not zpath.exists():
        data = _get(f"{BASE}/corpCode.xml", {}, binary=True)
        # 에러면 ZIP이 아니라 XML 텍스트가 온다
        if data[:2] != b"PK":
            head = data[:300].decode("utf-8", errors="replace")
            raise RuntimeError(f"corpCode 응답이 ZIP이 아님(키/차단 의심): {head}")
        zpath.write_bytes(data)
    exact, partial = None, []
    for nm, code, stock in iter_corp_index():
        if nm == company_name:
            # 동명 다수면 상장사(종목코드 보유) 우선
            if exact is None or stock:
                exact = {"name": nm, "corp_code": code, "stock_code": stock}
        elif company_name in nm:
            partial.append({"name": nm, "corp_code": code, "stock_code": stock})
    if exact:
        return {"ok": True, **exact}
    listed_first = sorted(partial, key=lambda x: (x["stock_code"] == "", x["name"]))[:5]
    return {"ok": False, "candidates": listed_first,
            "error": "기업명 미매칭 — 유사 후보에서 정확한 이름으로 재입력 필요"}


# ---------------------------------------------------------------- 2. 재무 (CFS)
def get_financials(corp_code):
    """
    fnlttSinglAcnt: 최신 사업보고서 기준 매출액/영업이익 3개년.
    반드시 CFS(연결) 행만 사용. 없으면 OFS 폴백 + 기준 명시.
    """
    import datetime
    year = datetime.date.today().year
    rows, used_year = None, None
    for y in (year - 1, year - 2):  # 최신 사업보고서 연도 탐색
        txt = _get(f"{BASE}/fnlttSinglAcnt.json",
                   {"corp_code": corp_code, "bsns_year": str(y), "reprt_code": "11011"})
        obj = json.loads(txt)
        if obj.get("status") == "000" and obj.get("list"):
            rows, used_year = obj["list"], y
            break
    if not rows:
        return {"ok": False, "error": "재무제표(사업보고서) 조회 실패 — 비상장/미제출 가능성"}

    def pick(account, fs):
        for r in rows:
            if r.get("fs_div") == fs and account in (r.get("account_nm") or ""):
                return r
        return None

    fs_basis = "연결"
    rev = pick("매출액", "CFS") or pick("영업수익", "CFS")
    op = pick("영업이익", "CFS")
    if rev is None:  # 연결 없음 → 별도 폴백
        fs_basis = "별도(연결 없음)"
        rev = pick("매출액", "OFS") or pick("영업수익", "OFS")
        op = pick("영업이익", "OFS")
    if rev is None:
        return {"ok": False, "error": "매출액 계정을 찾지 못함"}

    def three(r):
        return {
            "당기": _to_int(r.get("thstrm_amount")),
            "전기": _to_int(r.get("frmtrm_amount")),
            "전전기": _to_int(r.get("bfefrmtrm_amount")),
        }

    return {
        "ok": True,
        "fs_basis": fs_basis,
        "bsns_year": used_year,
        "rcept_no": rev.get("rcept_no"),
        "매출액": three(rev),
        "영업이익": three(op) if op else None,
    }


# ---------------------------------------------------------------- 3. 원문 4개 섹션
SECTION_KEYS = {
    "매출구성": ("매출 및 수주", "매출및수주", "매출에 관한 사항"),
    "원재료": ("원재료 및 생산설비", "원재료및생산설비", "주요 원재료"),
    "연구개발": ("연구개발활동", "연구개발 활동", "연구개발비"),
    "위험": ("위험관리", "위험요소", "파생상품 및 위험"),
}


def _period_order(text):
    """
    표의 연도/기수 열이 '당기 먼저'인지 '과거 먼저'인지 판별한다.
    DART 표는 회사마다 순서가 반대여서 첫 값을 당기로 가정하면 급변의 부호가
    뒤집히고 R&D 비중의 증감이 반대로 읽힌다.
    반환: +1 = 당기 먼저(내림차순), -1 = 과거 먼저(오름차순), 0 = 판별 불가
    """
    if not text:
        return 0
    head = text[:2500]
    nums = [int(x) for x in re.findall(r"제\s*(\d{1,3})\s*기", head)]
    if len(nums) < 2:
        nums = [int(x) for x in re.findall(r"(20\d{2})\s*년", head)]
    seq = []
    for n in nums:
        if not seq or n != seq[-1]:
            seq.append(n)
        if len(seq) >= 2:
            break
    if len(seq) < 2:
        return 0
    return 1 if seq[0] > seq[1] else -1


MIN_TABLE_SIGNAL = 3   # 표로 인정할 최소 신호 점수(셀·콤마숫자·% 합산)


def get_document_sections(rcp_no):
    """document.xml(ZIP, 캐시) → 태그 제거 텍스트 → 4개 섹션 추출."""
    zpath = CACHE_DIR / f"document_{rcp_no}.zip"
    if not zpath.exists():
        data = _get(f"{BASE}/document.xml", {"rcept_no": rcp_no}, binary=True)
        if data[:2] != b"PK":
            head = data[:300].decode("utf-8", errors="replace")
            raise RuntimeError(f"document 응답이 ZIP이 아님: {head}")
        zpath.write_bytes(data)
    with zipfile.ZipFile(zpath) as z:
        # 가장 큰 xml이 본문
        name = max(z.namelist(), key=lambda n: z.getinfo(n).file_size)
        raw = z.read(name).decode("utf-8", errors="replace")

    # 표 구조 보존을 위해 셀 경계를 '|' 로, 행 경계를 개행으로 치환 후 태그 제거
    txt = re.sub(r"(?i)</t[dh]\w*>", " | ", raw)
    txt = re.sub(r"(?i)</tr\w*>", "\n", txt)
    txt = re.sub(r"(?i)<br\s*/?>", "\n", txt)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = txt.replace("&nbsp;", " ").replace("&amp;", "&")
    txt = re.sub(r"[ \t]+", " ", txt)

    def _find_body(text, alias):
        # 모든 등장 위치 중 "뒤에 실제 표/본문이 있는" 곳을 고른다.
        # 표 신호 = 셀 구분자(|), 콤마 숫자, % 밀도. 목차(점선+페이지번호)는
        # 신호가 없어 자연 탈락 — 회사별 목차/서식 차이에 강한 방식.
        best, best_score = -1, 0
        for m in re.finditer(re.escape(alias), text):
            w = text[m.end():m.end() + 3000]
            score = (w.count("|")
                     + len(re.findall(r"\d[\d,]{2,}", w))
                     + 2 * w.count("%"))
            if score >= best_score:      # 동점이면 나중 등장(본문 쪽) 우선
                best, best_score = m.start(), score
        # 표 신호가 최소치에 못 미치면 본문이 아니라 목차·언급이다 → 못 찾은 것으로 처리.
        # judge가 "판정 불가 + 원문 확인 안내"로 정직하게 넘긴다.
        if best_score < MIN_TABLE_SIGNAL:
            return -1
        return best

    sections, order = {}, []
    for key, aliases in SECTION_KEYS.items():
        pos = -1
        for a in aliases:
            pos = _find_body(txt, a)
            if pos >= 0:
                break
        order.append((pos, key))
        sections[key] = None
    # 각 섹션 = 제목 위치부터 다음 제목(또는 +15000자)까지
    found = sorted([(p, k) for p, k in order if p >= 0])
    for i, (pos, key) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else pos + 15000
        sections[key] = txt[pos:min(end, pos + 15000)]
    sections["_doc_head"] = txt[:5000]     # 기수(제N기) 추출용. 파싱 대상 섹션이 아니다
    return sections


SALES_TYPE_WORDS = ("수출", "내수", "국내", "해외", "국내판매", "해외판매", "수출매출", "내수매출")
REGION_WORDS = ("국내", "한국", "아시아", "유럽", "미주", "미국", "북미", "중국",
                "일본", "기타", "국외", "해외", "EMEA", "중동")


def _parse_share_table(section_text, name_words, extra_stops=()):
    """
    이름 앵커 기반 파싱 — 줄바꿈에 의존하지 않는다.
    DART 원문 표는 셀이 줄바꿈으로 쪼개지는 경우가 있어("합 계 \n | \n 11,498")
    행 단위 파싱이 실패한다. 대신 텍스트를 평탄화한 뒤 항목명(앵커)에서
    다음 앵커까지 구간의 %·금액을 순서대로 수집한다.
    앵커 뒤에 %가 하나도 없으면 서술문 속 언급으로 보고 제외한다.
    반환: {이름: {"amounts": [...원], "shares": [...%]}}  (순서 = 당기→과거)
    """
    if not section_text:
        return {}
    flat = re.sub(r"\s*\n\s*", " ", section_text)
    mul = _unit_multiplier(section_text[:800]) or 1
    # 구간 종료 전용 앵커(결과에는 포함하지 않음).
    # extra_stops: 다른 표의 항목명. 한 섹션에 부문표·지역표가 붙어 있을 때
    # 앞 표 마지막 항목이 뒤 표의 %까지 빨아들이는 것을 막는다.
    STOP = set(("합계", "합 계", "총계", "총 계")) | set(extra_stops)
    anchors = []
    for w in set(name_words) | set(STOP):
        for m in re.finditer(re.escape(w), flat):
            anchors.append((m.start(), w))
    anchors.sort()
    out = {}
    for i, (pos, w) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else pos + 260
        if w in STOP:
            continue
        seg = flat[pos + len(w):min(end, pos + 260)]
        shares = [float(x) for x in re.findall(r"(\d{1,3}(?:\.\d)?)\s*%", seg)]
        shares = [s for s in shares if 0 < s <= 100][:3]   # 3개년 상한
        if not shares:
            continue
        amounts = []
        for tok in re.findall(r"\d[\d,]{2,}", seg):
            v = _to_int(tok)
            if v and v >= 10:
                amounts.append(v * mul)
        prev = out.get(w)
        if prev is None or len(shares) > len(prev["shares"]):
            out[w] = {"amounts": amounts[:len(shares)], "shares": shares}

    # 비율 열이 없는 항목: 합계 행이 있으면 "항목 금액 ÷ 합계 금액"으로 비중을 도출한다.
    # 같은 섹션 뒤쪽에 다른 %표(판매경로 등)가 있어도 앵커 구간 기준으로 판단한다.
    # 도출값은 derived=True 로 표시해 리포트에서 "합계 대비 계산"임을 밝힌다.
    def _amts(seg):
        vals = []
        for tok in re.findall(r"\d{1,3}(?:,\d{3})+|\d{4,}", seg):
            v = _to_int(tok)
            if v and v >= 10:
                vals.append(v)
        return vals[:3]
    totals = []
    for i, (pos, w) in enumerate(anchors):
        if w in ("합계", "합 계", "총계", "총 계"):
            end = anchors[i + 1][0] if i + 1 < len(anchors) else pos + 260
            totals = _amts(flat[pos + len(w):min(end, pos + 260)])
            if totals:
                break
    if totals:
        for i, (pos, w) in enumerate(anchors):
            if w in STOP or w in out:
                continue
            end = anchors[i + 1][0] if i + 1 < len(anchors) else pos + 260
            seg = flat[pos + len(w):end]
            if "%" in seg[:260]:
                continue
            m_sub = re.search(r"소\s*계", seg)
            if m_sub:                                   # 하위 행(승용/RV/상용…)이 있으면 소계가 부문 금액
                amts = _amts(seg[m_sub.end():m_sub.end() + 200])
            else:
                amts = _amts(seg[:260])
            shares = [round(a / tot * 100, 1) for a, tot in zip(amts, totals) if tot]
            shares = [s for s in shares if 0 < s <= 100]
            if shares:
                out[w] = {"amounts": [a * mul for a in amts[:len(shares)]],
                          "shares": shares, "derived": True}
    # 표가 과거→당기 순이면 뒤집어 항상 당기가 [0]이 되도록 통일한다
    if _period_order(section_text) < 0:
        for v in out.values():
            v["shares"] = list(reversed(v["shares"]))
            v["amounts"] = list(reversed(v["amounts"]))
    return out


HEADER_WORDS = ("구분", "구 분", "품목", "매출액", "매출", "매출유형", "비율", "비중",
                "사업부문", "부문", "지역", "금액", "단위", "계", "합계", "합 계",
                "총계", "총 계", "소계", "소 계", "당기", "전기", "전전기",
                "주요제품", "주요 제품", "회사명", "구성비")

GENERIC_SEG = {"제품", "상품", "서비스", "용역", "기타"}
# 매출유형 열에 오는 말. 첫 셀이 이것이면 항목명은 그 다음 셀(품목)이다.
# '기타'는 여기 넣지 않는다 — 기타는 행 자체의 이름(잔여 항목)이라 뒤 셀로 넘어가면 안 된다.
TYPE_WORDS = {"제품", "상품", "서비스", "용역", "제품매출", "상품매출", "용역매출", "기타매출",
              "제", "상", "제·상품", "제ㆍ상품", "제상품", "임대", "수수료", "기타수익"}


def _cells(section_text):
    """섹션 텍스트를 셀 목록으로. DART 원문은 셀마다 줄바꿈이 들어와 '한 줄 = 한 행'이 아니다."""
    if not section_text:
        return []
    return [c.strip(" .·\t\n") for c in section_text.split("|")]


_NUMCELL = re.compile(r"^\(?-?\d{1,3}(?:,\d{3})+\)?$|^\(?-?\d{4,}\)?$|^\d{1,3}(?:\.\d+)?\s*%$|^-$")


def _is_num(c):
    return bool(c) and bool(_NUMCELL.match(c))


def _is_type_cell(c):
    parts = [x.strip() for x in re.split(r"[,、/·ㆍ･\s]+", c) if x.strip()]
    return bool(parts) and all(x in TYPE_WORDS for x in parts)


def _is_noise_label(c):
    """머리글·연도·기수·합계 셀 — 항목명도 아니고 행의 라벨 수에도 세지 않는다."""
    c_norm = re.sub(r"\s+", "", c)
    return (c_norm in HEADER_WORDS or re.fullmatch(r"(합|총|소)계", c_norm)
            or bool(re.fullmatch(r"제\s*\d+\s*기\s*(말|초|반기|분기)?", c)) or bool(re.search(r"^\d{4}\s*년", c))
            or bool(re.fullmatch(r"(당|전|전전)기\s*(말|초)?", c_norm))
            or not re.search(r"[가-힣A-Za-z]", c) or bool(re.search(r"\d{3,}", c))
            or c.startswith("(단위") or bool(re.match(r"^\d+\.\s", c)) or bool(re.match(r"^[가-하]\.\s", c)))


def _auto_anchors(section_text, exclude=()):
    """
    매출구성 표에서 항목명 후보를 자동으로 뽑는다 — 행이 아니라 셀 흐름으로.
    숫자 셀이 2개 이상 연달아 나오면 그 앞의 라벨 셀들 중 가장 왼쪽(부문 열)을 항목명으로 본다.
    매출유형 셀(제품/상품…)·머리글·지역어·판매유형·기간 라벨은 건너뛴다.
    부문 셀이 rowspan 으로 병합되면 그 아래 차종·품목 행은 라벨 수가 줄어든다(현대차: 차량|제품|승용
    다음 RV, 상용). 라벨 수가 가장 많은 행만 부문 행으로 보고 나머지는 하위 행으로 본다.
    실제 사고: 셀마다 줄바꿈이 들어와 행 단위 추출이 모든 실기업에서 실패했다(아모레·삼성전자·농심).
    """
    cells = _cells(section_text)
    runs = []
    i, n = 0, len(cells)
    while i < n:
        if _is_num(cells[i]):
            j = i
            while j < n and _is_num(cells[j]):
                j += 1
            if j - i >= 2:                      # 숫자 셀 2개 이상 = 데이터 행
                labels = []
                k = i - 1
                while k >= 0 and len(labels) < 5 and not _is_num(cells[k]):
                    if cells[k]:
                        if _is_noise_label(cells[k]):
                            break               # 머리글·단위·절 제목에 닿으면 행의 시작이다
                        labels.append(cells[k])
                    k -= 1
                labels.reverse()                # 왼쪽 열부터
                name, skip_row = None, False
                for c in labels:
                    c_norm = re.sub(r"\s+", "", c)
                    if _is_type_cell(c):        # 매출유형 열 → 다음 셀(품목)로
                        continue
                    if c_norm in SALES_TYPE_WORDS or any(re.sub(r"\s+", "", w) == c_norm for w in exclude):
                        skip_row = True         # 판매유형·지역·기타 행 → 부문 앵커 아님
                        break
                    if c_norm.startswith("~") \
                            or re.fullmatch(r"\d+~\d*(년|개월)|\d+(년|개월)?(이내|이상|초과|미만)", c_norm):
                        skip_row = True         # 수주잔고·만기 표의 기간 라벨
                        break
                    name = c
                    break
                if name and not skip_row and 1 <= len(name) <= 30:
                    runs.append((name, len(labels)))
            i = j
        else:
            i += 1
    if not runs:
        return ()
    full = max(k for _, k in runs)
    cand = []
    for name, k in runs:
        if k >= full and name not in cand:
            cand.append(name)
    return tuple(cand[:12])


def _parse_horizontal_pct(section_text, name_words):
    """
    가로형 비중 표: 라벨 행 다음에 '비중 | 44% | 9% | 47%' 행이 오는 형태(아모레 판매경로 등).
    '비중/비율/구성비' 셀 뒤의 % 셀 개수만큼 앞의 라벨을 짝지어 {지역어: 비중} 으로 돌려준다.
    """
    cells = [c for c in _cells(section_text) if c]
    out = {}
    for i, c in enumerate(cells):
        if not re.fullmatch(r"비\s*중|비\s*율|구성비", c):
            continue
        pcts = []
        j = i + 1
        while j < len(cells) and re.fullmatch(r"\d{1,3}(?:\.\d+)?\s*%", cells[j]):
            pcts.append(float(cells[j].rstrip("% ")))
            j += 1
        if len(pcts) < 2:
            continue
        labels = [x for x in cells[max(0, i - len(pcts) - 2):i]
                  if not re.fullmatch(r"경\s*로|구\s*분|지\s*역|비\s*중|비\s*율", x)][-len(pcts):]
        if len(labels) != len(pcts):
            continue
        for lab, pc in zip(labels, pcts):
            for w in name_words:
                if w in lab and w not in out:
                    out[w] = {"amounts": [], "shares": [pc]}
                    break
        if out:
            break
    return out


UNIT_WORDS = {"ton", "tons", "kg", "mt", "ea", "box", "unit", "units", "usd", "krw", "eur",
              "mm", "set", "sets", "pcs", "lot", "won", "per", "year", "month"}
SUPPLIER_HEADERS = re.compile(r"^(매\s*입\s*처|주요\s*매입처|공\s*급\s*처|구\s*매\s*처|거\s*래\s*처)$")

# 매입처 칸에 들어오지만 회사명이 아닌 값들
NOT_A_COMPANY = re.compile(
    r"^(자체|자가|생산|내부|해당\s*없음|미해당|기타|국내|해외|수입|국외|-+|—+|"
    r"억\s*원|백만\s*원|천\s*원|원|시간|톤|개|대|건|명|년|월|일|kg|㎏|㎡|평|매입액|비율|비중|단위)$")


def _head(text, n=1500):
    """진단·판정용 머리글. 표에서 나온 개행·공백 덩어리를 접어야 같은 글자 수에 본문이 더 담긴다."""
    if not text:
        return ""
    return re.sub(r"\s*\n\s*", "\n", re.sub(r"[ \t]+", " ", text)).strip()[:n]


_CORP = re.compile(r"㈜|\(주\)|주식회사|\bInc\b|\bLtd|\bCo\.|\bCorp|\bLLC|GmbH|\bAG\b|KGaA|SDN|BHD|\bS\.A\b|\bPte\b|\bLimited\b|\bCompany\b")
_LATIN_LIST = re.compile(r"^[A-Z][A-Za-z0-9&.\- ]{1,30}(?:\s*,\s*[A-Z][A-Za-z0-9&.\- ]{1,30})*\s*(?:등|외)?$")


def _split_names(cell):
    out = []
    cell = re.sub(r"\s+", " ", cell)
    for name in re.split(r"[,、·/]|\s+및\s+", re.sub(r"\s*(등|외)\s*$", "", cell)):
        name = re.sub(r"㈜|\(주\)|주식회사|\(유\)|\(재\)", "", name).strip(" .·\t()")
        if not (2 <= len(name) <= 30) or not re.search(r"[가-힣A-Za-z]", name):
            continue
        if re.search(r"\d{3,}|%", name) or NOT_A_COMPANY.match(name):
            continue
        if len(re.sub(r"[^A-Za-z가-힣]", "", name)) < 2 or name.lower() in UNIT_WORDS:
            continue
        out.append(name)
    return out


def _parse_suppliers(raw_text):
    """
    원재료 섹션에서 매입처를 뽑는다 — 셀 흐름 기반 (DART 원문은 셀마다 줄바꿈이 들어온다).

    모드 A) 머리글에 '매입처/공급처/거래처' 또는 '비고' 열이 있고 그 열이 숫자 열(매입액·비율)
            뒤에 오면: 각 행의 숫자 연속 구간 바로 다음 셀이 매입처다 (아모레형).
    모드 B) 숫자 열이 없으면: 법인격 표기(㈜·(주)·Inc…)가 있거나 "Qualcomm, MediaTek 등"처럼
            대문자 영문 사명 목록 형태인 셀만 매입처로 본다 (삼성전자형).
    어느 쪽도 아니면 빈 목록 — 원재료명(GLYCERINE 등)을 회사로 오인하지 않는다.
    """
    if not raw_text:
        return []
    cells = _cells(raw_text)
    # 머리글: 첫 번째로 '매입/품목' 류 셀이 연속 등장하는 구간
    hdr_i = None
    for i, c in enumerate(cells):
        if re.fullmatch(r"매입\s*유형|품\s*목|사업\s*부문|구\s*분", c):
            hdr_i = i
            break
    header = []
    if hdr_i is not None:
        j = hdr_i
        while j < len(cells) and cells[j] and not _is_num(cells[j]) and len(header) < 10:
            header.append(cells[j])
            j += 1
    def _idx(pat):
        for k, h in enumerate(header):
            if re.fullmatch(pat, h):
                return k
        return None
    sup_col = _idx(r"매\s*입\s*처|주요\s*매입처|공\s*급\s*처|거\s*래\s*처")
    note_col = _idx(r"비\s*고")
    num_cols = [k for k, h in enumerate(header) if re.fullmatch(r"매입액|비\s*율|비\s*중|금\s*액", h)]

    hits = []
    target = sup_col if sup_col is not None else note_col
    if target is not None and num_cols:
        # 모드 A: 행마다 숫자 연속 구간(매입액·비율)을 기준점으로, 매입처 열의 상대 위치로 셀을 집는다
        before = target < min(num_cols)
        off = (min(num_cols) - target) if before else (target - max(num_cols) - 1)
        i, n = 0, len(cells)
        while i < n:
            if _is_num(cells[i]):
                j = i
                while j < n and _is_num(cells[j]):
                    j += 1
                k = (i - off) if before else (j + off)
                if 0 <= k < n:
                    v = cells[k]
                    if v and not _is_num(v) and not re.fullmatch(r"-+|—+", v) \
                            and not re.fullmatch(r"(소\s*계|합\s*계|총\s*계).*", v) \
                            and not (target == note_col and not (_CORP.search(v) or "," in v or v.endswith("등"))):
                        hits += _split_names(v)
                i = j
            else:
                i += 1
    else:
        # 모드 B: 내용으로 판별
        for c in cells:
            if not c or _is_num(c) or len(c) > 120:
                continue
            if _CORP.search(c) or (_LATIN_LIST.match(c) and ("," in c or re.search(r"(등|외)$", c))):
                if not re.search(r"^(단위|주\)|※)", c):
                    hits += _split_names(c)

    block = re.compile(r"CDMO|CMO|BOM|Bill|Material|Handling|Fee|"
                       r"구\s*분|품목|비율|비중|금액|단위|합\s*계|총\s*계|가격|변동|용도|"
                       r"주요|사업|제품|상품|원재료|매입|기준|참조", re.I)
    dedup = []
    for h in hits:
        if h and h not in dedup and not block.search(h):
            dedup.append(h)
    return dedup[:8]


RND_ANCHORS = ("매출액 대비", "매출액대비", "연구개발비용 계", "연구개발비 계",
               "연구개발비용", "연구개발비", "비율")


def _parse_rnd(section_text):
    """
    연구개발 섹션에서 매출액 대비 연구개발비 비중(%)을 연도별로 추출한다.
    - 정수 퍼센트("7%")도 인식한다. 이전 정규식은 소수점이 있어야만 잡았다.
    - 섹션 전체를 훑지 않고 연구개발 관련 앵커 주변 구간만 본다.
      섹션 안에는 지분율·진행률 같은 무관한 %가 섞여 있기 때문이다.
    - 앵커를 못 찾으면 섹션 전체 폴백(정수 포함).
    """
    if not section_text:
        return {"ratios": [], "amounts": []}
    flat = re.sub(r"\s*\n\s*", " ", section_text)
    pct = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*%")

    ratios, seen = [], set()
    hits = []
    for a in RND_ANCHORS:
        for m in re.finditer(re.escape(a), flat):
            hits.append(m.start())
    for pos in sorted(set(hits)):
        for x in pct.findall(flat[pos:pos + 400]):
            v = float(x)
            if 0 < v <= 30 and v not in seen:   # R&D 비중 상식 범위
                seen.add(v)
                ratios.append(v)
    if not ratios:  # 앵커 실패 시 폴백
        for x in pct.findall(flat):
            v = float(x)
            if 0 < v <= 30 and v not in seen:
                seen.add(v)
                ratios.append(v)
    ratios = ratios[:6]
    if _period_order(section_text) < 0:      # 과거→당기 순이면 뒤집는다
        ratios = list(reversed(ratios))
    mul = _unit_multiplier(section_text[:800]) or 1
    amounts = []
    for line in section_text.splitlines():
        if "연구개발비" in line or "연구개발비용" in line:
            for c in line.split("|"):
                v = _to_int(c)
                if v and v > 1000:
                    amounts.append(v * mul)
    return {"ratios": ratios, "amounts": amounts[:6]}


# ---------------------------------------------------------------- 조립
def fetch(company_name):
    out = {"input": company_name}
    corp = get_corp_code(company_name)
    out["corp"] = corp
    if not corp.get("ok"):
        return out

    fin = get_financials(corp["corp_code"])
    out["financials"] = fin
    if not fin.get("ok"):
        return out

    rcp = fin["rcept_no"]
    out["rcp_no"] = rcp
    out["dart_url"] = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp}"
    try:
        sec = get_document_sections(rcp)
    except Exception as e:  # noqa: BLE001
        out["sections"] = {"ok": False, "error": str(e)}
        return out

    seg_anchors = _auto_anchors(sec.get("매출구성"), exclude=set(REGION_WORDS))
    regions = _parse_share_table(sec.get("매출구성"), REGION_WORDS,
                                 extra_stops=seg_anchors)
    if len(regions) < 2:                       # 세로형이 부실하면 가로형(라벨 행 + 비중 행) 시도
        horiz = _parse_horizontal_pct(sec.get("매출구성"), REGION_WORDS)
        if len(horiz) >= 2:
            regions = horiz
    # 부문(사업부문) 파싱: 매출구성 섹션에서 지역어가 아닌 굵은 항목
    segs = (_parse_share_table(sec.get("매출구성"), seg_anchors,
                               extra_stops=REGION_WORDS) if seg_anchors else {})
    # 일반어(제품/서비스/기타)만 잡혔다면 실명 부문이 아니므로 폐기 →
    # judge에서 "판정 불가 + 원문 확인"으로 정직하게 처리된다
    if segs and set(segs) <= GENERIC_SEG:
        segs = {}
    # 보고서 기수: 문서 앞부분의 "제 61 기" 표기. 없으면 None — 리포트는 이 값이 있을 때만 쓴다
    m = re.search(r"제\s*(\d{1,3})\s*기", sec.get("_doc_head") or "")
    out["period_no"] = int(m.group(1)) if m else None
    out["sections"] = {
        "ok": True,
        "parse": {
            # raw_head 는 파싱 실패 시 표 구조를 눈으로 확인하기 위한 진단용이다
            # raw_head 는 성공·실패와 무관하게 항상 담는다 — "성공"이 오독일 때 진단할 유일한 단서
            "지역별": {"ok": bool(regions), "data": regions, "raw_head": _head(sec.get("매출구성"), 2500)},
            "부문별": {"ok": bool(segs), "data": segs, "raw_head": _head(sec.get("매출구성"), 2500)},
            "원재료": {"ok": bool(sec.get("원재료")),
                       "suppliers": _parse_suppliers(sec.get("원재료")),
                       "raw_head": _head(sec.get("원재료"))},
            "연구개발": {"ok": bool(sec.get("연구개발")), **_parse_rnd(sec.get("연구개발")),
                         "raw_head": _head(sec.get("연구개발"), 800)},
            "위험": {"ok": bool(sec.get("위험")),
                     "raw_head": _head(sec.get("위험"), 500)},
        },
    }
    return out


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "삼성바이오로직스"
    result = fetch(name)
    print(json.dumps(result, ensure_ascii=False, indent=2))
