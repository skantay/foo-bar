"""CQA — Conversation Quality Analytics: API + статика интерфейса."""
import asyncio
import json
import logging
import os
import re
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


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    """Интерфейс без сборки: браузер должен перепроверять файлы, иначе после обновления видны старые модули."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---------- данные ----------

SPEAKER_ALIAS = {"оператор": "operator", "agent": "operator", "клиент": "client", "customer": "client",
                 "user": "client", "бот": "bot", "система": "system"}


ROLES = ("operator", "client", "bot", "system")
MAX_UPLOAD = 10 * 1024 * 1024


def speaker(s) -> str:
    s = str(s or "").strip().lower()
    return SPEAKER_ALIAS.get(s, s)


def check_dialog(d: dict, need_operator: bool) -> None:
    """Строгая проверка загружаемого файла: мусор не должен становиться «диалогом».

    Оператор обязателен только для txt: в наборе организаторов бывают чаты, где оператор
    не подключился, — их нужно оценить и сдать, а не отклонять весь файл."""
    for i, t in enumerate(d["transcript"]):
        if t["speaker"] not in ROLES:
            raise ValueError(f"реплика {i + 1}: неизвестная роль {t['speaker']!r}, ожидается {', '.join(ROLES)}")
        if not t["text"].strip():
            raise ValueError(f"реплика {i + 1}: пустой текст")
    if not d["transcript"]:
        raise ValueError("пустой транскрипт")
    if need_operator and not any(t["speaker"] == "operator" for t in d["transcript"]):
        raise ValueError("нет ни одной реплики оператора — оценивать нечего")


def normalize_record(rec: dict, dataset: str, source: str) -> dict:
    cid = rec.get("conversation_id") or rec.get("id")
    transcript = rec.get("transcript")
    if not cid or not isinstance(transcript, list):
        raise ValueError("ожидаются поля conversation_id и transcript[]")
    audio = rec.get("audio_url") or None
    if audio and not re.match(r"https?://", str(audio), re.I):  # в ссылку плеера — только http(s)
        audio = None
    channel = rec.get("channel") or ("voice call" if audio else "agent chat")
    if any(not isinstance(t, dict) or not isinstance(t.get("text", ""), str) for t in transcript):
        raise ValueError("реплика должна быть объектом с текстом-строкой")
    return {
        "id": str(cid),
        "dataset": dataset,
        "source": source,
        "channel": channel,
        "language": (rec.get("language") or "").upper() or "RU",
        "audio_url": audio,
        "transcript": [{"timestamp": t.get("timestamp", ""), "speaker": speaker(t.get("speaker")),
                        "text": t.get("text", "")} for t in transcript],
    }


def import_payload(payload, dataset: str, source: str, strict: bool = False, need_operator: bool = False) -> tuple[list[str], str]:
    """Принимает ответ API организаторов, массив записей или одну запись.

    Возвращает id и фактический набор: у ответа API он берётся из dataset_group_id.
    """
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
    if not isinstance(records, list) or not records:
        raise HTTPException(400, "В файле нет записей диалогов")
    docs = []
    for rec in records:  # сначала проверяем всё, чтобы плохой файл не загрузился наполовину
        try:
            if not isinstance(rec, dict):
                raise ValueError("ожидается объект диалога")
            d = normalize_record(rec, dataset, source)
            if strict:
                check_dialog(d, need_operator)
        except ValueError as e:
            cid = rec.get("conversation_id") if isinstance(rec, dict) else None
            raise HTTPException(400, f"Некорректная запись{f' {cid}' if cid else ''}: {e}")
        docs.append(d)
    ids = []
    for d in docs:
        old = db.get("dialogs", d["id"])
        if old:
            d["created_at"] = old["created_at"]
        db.put("dialogs", d)
        ids.append(d["id"])
    return ids, dataset


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
    try:
        payload = r.json()
    except ValueError:
        raise HTTPException(502, "API организаторов вернул не JSON")
    ids, dataset = import_payload(payload, group_id, "api")
    return {"imported": len(ids), "dataset": dataset, "ids": ids}


@app.post("/api/dialogs/upload")
async def upload_dialogs(file: UploadFile = File(...), dataset: str = "UPLOAD"):
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(400, "Файл больше 10 МБ")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "Файл не текстовый: ожидается JSON или транскрипт .txt в UTF-8")
    plain = False
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        try:
            payload, plain = parse_plain_transcript(text, Path(file.filename or "dialog").stem), True
        except ValueError as e:
            raise HTTPException(400, f"Не похоже на транскрипт: {e}")
    ids, dataset = import_payload(payload, dataset, "upload", strict=True, need_operator=plain)
    return {"imported": len(ids), "dataset": dataset, "ids": ids}


def parse_plain_transcript(text: str, cid: str) -> dict:
    """Текстовый транскрипт: строки вида `[00:12] operator: текст` или `client: текст`."""
    turns = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        ts, body = "", line
        if body.startswith("[") and "]" in body:
            ts, body = body[1:body.index("]")], body[body.index("]") + 1:].strip()
        sp, sep, msg = body.partition(":")
        sp = speaker(sp)
        if sep and sp in ROLES:
            turns.append({"timestamp": ts, "speaker": sp, "text": msg.strip()})
        elif turns:  # строка без роли — продолжение предыдущей реплики
            turns[-1]["text"] += "\n" + line
        else:
            raise ValueError(f"строка {n} должна начинаться с роли: «operator: …», «client: …» "
                             f"(можно с меткой времени «[00:12] operator: …»)")
    return {"conversation_id": cid, "channel": "agent chat", "language": "RU", "transcript": turns}


def latest_evaluations() -> dict[tuple[str, str], dict]:
    """Последняя оценка по паре (диалог, чек-лист). Завершённая важнее более новой незавершённой:
    упавшая или прерванная переоценка не должна прятать готовый результат."""
    out = {}
    for e in db.all("evaluations"):  # отсортированы по created_at
        k = (e["dialog_id"], e["checklist_id"])
        if e.get("status") == "done" or (out.get(k) or {}).get("status") != "done":
            out[k] = e
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
        elif a.get("critical_error"):
            m = "crit"
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
            "checklist_id": e["checklist_id"], "checklist_version": e.get("checklist_version"),
            "created_at": e["created_at"]}


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

def num(v, what: str) -> float:
    """Число из JSON (в т.ч. строкой «10»); иначе 400, а не падение оценки позже."""
    try:
        if isinstance(v, bool):
            raise ValueError
        return float(v)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{what}: ожидается число, получено {v!r}")


def validate_checklist(c: dict) -> None:
    if not c.get("name"):
        raise HTTPException(400, "Укажите название чек-листа")
    if c.get("pass_threshold") not in (None, ""):
        c["pass_threshold"] = num(c["pass_threshold"], "Порог прохождения")
    if c.get("first_response_limit_s") not in (None, ""):
        c["first_response_limit_s"] = int(num(c["first_response_limit_s"], "Порог первой реакции"))
    if c.get("critical_mode") not in (None, "flag", "zero"):
        raise HTTPException(400, "critical_mode: flag или zero")
    r = c.get("rounding") or {}
    if "digits" in r:
        r["digits"] = int(num(r["digits"], "Округление"))
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
        else:
            x["weight"] = num(x.get("weight") or 0, f"Критерий {x['id']}: вес")
            if x["weight"] <= 0:
                raise HTTPException(400, f"Критерий {x['id']}: вес должен быть > 0")
        if x["type"] == "scale":
            s = x.setdefault("scale", {})
            steps = sorted({num(v, f"Критерий {x['id']}: ступень") for v in s.get("steps") or []})
            if steps:
                s["steps"], s["min"], s["max"] = steps, steps[0], steps[-1]
            if len(steps) == 1 or num(s.get("max", 0), "max") <= num(s.get("min", 0), "min"):
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


def new_evaluation(job: dict, dialog: dict, checklist: dict) -> dict:
    """Запись создаётся сразу при запуске: карточка видит «running», а не прошлую оценку как готовую."""
    return db.put("evaluations", {
        "id": db.new_id("ev"), "dialog_id": dialog["id"], "checklist_id": checklist["id"],
        "checklist_version": checklist["version"], "checklist_name": checklist["name"],
        "job_id": job["id"], "status": "running"})


async def run_one(job: dict, e: dict, dialog: dict, checklist: dict, sem: asyncio.Semaphore) -> None:
    async with sem:
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


async def run_job(job: dict, pairs: list[tuple[dict, dict]], checklist: dict) -> None:
    sem = asyncio.Semaphore(CONCURRENCY)
    await asyncio.gather(*(run_one(job, e, d, checklist, sem) for e, d in pairs))
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
    if evaluator.backend() == "none":
        raise HTTPException(400, evaluator.NO_MODEL)
    job = {"id": db.new_id("job"), "status": "running", "checklist_id": checklist["id"],
           "checklist_version": checklist["version"], "total": len(dialogs), "done": 0, "finished": 0,
           "failed": [], "cost_usd": 0.0, "model": evaluator.MODEL if evaluator.has_llm() else evaluator.backend()}
    db.put("jobs", job)
    JOBS[job["id"]] = job
    pairs = [(new_evaluation(job, d, checklist), d) for d in dialogs]
    asyncio.get_running_loop().create_task(run_job(job, pairs, checklist))
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
            mp = num(mp, "Баллы вручную")
            if not 0 <= mp <= w:
                raise HTTPException(400, f"Баллы вручную — от 0 до {w:g}")
            manual_ratio = round(mp / w, 4)
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
        if e["final_result"]["total"] is not None:  # итог N/A — не ноль
            by_lang.setdefault(d.get("language", "?"), []).append(e["final_result"]["total"])
    costs = [e.get("cost_usd", 0) for e in latest if e.get("model") != "mock"]
    return {
        "checklist": {"id": checklist["id"], "name": checklist["name"], "version": checklist["version"]},
        "evaluated": len(latest), "dialogs": len(db.all("dialogs")),
        "avg_total": round(sum(totals) / len(totals), 1) if totals else None,
        # без порога в чек-листе «прошёл/не прошёл» не определено
        "passed": None if checklist.get("pass_threshold") in (None, "")
        else sum(1 for e in latest if e["final_result"]["passed"]),
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


# ---------- результаты в формате организаторов (Appendix Results, JSON 1.0) ----------

# наши id критериев → коды организаторов
OFFICIAL_CODE = {"needs": "discovery", "management": "dialogue", "closing": "closure", "gross_errors": "critical"}
OFFICIAL_CODES = ["contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical"]
TEAM_NAME = os.getenv("CQA_TEAM_NAME", "foo-bar")
# допустимые value по Appendix Results: true/false, null = N/A, число = ступень шкалы
_YN, _YNN = {True, False}, {True, False, None}
OFFICIAL_VALUES = {"contact": _YN, "discovery": _YNN, "identification": _YNN, "consultation": {0, 5, 10, 15, 20, None},
                   "empathy": _YN, "dialogue": {0, 4, 7, 10}, "closure": _YNN, "critical": _YN}


def official_value(c: dict, a: dict | None):
    """true/false = Да/Нет, null = N/A, число = ступень шкалы до учёта веса."""
    if not a or a.get("na") or a.get("value") is None:
        return None
    if c.get("type") == "scale":
        v = float(a["value"])
        return int(v) if v.is_integer() else v
    return a["value"] >= 1


def official_record(d: dict, e: dict | None, checklist: dict) -> dict:
    if not e or e.get("status") != "done":
        reason = (e or {}).get("error") or ("Оценка ещё не завершена" if e else "Диалог не оценён")
        return {"conversation_id": d["id"], "status": "error", "criteria": [], "score": None, "error": reason}
    fa = final_answers(e)
    overrides = e.get("overrides") or {}
    criteria = []
    for c in checklist["criteria"]:
        a = fa.get(c["id"]) or {}
        turns = sorted({x["turn"] + 1 for x in a.get("evidence") or []})  # нумерация с 1 по всему транскрипту
        text = (a.get("na_reason") if a.get("na") else a.get("explanation")) or ""
        if c["id"] in overrides:  # обоснование ИИ противоречило бы изменённому значению
            text = f"{c['name']} — {answer_label(c, a)} (решение проверяющего). {overrides[c['id']]['reason']}"
        criteria.append({"criterion_id": OFFICIAL_CODE.get(c["id"], c["id"]), "value": official_value(c, a),
                         # в тексте модель ссылается на [N] с нуля — приводим к нумерации с 1, как evidence_turns
                         "rationale": re.sub(r"\[(\d+)\]", lambda m: f"[{int(m.group(1)) + 1}]", text),
                         "evidence_turns": turns})
    critical = next((x["value"] for x in criteria if x["criterion_id"] == "critical"), None)
    # Итог по Appendix выводится из value критериев: без ручных баллов апелляции
    # и без обнуления за грубую ошибку («флаг не обнуляет итог автоматически»).
    score = scoring.compute(checklist, {k: {**a, "manual_ratio": None} for k, a in fa.items()})["raw_total"]
    return {"conversation_id": d["id"], "status": "ok", "criteria": criteria,
            "score": {"final_score": score,
                      "critical_flag": bool(critical) if critical is not None else bool(e["final_result"]["critical_errors"])}}


def validate_results(doc: dict, expected_ids: list[str]) -> list[str]:
    problems = []
    ids = [r["conversation_id"] for r in doc["results"]]
    if len(ids) != len(set(ids)):
        problems.append("повторяющиеся conversation_id")
    if missing := set(expected_ids) - set(ids):
        problems.append(f"нет записей: {', '.join(sorted(missing))}")
    for r in doc["results"]:
        if r["status"] == "error":
            problems.append(f"{r['conversation_id']}: ошибка оценки — {r['error']}")
            continue
        codes = [x["criterion_id"] for x in r["criteria"]]
        if sorted(codes) != sorted(OFFICIAL_CODES):
            extra = [c for c in codes if c not in OFFICIAL_CODES]
            missing = [c for c in OFFICIAL_CODES if c not in codes]
            diff = "; ".join(x for x in (f"нет кодов {', '.join(missing)}" if missing else "",
                                         f"лишние {', '.join(extra)}" if extra else "") if x)
            problems.append(f"{r['conversation_id']}: {diff}. Если критерий заменён, "
                            f"впишите ему код заменённого в «Чек-листы» → критерий → «Ещё» → «ID (для экспорта)»")
        for x in r["criteria"]:
            dom = OFFICIAL_VALUES.get(x["criterion_id"])
            v = x["value"]
            ok = dom is None or (v is None and None in dom) or (
                v is not None and isinstance(v, bool) == (True in dom) and v in dom)
            if not ok:
                problems.append(f"{r['conversation_id']}.{x['criterion_id']}: недопустимое value {v!r} "
                                f"(допустимо {sorted(map(str, dom))}) — проверьте критерий вручную")
    return problems


@app.get("/api/results")
def official_results(dataset: str, checklist_id: str | None = None):
    """Файл results.json для сдачи: одна запись на каждый диалог набора, включая неоценённые (status=error)."""
    checklist = get_checklist(checklist_id) if checklist_id else active_checklist()
    dialogs = sorted((d for d in db.all("dialogs") if d["dataset"] == dataset), key=lambda d: d["id"])
    if not dialogs:
        raise HTTPException(404, f"В наборе {dataset} нет диалогов")
    latest = latest_evaluations()
    b = evaluator.backend()
    doc = {"schema_version": "1.0", "team_name": TEAM_NAME,
           "model": {"mode": "local" if b == "mock" else "external_api", "name": "mock" if b == "mock" else evaluator.MODEL},
           "results": [official_record(d, latest.get((d["id"], checklist["id"])), checklist) for d in dialogs]}
    return {"document": doc, "problems": validate_results(doc, [d["id"] for d in dialogs]),
            "checklist_version": checklist["version"]}


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
    recs = (body.get("records") or body.get("results")) if isinstance(body, dict) else body
    ours = {v: k for k, v in OFFICIAL_CODE.items()}  # коды организаторов → наши id
    n = 0
    for r in recs or []:
        if not isinstance(r, dict) or r.get("status") == "error":
            continue
        cid = r.get("conversation_id")
        crit = r.get("criteria") or {}
        if isinstance(crit, list):  # формат results.json: [{criterion_id, value}], null = N/A
            crit = {x.get("criterion_id"): "N/A" if x.get("value") is None else x.get("value")
                    for x in crit if isinstance(x, dict)}
        crit = {ours.get(k, k): (v.get("answer") if isinstance(v, dict) else v) for k, v in crit.items()}
        total = r.get("total", (r.get("score") or {}).get("final_score"))
        try:
            total = None if total in (None, "") else float(total)
        except (TypeError, ValueError):
            total = None
        if cid and crit:
            db.put("ground_truth", {"id": cid, "criteria": crit, "total": total, "source": r.get("source", "upload")})
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
        # итог по эталону считаем, только если размечены все балльные критерии:
        # по части критериев он несравним с итогом модели
        gt_total = gt.get("total")
        scored_ids = {c["id"] for c in checklist["criteria"] if not scoring.is_flag(c)}
        if gt_total is None and scored_ids <= gt_answers.keys():
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
            "model": {"mock": "mock (ненастоящие оценки)", "none": "не подключена"}.get(b) or evaluator.MODEL + (" · claude -p" if b == "cli" else ""),
            "effort": evaluator.EFFORT, "samples": evaluator.SAMPLES, "concurrency": CONCURRENCY}


# ---------- старт ----------

@app.on_event("startup")
def seed():
    if not db.all("checklists"):
        base = json.loads((Path(__file__).parent / "checklist_default.json").read_text("utf-8"))
        save_checklist(base)
        log.info("seeded default checklist")
    # задачи в памяти не переживают рестарт: незавершённые помечаем прерванными, иначе они висят «running»
    for j in db.all("jobs"):
        if j.get("status") == "running":
            j.update(status="interrupted", finished_at=db.now())
            db.put("jobs", j)
    for e in db.all("evaluations"):
        if e.get("status") == "running":
            e.update(status="error", error="Оценка прервана перезапуском сервера")
            db.put("evaluations", e)
    dev = ROOT / "data" / "dev.json"
    if not db.all("dialogs") and dev.exists():
        n = len(import_payload(json.loads(dev.read_text("utf-8")), "DEV", "api")[0])
        log.info("imported %d DEV dialogs from cache", n)


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
