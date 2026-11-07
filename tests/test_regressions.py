"""Регресс по багам, найденным прогоном «как жюри» (каждый тест падал до исправления)."""
import json

import pytest

import app as app_module
import db
import pii
from conftest import latest_eval, wait_job


def mask(*turns):
    d = {"id": "x", "transcript": [{"speaker": s, "text": t, "timestamp": ""} for s, t in turns]}
    return pii.mask_dialog(d)


# ---------- маскирование ----------

@pytest.mark.parametrize("text", ["Здравствуйте! Я Вам сейчас помогу.", "Сәлеметсіз бе! Мен Сізге көмектесемін.",
                                  "Я Вас понял, сейчас уточню."])
def test_polite_pronouns_are_not_names(text):
    """«Я [ИМЯ_ОПЕРАТОРА_1] помогу» выглядело для модели как представление → contact=Да."""
    masked, mapping, _ = mask(("operator", text))
    assert masked["transcript"][0]["text"] == text and not mapping


def test_real_name_still_masked_next_to_pronoun():
    masked, mapping, _ = mask(("operator", "Здравствуйте! Меня зовут Тимур. Я Вам помогу."))
    assert masked["transcript"][0]["text"] == "Здравствуйте! Меня зовут [ИМЯ_ОПЕРАТОРА_1]. Я Вам помогу."
    assert mapping == {"[ИМЯ_ОПЕРАТОРА_1]": "Тимур"}


def test_email_at_sentence_end_gets_same_label():
    """Точка в конце предложения попадала в адрес → разные метки → модель видела «другой» email."""
    masked, mapping, _ = mask(("client", "Почта ivan.petrov@mail.kz"),
                              ("operator", "Письмо придёт на ivan.petrov@mail.kz."))
    assert masked["transcript"][1]["text"] == "Письмо придёт на [EMAIL_1]."
    assert mapping == {"[EMAIL_1]": "ivan.petrov@mail.kz"}


# ---------- импорт ----------

def upload(client, payload, dataset="EDGE"):
    return client.post(f"/api/dialogs/upload?dataset={dataset}",
                       files={"file": ("d.json", json.dumps(payload), "application/json")})


def test_speaker_aliases_in_json(client):
    upload(client, {"records": [{"conversation_id": "a1", "transcript": [
        {"timestamp": "00:00", "speaker": "Customer", "text": "hi"},
        {"timestamp": "00:10", "speaker": "Agent", "text": "Hello"}]}]})
    assert [t["speaker"] for t in client.get("/api/dialogs/a1").json()["transcript"]] == ["client", "operator"]


def test_non_http_audio_url_dropped(client):
    upload(client, {"records": [{"conversation_id": "a2", "audio_url": "javascript:alert(1)",
                                 "transcript": [{"timestamp": "", "speaker": "client", "text": "x"}]}]})
    assert client.get("/api/dialogs/a2").json()["audio_url"] is None


def test_upload_returns_actual_dataset(client, dialogs_payload):
    """Ответ API организаторов, загруженный файлом, лежит в своём dataset_group_id — фронт фильтрует по нему."""
    r = upload(client, dialogs_payload, dataset="UPLOAD").json()
    assert r["dataset"] == "TEST"


def test_sync_non_json_is_502(client, monkeypatch):
    class R:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    class C:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url): return R()

    monkeypatch.setattr(app_module.httpx, "AsyncClient", C)
    r = client.post("/api/dialogs/sync", json={"group_id": "X"})
    assert r.status_code == 502 and "не JSON" in r.json()["detail"]


# ---------- чек-листы ----------

@pytest.mark.parametrize("patch", [
    {"pass_threshold": "abc"},
    {"critical_mode": "boom"},
    {"criteria_0": {"weight": "abc"}},
    {"criteria_3": {"scale": {"steps": ["x", 1]}}},
])
def test_invalid_numbers_rejected_and_active_kept(client, default_checklist, patch):
    """pass_threshold='abc' раньше сохранялся активным, и падала каждая следующая оценка."""
    before = client.get("/api/checklists/active").json()["id"]
    body = json.loads(json.dumps(default_checklist))
    for k, v in patch.items():
        if k.startswith("criteria_"):
            body["criteria"][int(k.split("_")[1])].update(v)
        else:
            body[k] = v
    r = client.post("/api/checklists", json=body)
    assert r.status_code == 400
    assert client.get("/api/checklists/active").json()["id"] == before


def test_numeric_strings_are_coerced(client, default_checklist):
    default_checklist["pass_threshold"] = "70"
    default_checklist["criteria"][0]["weight"] = "10"
    c = client.post("/api/checklists", json=default_checklist).json()
    assert c["pass_threshold"] == 70.0 and c["criteria"][0]["weight"] == 10.0


def test_manual_points_non_numeric_is_400(evaluated):
    ev = latest_eval(evaluated, "t-ru-001")
    ap = evaluated.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": "consultation",
                                              "comment": "x"}).json()
    r = evaluated.post(f"/api/appeals/{ap['id']}/decision",
                       json={"decision": "change", "value": 15, "manual_points": "abc", "reason": "r"})
    assert r.status_code == 400


# ---------- оценки и results.json ----------

def test_failed_rerun_does_not_hide_done_result(evaluated):
    """Упавшая/прерванная переоценка подменяла готовый результат на status=error в results.json."""
    ev = latest_eval(evaluated, "t-ru-001")
    db.put("evaluations", {"id": "ev_broken", "dialog_id": "t-ru-001", "checklist_id": ev["checklist_id"],
                           "checklist_version": 1, "status": "error", "error": "boom"})
    res = evaluated.get("/api/results", params={"dataset": "TEST"}).json()
    rec = next(r for r in res["document"]["results"] if r["conversation_id"] == "t-ru-001")
    assert rec["status"] == "ok"


def test_run_creates_records_before_response(loaded):
    """Карточка сразу после «Оценить» видела прошлую оценку как готовую."""
    job = loaded.post("/api/evaluations/run", json={"dialog_ids": ["t-ru-001"]}).json()
    evs = [e for e in db.all("evaluations") if e.get("job_id") == job["id"]]
    assert len(evs) == 1
    wait_job(loaded, job["id"])


def test_reviewer_decision_replaces_rationale(evaluated):
    ev = latest_eval(evaluated, "t-ru-001")
    ap = evaluated.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": "consultation",
                                              "comment": "x"}).json()
    evaluated.post(f"/api/appeals/{ap['id']}/decision",
                   json={"decision": "change", "value": 5, "reason": "ответ неполный"})
    res = evaluated.get("/api/results", params={"dataset": "TEST"}).json()
    rec = next(r for r in res["document"]["results"] if r["conversation_id"] == "t-ru-001")
    c = next(x for x in rec["criteria"] if x["criterion_id"] == "consultation")
    assert c["value"] == 5 and "решение проверяющего" in c["rationale"] and "ответ неполный" in c["rationale"]


def test_validate_results_checks_value_domain():
    rec = {"conversation_id": "x", "status": "ok", "criteria": [
        {"criterion_id": k, "value": v} for k, v in
        [("contact", None), ("discovery", None), ("identification", True), ("consultation", 7),
         ("empathy", True), ("dialogue", 10), ("closure", True), ("critical", False)]]}
    problems = app_module.validate_results({"results": [rec]}, ["x"])
    assert any("contact" in p for p in problems) and any("consultation" in p for p in problems)
    assert len(problems) == 2


def test_stats_passed_is_none_without_threshold(evaluated):
    assert evaluated.get("/api/stats").json()["passed"] is None


def test_eval_brief_has_checklist_version(evaluated):
    ev = evaluated.get("/api/dialogs/t-ru-001").json()["evaluations"][0]
    assert ev["checklist_version"] == 1


# ---------- эталон ----------

def test_ground_truth_in_results_json_format(evaluated):
    """Эталон в формате results.json (criteria списком, коды организаторов, null = N/A) давал 500."""
    doc = {"results": [{"conversation_id": "t-ru-001", "status": "ok", "score": {"final_score": "87.5"},
                        "criteria": [{"criterion_id": "dialogue", "value": 10},
                                     {"criterion_id": "closure", "value": True},
                                     {"criterion_id": "discovery", "value": None}]}]}
    assert evaluated.post("/api/ground-truth", json=doc).json()["saved"] == 1
    acc = evaluated.get("/api/accuracy").json()
    assert acc["criteria"]["management"]["n"] == 1 and acc["criteria"]["closing"]["n"] == 1
    assert acc["criteria"]["needs"]["n"] == 1
    row = next(d for d in acc["dialogs"] if d["conversation_id"] == "t-ru-001")
    assert row["gt_total"] == 87.5
