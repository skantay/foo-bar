"""Независимая проверка results.json по Appendix Results (JSON 1.0); формулу итога считает сам.

    python3 scripts/validate_results.py results/dev/results.json [data/dev.json]

Второй аргумент (ответ API организаторов) необязателен: с ним сверяются ID и номера реплик.
"""
import json, sys
from decimal import Decimal, ROUND_HALF_UP
SPEC = {"contact": ({True, False}, 10, 1), "discovery": ({True, False, None}, 15, 1),
        "identification": ({True, False, None}, 20, 1), "consultation": ({0, 5, 10, 15, 20, None}, 20, 20),
        "empathy": ({True, False}, 15, 1), "dialogue": ({0, 4, 7, 10}, 15, 10),
        "closure": ({True, False, None}, 5, 1), "critical": ({True, False}, 0, 1)}
def score(crit):
    num = den = 0
    for c, v in crit.items():
        dom, w, mx = SPEC[c]
        if w == 0 or v is None: continue
        r = (1 if v else 0) if isinstance(v, bool) else v / mx
        num += w * r; den += w
    if not den: return None
    return float(Decimal(str(num / den * 100)).quantize(Decimal("0.01"), ROUND_HALF_UP))
res = json.load(open(sys.argv[1], encoding="utf-8"))
src = json.load(open(sys.argv[2], encoding="utf-8")) if len(sys.argv) > 2 else None
P = []
for k in ("schema_version", "team_name", "model", "results"):
    if k not in res: P.append(f"нет поля {k}")
if res.get("schema_version") != "1.0": P.append("schema_version != 1.0")
if res["model"].get("mode") not in ("local", "external_api", "hybrid"): P.append(f"model.mode {res['model']}")
ids = [r["conversation_id"] for r in res["results"]]
if len(ids) != len(set(ids)): P.append("дубли conversation_id")
if "prod" in sys.argv[1] and len(ids) != 10: P.append(f"в prod должно быть ровно 10 записей, сейчас {len(ids)}")
lens = {}
if src:
    recs = src["data"]["records"] if "data" in src else src["records"]
    lens = {r["conversation_id"]: len(r["transcript"]) for r in recs}
    if set(lens) != set(ids): P.append(f"ID не совпадают: нет {set(lens)-set(ids)}, лишние {set(ids)-set(lens)}")
for r in res["results"]:
    cid = r["conversation_id"]
    if r["status"] == "error":
        if r["criteria"] != [] or r["score"] is not None or not r.get("error"): P.append(f"{cid}: error-запись не по формату")
        continue
    if r["status"] != "ok": P.append(f"{cid}: status {r['status']}"); continue
    codes = [c["criterion_id"] for c in r["criteria"]]
    if sorted(codes) != sorted(SPEC): P.append(f"{cid}: коды {codes}"); continue
    crit = {}
    for c in r["criteria"]:
        v = c["value"]; dom = SPEC[c["criterion_id"]][0]
        okv = (v is None and None in dom) or (isinstance(v, bool) and v in dom and True in dom) or \
              (not isinstance(v, bool) and isinstance(v, (int, float)) and v in dom and True not in dom)
        if not okv: P.append(f"{cid}.{c['criterion_id']}: недопустимое value {v!r}")
        if not isinstance(c.get("rationale"), str) or not c["rationale"].strip(): P.append(f"{cid}.{c['criterion_id']}: пустой rationale")
        et = c.get("evidence_turns")
        if not isinstance(et, list) or any(not isinstance(t, int) or t < 1 or (lens and t > lens[cid]) for t in et):
            P.append(f"{cid}.{c['criterion_id']}: evidence_turns вне 1..{lens.get(cid)}: {et}")
        crit[c["criterion_id"]] = v
    exp = score(crit)
    fs = r["score"]["final_score"]
    if exp != fs: P.append(f"{cid}: final_score {fs}, по формуле {exp}")
    if r["score"]["critical_flag"] is not crit["critical"]: P.append(f"{cid}: critical_flag != critical")
print(f"записей {len(ids)}; проблем {len(P)}")
for p in P: print(" !", p)
