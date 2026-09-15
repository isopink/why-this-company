import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dart_client


def main():
    q = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if len(q) < 2:
        print(json.dumps({"ok": True, "items": []}, ensure_ascii=False))
        return
    try:
        listed = []
        for nm, code, stock in dart_client.iter_corp_index():
            if stock and q in nm:
                listed.append({"name": nm, "corp_code": code, "stock_code": stock})
        listed.sort(key=lambda x: (len(x["name"]), x["name"]))
        print(json.dumps({"ok": True, "items": listed[:8]}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ok": False, "items": [], "error": str(e)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
