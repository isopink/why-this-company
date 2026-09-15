import json, sys, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dart_client

def ensure_cache():
    zpath = dart_client.CACHE_DIR / "corpCode.zip"
    if not zpath.exists():
        dart_client.get_corp_code("삼성전자")
    return zpath

def main():
    q = sys.argv[1] if len(sys.argv) > 1 else ""
    q = q.strip()
    if len(q) < 2:
        print(json.dumps({"ok": True, "items": []}, ensure_ascii=False))
        return
    try:
        zpath = ensure_cache()
        with zipfile.ZipFile(zpath) as z:
            xml = z.read(z.namelist()[0]).decode("utf-8", errors="replace")
        root = ET.fromstring(xml)
        listed, unlisted = [], []
        for el in root.iter("list"):
            nm = (el.findtext("corp_name") or "").strip()
            if q not in nm:
                continue
            code = (el.findtext("corp_code") or "").strip()
            stock = (el.findtext("stock_code") or "").strip()
            row = {"name": nm, "corp_code": code, "stock_code": stock}
            if stock:
                listed.append(row)
            else:
                unlisted.append(row)
        listed.sort(key=lambda x: (len(x["name"]), x["name"]))
        unlisted.sort(key=lambda x: (len(x["name"]), x["name"]))
        items = listed
        print(json.dumps({"ok": True, "items": items[:8]}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ok": False, "items": [], "error": str(e)}, ensure_ascii=False))

main()
