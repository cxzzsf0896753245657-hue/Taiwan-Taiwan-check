"""Fix known typos in site/data/entries.json and write a change log.

Every change is recorded (entry id, field path, before, after) in
site/data/history.json so the original wording is never lost.
Usage: python3 tools/fix_typos.py
"""
import json
import datetime
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "data"

# wrong -> right (character slips introduced when the data was first entered)
FIXES = [
    ("刢", "判"), ("刉", "判"), ("刡", "判"), ("刔", "判"),
    ("拉攝", "拉攏"), ("徐慧論", "徐慧諭"), ("珍瑶", "珍瑤"), ("思瑶", "思瑤"),
    ("于2天內", "於2天內"), ("辨方", "辯方"), ("反驳", "反駁"),
    ("震稱", "號稱"), ("容積獲獎", "容積獎勵"),
]


def fix(s):
    for a, b in FIXES:
        s = s.replace(a, b)
    return s


def walk(obj, path, changes):
    if isinstance(obj, str):
        new = fix(obj)
        if new != obj:
            changes.append({"field": path, "from": obj, "to": new})
        return new
    if isinstance(obj, list):
        return [walk(v, f"{path}[{i}]", changes) for i, v in enumerate(obj)]
    if isinstance(obj, dict):
        return {k: walk(v, f"{path}.{k}" if path else k, changes) for k, v in obj.items()}
    return obj


def main():
    entries = json.loads((DATA / "entries.json").read_text(encoding="utf-8"))
    hist_path = DATA / "history.json"
    history = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else []
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for e in entries:
        changes = []
        fixed = walk(e, "", changes)
        changes = [c for c in changes if c["field"] != "id"]
        if changes:
            fixed["updated"] = now
            history.append({"entryId": e["id"], "name": fixed["name"], "at": now,
                            "reason": "錯字修正（逐字記錄修改前後）", "by": "Claude 建檔",
                            "changes": changes})
        out.append(fixed)
    (DATA / "entries.json").write_text(
        "[\n" + ",\n".join(json.dumps(e, ensure_ascii=False) for e in out) + "\n]\n", encoding="utf-8")
    hist_path.write_text(json.dumps(history, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    n = sum(1 for h in history if h["at"] == now)
    print(f"fixed {n} entries")


if __name__ == "__main__":
    main()
