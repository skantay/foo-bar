"""Выгрузка results.json в формате организаторов (Appendix Results, JSON 1.0).

    python3 scripts/export_results.py DEV              → results/dev/results.json
    python3 scripts/export_results.py <group_id> prod  → results/prod/results.json

Берёт данные у запущенного сервиса (CQA_API, по умолчанию http://127.0.0.1:8000).
Пишет файл, даже если есть проблемы, и печатает их: диалоги с ошибкой попадают как status=error.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API = os.getenv("CQA_API", "http://127.0.0.1:8000")
ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    dataset = sys.argv[1]
    folder = sys.argv[2] if len(sys.argv) > 2 else ("dev" if dataset.upper() == "DEV" else "prod")
    if folder not in ("dev", "prod"):
        print("папка: dev или prod")
        return 2
    url = f"{API}/api/results?dataset={urllib.parse.quote(dataset)}"
    with urllib.request.urlopen(url, timeout=60) as r:
        resp = json.load(r)
    doc = resp["document"]
    out = ROOT / "results" / folder / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", "utf-8")
    ok = sum(1 for x in doc["results"] if x["status"] == "ok")
    print(f"{out.relative_to(ROOT)}: записей {len(doc['results'])}, ok {ok}, error {len(doc['results']) - ok}, "
          f"чек-лист v{resp['checklist_version']}, модель {doc['model']['name']}")
    if folder == "prod" and len(doc["results"]) != 10:
        print(f"ВНИМАНИЕ: в prod должно быть ровно 10 записей, сейчас {len(doc['results'])}")
    for p in resp["problems"]:
        print("  !", p)
    return 1 if resp["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
