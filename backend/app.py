"""CQA — Conversation Quality Analytics: API + статика интерфейса."""
import asyncio
import json
import logging
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> None:
    """Минимальный загрузчик .env: KEY=VALUE, без перезаписи уже заданных переменных."""
    if path.exists():
        for line in path.read_text("utf-8").splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and k and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# .env читается до импорта модулей: evaluator берёт CQA_MODEL, CQA_SAMPLES и др. при импорте
load_env(ROOT / ".env")

import db  # noqa: E402
import evaluator  # noqa: E402
import scoring  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cqa")

FRONTEND = ROOT / "frontend"
CONCURRENCY = int(os.getenv("CQA_CONCURRENCY", "8"))
# Адрес API организаторов с подписью — секрет, хранится только в .env (см. .env.example)
SOURCE_URL = os.getenv("CQA_SOURCE_URL", "")

app = FastAPI(title="CQA API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------- данные ----------

def normalize_record(rec: dict, dataset: str, source: str) -> dict:
    cid = rec.get("conversation_id") or rec.get("id")
    transcript = rec.get("transcript")
    if not cid or not isinstance(transcript, list):
        raise ValueError("ожидаются поля conversation_id и transcript[]")
    channel = rec.get("channel") or ("voice call" if rec.get("audio_url") else "agent chat")
    return {
        "id": str(cid),
        "dataset": dataset,
        "source": source,
        "channel": channel,
        "language": (rec.get("language") or "").upper() or "RU",
        "audio_url": rec.get("audio_url") or None,
        "transcript": [{"timestamp": t.get("timestamp", ""), "speaker": (t.get("speaker") or "").lower(),
                        "text": t.get("text", "")} for t in transcript],
    }


def import_payload(payload, dataset: str, source: str) -> list[str]:
    """Принимает ответ API организаторов, массив записей или одну запись."""
    if isinstance(payload, dict) and "data" in payload:
        if payload.get("isSuccess") is False:
            raise HTTPException(502, f"API организаторов вернул ошибку: {payload.get('error')}")
        dataset = payload["data"].get("dataset_group_id") or dataset
        records = payload["data"].get("records") or []
    elif isinstance(payload, dict) and "records" in payload:
        records = payload["records"]
    elif isinstance(payload, list):
        records = payload
    else:
        records = [payload]
    ids = []
    for rec in records:
        try:
            d = normalize_record(rec, dataset, source)
        except ValueError as e:
            raise HTTPException(400, f"Некорректная запись: {e}")
        old = db.get("dialogs", d["id"])
        if old:
            d["created_at"] = old["created_at"]
        db.put("dialogs", d)
        ids.append(d["id"])
    return ids


def source_url(group_id: str) -> str:
    parts = urlsplit(SOURCE_URL)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k != "group_id"] + [("group_id", group_id)]
    return urlunsplit(parts._replace(query=urlencode(q)))


@app.post("/api/dialogs/sync")
async def sync_dialogs(body: dict = Body(...)):
    group_id = (body.get("group_id") or "DEV").strip()
    if not SOURCE_URL:
        raise HTTPException(400, "Не задан CQA_SOURCE_URL — укажите ссылку на API организаторов в .env")
    async with httpx.AsyncClient(timeout=90) as http:
        r = await http.get(source_url(group_id))
    if r.status_code != 200:
        raise HTTPException(502, f"API организаторов: HTTP {r.status_code}")
    ids = import_payload(r.json(), group_id, "api")
    return {"imported": len(ids), "dataset": group_id, "ids": ids}


@app.post("/api/dialogs/upload")
async def upload_dialogs(file: UploadFile = File(...), dataset: str = "UPLOAD"):
    raw = await file.read()
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = parse_plain_transcript(raw.decode("utf-8", errors="replace"), Path(file.filename or "dialog").stem)
    ids = import_payload(payload, dataset, "upload")
    return {"imported": len(ids), "dataset": dataset, "ids": ids}


def parse_plain_transcript(text: str, cid: str) -> dict:
    """Текстовый транскрипт: строки вида `[00:12] operator: текст` или `client: текст`."""
    turns = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        ts = ""
        if line.startswith("[") and "]" in line:
            ts, line = line[1:line.index("]")], line[line.index("]") + 1:].strip()
        sp, _, msg = line.partition(":")
        sp = sp.strip().lower()
        alias = {"оператор": "operator", "клиент": "client", "бот": "bot", "система": "system", "agent": "operator"}
        sp = alias.get(sp, sp)
        if sp not in ("operator", "client", "bot", "system"):
            sp, msg = "client", line
        turns.append({"timestamp": ts, "speaker": sp, "text": msg.strip()})
    return {"conversation_id": cid, "channel": "agent chat", "language": "RU", "transcript": turns}


def latest_evaluations() -> dict[tuple[str, str], dict]:
    out = {}
    for e in db.all("evaluations"):
        out[(e["dialog_id"], e["checklist_id"])] = e  # отсортированы по created_at
    return out


_cl_cache: dict[str, dict] = {}


def criteria_marks(e: dict) -> list[dict]:
    """Короткая сводка по критериям для списка: цвет ответа на каждый критерий."""
    cl = _cl_cache.get(e["checklist_id"]) or db.get("checklists", e["checklist_id"])
    if not cl or e.get("status") != "done":
        return []
    _cl_cache[cl["id"]] = cl
    fa = final_answers(e)
    out = []
    for c in cl["criteria"]:
        a = fa.get(c["id"]) or {}
        lo, hi = scoring.value_range(c)
        if a.get("na"):
            m = "na"
        elif a.get("value") is None:
            m = "none"
        elif c.get("type") == "flag":
            m = "crit" if a["value"] else "ok"
        elif a.get("manual_ratio") is not None:
            r = a["manual_ratio"]
            m = "yes" if r >= 1 else "no" if r <= 0 else "part"
        else:
            m = "yes" if a["value"] >= hi else "no" if a["value"] <= lo else "part"
        out.append({"id": c["id"], "name": c["name"], "m": m, "label": answer_label(c, a)})
    return out


def eval_brief(e: dict | None) -> dict | None:
    if not e:
        return None
    fr = e.get("final_result") or {}
    return {"id": e["id"], "status": e["status"], "case_status": e.get("case_status"),
            "total": fr.get("total"), "ai_total": (e.get("ai_result") or {}).get("total"),
            "critical_errors": fr.get("critical_errors", []), "passed": fr.get("passed"),
            "open_appeals": e.get("open_appeals", 0), "error": e.get("error"), "marks": criteria_marks(e),
            "checklist_id": e["checklist_id"], "created_at": e["created_at"]}


@app.get("/api/dialogs")
def list_dialogs(checklist_id: str | None = None, dataset: str | None = None):
    checklist_id = checklist_id or active_checklist()["id"]
    latest = latest_evaluations()
    out = []
    for d in db.all("dialogs"):
        if dataset and d["dataset"] != dataset:
            continue
        turns = d["transcript"]
        out.append({
            "id": d["id"], "dataset": d["dataset"], "channel": d["channel"], "language": d["language"],
            "audio_url": d.get("audio_url"), "turns": len(turns),
            "operator_turns": sum(1 for t in turns if t["speaker"] == "operator"),
            "preview": next((t["text"] for t in turns if t["speaker"] == "client"), "")[:140],
            "evaluation": eval_brief(latest.get((d["id"], checklist_id))),
        })
    return out


@app.get("/api/dialogs/{dialog_id}")
def get_dialog(dialog_id: str):
    d = db.get("dialogs", dialog_id)
    if not d:
        raise HTTPException(404, "Диалог не найден")
    evals = [e for e in db.all("evaluations") if e["dialog_id"] == dialog_id]
    d["evaluations"] = [eval_brief(e) for e in reversed(evals)]
    return d


@app.delete("/api/dialogs/{dialog_id}")
def delete_dialog(dialog_id: str):
    db.delete("dialogs", dialog_id)
    return {"ok": True}


@app.get("/api/datasets")
def datasets():
    counts = {}
    for d in db.all("dialogs"):
        counts[d["dataset"]] = counts.get(d["dataset"], 0) + 1
    return [{"id": k, "count": v} for k, v in counts.items()]


# ---------- чек-листы ----------

def validate_checklist(c: dict) -> None:
    if not c.get("name"):
        raise HTTPException(400, "Укажите название чек-листа")
    crits = c.get("criteria") or []
    if not crits:
        raise HTTPException(400, "Добавьте хотя бы один критерий")
    seen = set()
    for x in crits:
        if not x.get("id") or x["id"] in seen:
            raise HTTPException(400, f"Повторяющийся или пустой id критерия: {x.get('id')!r}")
        seen.add(x["id"])
        if not x.get("name"):
            raise HTTPException(400, f"Критерий {x['id']}: нет названия")
        if x.get("type") not in ("binary", "scale", "flag"):
            raise HTTPException(400, f"Критерий {x['id']}: тип binary, scale или flag")
        if x["type"] == "flag":
            x["weight"] = 0
            x["na_allowed"] = False
        elif float(x.get("weight") or 0) <= 0:
            raise HTTPException(400, f"Критерий {x['id']}: вес должен быть > 0")
        if x["type"] == "scale":
            s = x.setdefault("scale", {})
            steps = sorted({float(v) for v in s.get("steps") or []})
            if steps:
                s["steps"], s["min"], s["max"] = steps, steps[0], steps[-1]
            if len(steps) == 1 or float(s.get("max", 0)) <= float(s.get("min", 0)):
                raise HTTPException(400, f"Критерий {x['id']}: шкала должна содержать хотя бы два значения")
    if not any(x["type"] != "flag" for x in crits):
        raise HTTPException(400, "Нужен хотя бы один критерий с весом")


@app.get("/api/checklists")
def list_checklists():
    return list(reversed(db.all("checklists")))


@app.get("/api/checklists/active")
def active_checklist():
    cls = db.all("checklists")
    act = [c for c in cls if c.get("active")]
    return (act or cls)[-1]


@app.get("/api/checklists/{cid}")
def get_checklist(cid: str):
    c = db.get("checklists", cid)
    if not c:
        raise HTTPException(404, "Чек-лист не найден")
    return c


@app.post("/api/checklists")
def save_checklist(body: dict = Body(...)):
    """Каждое сохранение — новая неизменяемая версия; старые оценки ссылаются на свою версию."""
    validate_checklist(body)
    family = body.get("family_id") or db.new_id("fam")
    versions = [c["version"] for c in db.all("checklists") if c.get("family_id") == family]
    doc = {k: v for k, v in body.items() if k not in ("id", "created_at", "active")}
    doc.update(id=db.new_id("cl"), family_id=family, version=max(versions, default=0) + 1)
    for c in db.all("checklists"):
        if c.get("active"):
            c["active"] = False
            db.put("checklists", c)
    doc["active"] = True
    return db.put("checklists", doc)


@app.post("/api/checklists/draft-criterion")
async def draft_criterion(body: dict = Body(...)):
    """ИИ-помощник: критерий из описания своими словами."""
    text = (body.get("text") or "").strip()
    if len(text) < 3:
        raise HTTPException(400, "Опишите, что нужно проверить")
    try:
        return await evaluator.draft_criterion(text, body.get("context") or "")
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.post("/api/checklists/{cid}/activate")
def activate_checklist(cid: str):
    target = get_checklist(cid)
    for c in db.all("checklists"):
        c["active"] = c["id"] == target["id"]
        db.put("checklists", c)
    return get_checklist(cid)


# ---------- оценка ----------

JOBS: dict[str, dict] = {}


def recompute(e: dict, checklist: dict) -> dict:
    """Пересчитывает финальный результат с учётом решений по апелляциям."""
    e["final_result"] = scoring.compute(checklist, final_answers(e))
    appeals = [a for a in db.all("appeals") if a["evaluation_id"] == e["id"]]
    e["open_appeals"] = sum(1 for a in appeals if a["status"] == "open")
    total = e["final_result"]["total"]
    if e["open_appeals"]:
        e["case_status"] = "disputed"
    elif appeals:
        e["case_status"] = "reviewed"
    elif e["final_result"]["passed"] is None:
        e["case_status"] = "scored"
    else:
        e["case_status"] = "passed" if e["final_result"]["passed"] else "failed"
    e["final_total"] = total
    return e


async def run_one(job: dict, dialog: dict, checklist: dict, sem: asyncio.Semaphore) -> None:
    e = {"id": db.new_id("ev"), "dialog_id": dialog["id"], "checklist_id": checklist["id"],
         "checklist_version": checklist["version"], "checklist_name": checklist["name"],
         "job_id": job["id"], "status": "running"}
    async with sem:
        db.put("evaluations", e)
        try:
            res = await evaluator.evaluate(dialog, checklist)
            answers = res["answers"]
            e.update(status="done", model=res["model"], usage=res["usage"], cost_usd=res["cost_usd"],
                     latency_s=res["latency_s"], summary=res["summary"], samples=res["samples"],
                     timing_facts=res["timing_facts"], raw=res["raw"],
                     answers=answers, warnings=res["warnings"], pii=res["pii"], overrides={})
            e["ai_result"] = scoring.compute(checklist, answers)
            recompute(e, checklist)
            job["cost_usd"] = round(job["cost_usd"] + res["cost_usd"], 4)
            job["done"] += 1
        except Exception as ex:  # одна ошибка не должна ронять пакет
            log.exception("evaluation failed for %s", dialog["id"])
            e.update(status="error", error=str(ex)[:500])
            job["failed"].append({"dialog_id": dialog["id"], "error": str(ex)[:300]})
        db.put("evaluations", e)
        job["finished"] = job["done"] + len(job["failed"])
        db.put("jobs", job)


async def run_job(job: dict, dialogs: list[dict], checklist: dict) -> None:
    sem = asyncio.Semaphore(CONCURRENCY)
    await asyncio.gather(*(run_one(job, d, checklist, sem) for d in dialogs))
    job["status"] = "done"
    job["finished_at"] = db.now()
    db.put("jobs", job)


@app.post("/api/evaluations/run")
async def run_evaluations(body: dict = Body(...)):
    checklist = get_checklist(body["checklist_id"]) if body.get("checklist_id") else active_checklist()
    ids = body.get("dialog_ids")
    if body.get("only_missing"):
        latest = latest_evaluations()
        pool = [d for d in db.all("dialogs") if (body.get("dataset") in (None, d["dataset"]))]
        dialogs = [d for d in pool if (latest.get((d["id"], checklist["id"])) or {}).get("status") != "done"]
    elif ids:
        dialogs = [d for d in (db.get("dialogs", i) for i in ids) if d]
    else:
        dialogs = [d for d in db.all("dialogs") if body.get("dataset") in (None, d["dataset"])]
    if not dialogs:
        raise HTTPException(400, "Нет диалогов для оценки")
    job = {"id": db.new_id("job"), "status": "running", "checklist_id": checklist["id"],
           "checklist_version": checklist["version"], "total": len(dialogs), "done": 0, "finished": 0,
           "failed": [], "cost_usd": 0.0, "model": evaluator.MODEL if evaluator.has_llm() else "mock"}
    db.put("jobs", job)
    JOBS[job["id"]] = job
    asyncio.get_running_loop().create_task(run_job(job, dialogs, checklist))
    return job


@app.get("/api/jobs")
def list_jobs():
    return list(reversed(db.all("jobs")))[:20]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = JOBS.get(job_id) or db.get("jobs", job_id)
    if not j:
        raise HTTPException(404, "Задача не найдена")
    return j


@app.get("/api/evaluations/{eid}")
def get_evaluation(eid: str):
    e = db.get("evaluations", eid)
    if not e:
        raise HTTPException(404, "Оценка не найдена")
    e["checklist"] = db.get("checklists", e["checklist_id"])
    e["appeals"] = [a for a in db.all("appeals") if a["evaluation_id"] == eid]
    return e


# ---------- апелляции ----------

@app.post("/api/appeals")
def create_appeal(body: dict = Body(...)):
    e = db.get("evaluations", body.get("evaluation_id", ""))
    if not e or e["status"] != "done":
        raise HTTPException(400, "Оценка не найдена или не завершена")
    cid = body.get("criterion_id")
    if cid not in e["answers"]:
        raise HTTPException(400, "Неизвестный критерий")
    checklist = db.get("checklists", e["checklist_id"])
    crit = next(c for c in checklist["criteria"] if c["id"] == cid)
    if crit.get("appealable") is False:
        raise HTTPException(400, "Этот критерий нельзя оспорить")
    if any(a["evaluation_id"] == e["id"] and a["criterion_id"] == cid and a["status"] == "open"
           for a in db.all("appeals")):
        raise HTTPException(409, "По этому критерию уже есть открытая апелляция")
    comment = (body.get("comment") or "").strip()
    if not comment:
        raise HTTPException(400, "Добавьте комментарий к апелляции")
    current = (e.get("overrides") or {}).get(cid) or e["answers"][cid]
    a = {"id": db.new_id("ap"), "evaluation_id": e["id"], "dialog_id": e["dialog_id"], "criterion_id": cid,
         "criterion_name": crit["name"], "comment": comment, "author": body.get("author") or "Оператор",
         "status": "open", "disputed_value": current.get("value"), "disputed_na": current.get("na", False),
         "proposed_value": body.get("proposed_value")}
    db.put("appeals", a)
    recompute(e, checklist)
    db.put("evaluations", e)
    return a


@app.get("/api/appeals")
def list_appeals(status: str | None = None):
    out = []
    for a in reversed(db.all("appeals")):
        if status and a["status"] != status:
            continue
        e = db.get("evaluations", a["evaluation_id"]) or {}
        a["evaluation_total"] = (e.get("final_result") or {}).get("total")
        a["checklist_version"] = e.get("checklist_version")
        out.append(a)
    return out


@app.post("/api/appeals/{aid}/decision")
def decide_appeal(aid: str, body: dict = Body(...)):
    a = db.get("appeals", aid)
    if not a or a["status"] != "open":
        raise HTTPException(400, "Апелляция не найдена или уже рассмотрена")
    decision = body.get("decision")
    reason = (body.get("reason") or "").strip()
    if decision not in ("confirm", "change"):
        raise HTTPException(400, "decision: confirm или change")
    if not reason:
        raise HTTPException(400, "Укажите причину решения")
    e = db.get("evaluations", a["evaluation_id"])
    checklist = db.get("checklists", e["checklist_id"])
    crit = next(c for c in checklist["criteria"] if c["id"] == a["criterion_id"])
    before = scoring.compute(checklist, final_answers(e))
    if decision == "change":
        na = bool(body.get("na"))
        if na and not crit.get("na_allowed"):
            raise HTTPException(400, "Для этого критерия N/A не допускается")
        value = None if na else scoring.normalize_value(crit, body.get("value"))
        if not na and value is None:
            raise HTTPException(400, "Укажите новое значение")
        # Необязательная ручная корректировка: баллы в единицах веса критерия (0…вес),
        # например 5 из 10 за частично выполненное «Да / Нет».
        manual_ratio = None
        mp = body.get("manual_points")
        lo, _ = scoring.value_range(crit)
        # «Нет» / нижняя ступень — это 0 баллов по определению, ручная корректировка к ним не применяется
        if mp not in (None, "") and not na and crit.get("type") != "flag" and value > lo:
            w = float(crit["weight"])
            if not 0 <= float(mp) <= w:
                raise HTTPException(400, f"Баллы вручную — от 0 до {w:g}")
            manual_ratio = round(float(mp) / w, 4)
        e.setdefault("overrides", {})[a["criterion_id"]] = {
            "value": value, "na": na, "critical_error": bool(body.get("critical_error", False)),
            "manual_ratio": manual_ratio, "manual_points": None if manual_ratio is None else float(mp),
            "reason": reason, "appeal_id": aid, "reviewer": body.get("reviewer") or "Проверяющий", "at": db.now()}
    a.update(status="resolved", decision=decision, reason=reason, reviewer=body.get("reviewer") or "Проверяющий",
             resolved_at=db.now(), new_value=e.get("overrides", {}).get(a["criterion_id"], {}).get("value")
             if decision == "change" else a["disputed_value"],
             new_na=e.get("overrides", {}).get(a["criterion_id"], {}).get("na") if decision == "change"
             else a["disputed_na"], total_before=before["total"],
             manual_points=e.get("overrides", {}).get(a["criterion_id"], {}).get("manual_points") if decision == "change" else None)
    db.put("appeals", a)
    recompute(e, checklist)
    a["total_after"] = e["final_result"]["total"]
    db.put("appeals", a)
    db.put("evaluations", e)
    return a


# ---------- сводка ----------

@app.get("/api/stats")
def stats(checklist_id: str | None = None):
    checklist = get_checklist(checklist_id) if checklist_id else active_checklist()
    latest = [e for (d, c), e in latest_evaluations().items() if c == checklist["id"] and e["status"] == "done"]
    totals = [e["final_result"]["total"] for e in latest if e["final_result"]["total"] is not None]
    per_crit = {}
    for c in [c for c in checklist["criteria"] if c.get("type") != "flag"]:
        vals = [e["final_result"]["criteria"] for e in latest]
        rows = [r for rs in vals for r in rs if r["criterion_id"] == c["id"]]
        app_rows = [r for r in rows if not r["na"]]
        per_crit[c["id"]] = {"name": c["name"], "na": len(rows) - len(app_rows),
                             "avg_ratio": round(sum(r["ratio"] for r in app_rows) / len(app_rows), 3) if app_rows else None,
                             "full": sum(1 for r in app_rows if r["ratio"] == 1),
                             "partial": sum(1 for r in app_rows if 0 < r["ratio"] < 1),
                             "zero": sum(1 for r in app_rows if r["ratio"] == 0)}
    by_lang = {}
    for e in latest:
        d = db.get("dialogs", e["dialog_id"]) or {}
        by_lang.setdefault(d.get("language", "?"), []).append(e["final_result"]["total"] or 0)
    costs = [e.get("cost_usd", 0) for e in latest if e.get("model") != "mock"]
    return {
        "checklist": {"id": checklist["id"], "name": checklist["name"], "version": checklist["version"]},
        "evaluated": len(latest), "dialogs": len(db.all("dialogs")),
        "avg_total": round(sum(totals) / len(totals), 1) if totals else None,
        "passed": sum(1 for e in latest if e["final_result"]["passed"]),
        "critical": sum(1 for e in latest if e["final_result"]["critical_errors"]),
        "open_appeals": sum(1 for a in db.all("appeals") if a["status"] == "open"),
        "criteria": per_crit,
        "by_language": {k: round(sum(v) / len(v), 1) for k, v in by_lang.items()},
        "distribution": [sum(1 for t in totals if lo <= t < lo + 10 or (lo == 90 and t == 100))
                         for lo in range(0, 100, 10)],
        "avg_cost_usd": round(sum(costs) / len(costs), 4) if costs else None,
        "avg_latency_s": round(sum(e.get("latency_s", 0) for e in latest) / len(latest), 1) if latest else None,
    }


# ---------- экспорт, эталон и точность ----------

def answer_label(c: dict, a: dict | None):
    if not a or a.get("value") is None and not a.get("na"):
        return None
    if a.get("na"):
        return "N/A"
    if c.get("type") == "scale":
        v = a["value"]
        return int(v) if float(v).is_integer() else v
    return "Да" if a["value"] >= 1 else "Нет"


def final_answers(e: dict) -> dict:
    """Ответы ИИ с наложенными решениями проверяющего (включая ручные баллы)."""
    out = {k: dict(v) for k, v in e["answers"].items()}
    for k, ov in (e.get("overrides") or {}).items():
        if k in out:
            out[k].update(value=ov["value"], na=ov["na"],
                          critical_error=ov.get("critical_error", out[k].get("critical_error", False)),
                          manual_ratio=ov.get("manual_ratio"))
    return out


@app.get("/api/export")
def export(checklist_id: str | None = None, dataset: str | None = None, fmt: str = "json"):
    checklist = get_checklist(checklist_id) if checklist_id else active_checklist()
    rows = []
    for (did, cid), e in latest_evaluations().items():
        d = db.get("dialogs", did)
        if cid != checklist["id"] or e["status"] != "done" or not d or (dataset and d["dataset"] != dataset):
            continue
        fa = final_answers(e)
        rows.append({
            "conversation_id": did, "dataset": d["dataset"], "language": d["language"], "channel": d["channel"],
            "checklist_version": checklist["version"], "total": e["final_result"]["total"],
            "ai_total": e["ai_result"]["total"], "gross_errors": bool(e["final_result"]["critical_errors"]),
            "criteria": {c["id"]: {"answer": answer_label(c, fa.get(c["id"])),
                                   "explanation": (fa.get(c["id"]) or {}).get("na_reason") or (fa.get(c["id"]) or {}).get("explanation", ""),
                                   "evidence": [f"[{x['turn']}] {x['quote']}" for x in (fa.get(c["id"]) or {}).get("evidence", [])]}
                         for c in checklist["criteria"]},
        })
    rows.sort(key=lambda r: r["conversation_id"])
    if fmt != "csv":
        return {"checklist": {"id": checklist["id"], "name": checklist["name"], "version": checklist["version"]},
                "records": rows}
    import csv, io
    buf = io.StringIO()
    ids = [c["id"] for c in checklist["criteria"]]
    w = csv.writer(buf)
    w.writerow(["conversation_id", "dataset", "language", "total", "has_gross_error"] + ids)
    for r in rows:
        w.writerow([r["conversation_id"], r["dataset"], r["language"], r["total"], "Да" if r["gross_errors"] else "Нет"]
                   + [r["criteria"][i]["answer"] for i in ids])
    from fastapi.responses import Response
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="cqa_v{checklist["version"]}.csv"'})


def gt_value(c: dict, v):
    """Эталон в любой удобной форме (Да/Нет/N/A/число/yes/no/true) → {na, value}."""
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("n/a", "na", "н/д", "неприменимо"):
        return {"na": True, "value": None}
    if s in ("да", "yes", "true", "иә", "1") and c.get("type") != "scale":
        return {"na": False, "value": 1.0}
    if s in ("нет", "no", "false", "жоқ", "0") and c.get("type") != "scale":
        return {"na": False, "value": 0.0}
    try:
        return {"na": False, "value": scoring.normalize_value(c, float(s))}
    except ValueError:
        return None


@app.post("/api/ground-truth")
def save_ground_truth(body=Body(...)):
    """Принимает [{conversation_id, criteria: {id: "Да"|"Нет"|"N/A"|число}, total?}] или {records: [...]}."""
    recs = body.get("records") if isinstance(body, dict) else body
    n = 0
    for r in recs or []:
        cid = r.get("conversation_id")
        crit = r.get("criteria") or {}
        crit = {k: (v.get("answer") if isinstance(v, dict) else v) for k, v in crit.items()}
        if cid and crit:
            db.put("ground_truth", {"id": cid, "criteria": crit, "total": r.get("total"), "source": r.get("source", "upload")})
            n += 1
    return {"saved": n}


@app.post("/api/ground-truth/from-evaluation/{eid}")
def ground_truth_from_eval(eid: str):
    """Финальный результат (после проверки человеком) становится эталоном для замера точности."""
    e = db.get("evaluations", eid)
    if not e or e["status"] != "done":
        raise HTTPException(400, "Оценка не найдена")
    checklist = db.get("checklists", e["checklist_id"])
    fa = final_answers(e)
    db.put("ground_truth", {"id": e["dialog_id"], "source": "reviewer", "total": e["final_result"]["total"],
                            "criteria": {c["id"]: answer_label(c, fa.get(c["id"])) for c in checklist["criteria"]}})
    return {"ok": True}


@app.delete("/api/ground-truth/{did}")
def delete_ground_truth(did: str):
    db.delete("ground_truth", did)
    return {"ok": True}


@app.get("/api/accuracy")
def accuracy(checklist_id: str | None = None):
    checklist = get_checklist(checklist_id) if checklist_id else active_checklist()
    latest = latest_evaluations()
    per = {c["id"]: {"name": c["name"], "n": 0, "match": 0, "abs_err": 0.0} for c in checklist["criteria"]}
    dialogs, tot_err = [], []
    for gt in db.all("ground_truth"):
        e = latest.get((gt["id"], checklist["id"]))
        if not e or e["status"] != "done":
            dialogs.append({"conversation_id": gt["id"], "evaluated": False})
            continue
        row = {"conversation_id": gt["id"], "evaluated": True, "ai_total": e["ai_result"]["total"],
               "gt_total": None, "mismatches": []}
        gt_answers = {}
        for c in checklist["criteria"]:
            g = gt_value(c, gt["criteria"].get(c["id"]))
            if g is None:
                continue
            gt_answers[c["id"]] = g
            a = e["answers"].get(c["id"]) or {}
            ok = bool(a.get("na")) == g["na"] and (g["na"] or a.get("value") == g["value"])
            p = per[c["id"]]
            p["n"] += 1
            p["match"] += ok
            if not g["na"] and not a.get("na") and a.get("value") is not None:
                lo, hi = scoring.value_range(c)
                p["abs_err"] += abs(a["value"] - g["value"]) / ((hi - lo) or 1)
            elif g["na"] != bool(a.get("na")):
                p["abs_err"] += 1
            if not ok:
                row["mismatches"].append({"criterion_id": c["id"], "ai": answer_label(c, a), "gt": gt["criteria"].get(c["id"])})
        gt_total = gt.get("total")
        if gt_total is None and gt_answers:
            gt_total = scoring.compute(checklist, gt_answers)["total"]
        row["gt_total"] = gt_total
        if gt_total is not None and row["ai_total"] is not None:
            row["abs_err"] = round(abs(row["ai_total"] - float(gt_total)), 2)
            tot_err.append(row["abs_err"])
        dialogs.append(row)
    n = sum(p["n"] for p in per.values())
    return {
        "checklist": {"id": checklist["id"], "version": checklist["version"]},
        "dialogs": dialogs,
        "criteria": {k: {**v, "accuracy": round(v["match"] / v["n"], 3) if v["n"] else None,
                         "mae": round(v["abs_err"] / v["n"], 3) if v["n"] else None} for k, v in per.items()},
        "exact_match": round(sum(p["match"] for p in per.values()) / n, 3) if n else None,
        "total_mae": round(sum(tot_err) / len(tot_err), 2) if tot_err else None,
        "n_dialogs": sum(1 for d in dialogs if d["evaluated"]),
    }


@app.get("/api/health")
def health():
    b = evaluator.backend()
    return {"ok": True, "llm": evaluator.has_llm(), "backend": b,
            "model": "mock" if b == "mock" else evaluator.MODEL + (" · claude -p" if b == "cli" else ""),
            "effort": evaluator.EFFORT, "samples": evaluator.SAMPLES, "concurrency": CONCURRENCY}


# ---------- старт ----------

@app.on_event("startup")
def seed():
    if not db.all("checklists"):
        base = json.loads((Path(__file__).parent / "checklist_default.json").read_text("utf-8"))
        save_checklist(base)
        log.info("seeded default checklist")
    dev = ROOT / "data" / "dev.json"
    if not db.all("dialogs") and dev.exists():
        n = len(import_payload(json.loads(dev.read_text("utf-8")), "DEV", "api"))
        log.info("imported %d DEV dialogs from cache", n)


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
