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
                                 "transcript": [{"timestamp": "", "speaker": "operator", "text": "x"}]}]})
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


def test_official_score_not_zeroed_by_critical_flag(evaluated, default_checklist):
    """Appendix: final_score выводится из value критериев и не обнуляется флагом грубой ошибки."""
    base = evaluated.get("/api/results", params={"dataset": "TEST"}).json()["document"]["results"]
    ev = latest_eval(evaluated, "t-ru-001")
    ap = evaluated.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": "contact",
                                              "comment": "x"}).json()
    evaluated.post(f"/api/appeals/{ap['id']}/decision",
                   json={"decision": "change", "value": 1, "reason": "представился"})
    ap = evaluated.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": "gross_errors",
                                              "comment": "x"}).json()
    evaluated.post(f"/api/appeals/{ap['id']}/decision", json={"decision": "change", "value": 1, "reason": "оскорбление"})
    cl = evaluated.get("/api/checklists/active").json()
    db.put("checklists", {**cl, "critical_mode": "zero"})
    app_module._cl_cache.clear()
    rec = next(r for r in evaluated.get("/api/results", params={"dataset": "TEST"}).json()["document"]["results"]
               if r["conversation_id"] == "t-ru-001")
    contact = next(c for c in rec["criteria"] if c["criterion_id"] == "contact")
    old = next(r for r in base if r["conversation_id"] == "t-ru-001")
    assert contact["value"] is True and rec["score"]["critical_flag"] is True
    old_contact = next(c for c in old["criteria"] if c["criterion_id"] == "contact")["value"]
    if old_contact is True:  # те же value → тот же итог, несмотря на режим zero
        assert rec["score"]["final_score"] == old["score"]["final_score"]
    assert rec["score"]["final_score"] > 0


# ---------- валидация загружаемых файлов ----------

SPAM = """Заказ: 7479289
Сайт недоступен по старому адресу? Смотрите актуальный адрес на: https://darkstore.contact
==========================================
↓↓↓↓ Ваш заказ: ↓↓↓↓"""


@pytest.mark.parametrize("name,content", [
    ("spam.txt", SPAM.encode()),                                     # случайный текст без ролей
    ("pic.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe"),      # бинарный файл
    ("empty.txt", b""),
    ("num.json", b"42"),
    ("str.json", b'"hello"'),
    ("empty.json", b'{"records": []}'),
    ("bad_role.json", json.dumps({"records": [{"conversation_id": "x", "transcript": [
        {"speaker": "hacker", "text": "hi"}, {"speaker": "operator", "text": "hello"}]}]}).encode()),
    ("empty_transcript.json", json.dumps({"records": [{"conversation_id": "x", "transcript": []}]}).encode()),
    ("text_obj.json", json.dumps({"records": [{"conversation_id": "x", "transcript": [
        {"speaker": "operator", "text": {"a": 1}}]}]}).encode()),
    ("no_operator.txt", "client: привет\nclient: ау".encode()),
])
def test_upload_rejects_garbage(client, name, content):
    r = client.post("/api/dialogs/upload", files={"file": (name, content, "application/octet-stream")})
    assert r.status_code == 400, r.text
    assert client.get("/api/dialogs").json() == []


def test_bad_record_does_not_half_import(client):
    """Плохая запись в середине файла не должна оставлять в базе первые записи."""
    good = {"conversation_id": "ok-1", "transcript": [{"speaker": "operator", "text": "Здравствуйте"}]}
    bad = {"conversation_id": "bad-1", "transcript": [{"speaker": "???", "text": "x"}]}
    r = client.post("/api/dialogs/upload", files={"file": ("d.json", json.dumps({"records": [good, bad]}), "application/json")})
    assert r.status_code == 400 and "bad-1" in r.json()["detail"]
    assert client.get("/api/dialogs").json() == []


def test_plain_text_continuation_lines(client):
    txt = "[00:01] client: Здравствуйте\nу меня вопрос по выводу\n[00:30] operator: Добрый день, меня зовут Ая"
    r = client.post("/api/dialogs/upload", files={"file": ("chat.txt", txt.encode(), "text/plain")})
    assert r.status_code == 200
    t = client.get("/api/dialogs/chat").json()["transcript"]
    assert [x["speaker"] for x in t] == ["client", "operator"] and t[0]["text"].endswith("по выводу")


def test_sync_stays_lenient_for_dialog_without_operator(client, monkeypatch):
    """Контрольный набор не должен отклоняться целиком из-за одного диалога без оператора."""
    payload = {"isSuccess": True, "data": {"dataset_group_id": "CTRL", "records": [
        {"conversation_id": "c1", "transcript": [{"speaker": "client", "text": "hi"}]}]}}

    class R:
        status_code = 200
        def json(self): return payload

    class C:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url): return R()

    monkeypatch.setattr(app_module.httpx, "AsyncClient", C)
    assert client.post("/api/dialogs/sync", json={"group_id": "CTRL"}).json()["imported"] == 1


def test_organizers_file_with_dialog_without_operator_is_accepted(client):
    """Файл набора организаторов (запасной путь, если API недоступен) не отклоняется из-за чата без оператора."""
    payload = {"isSuccess": True, "data": {"dataset_group_id": "CTRL", "records": [
        {"conversation_id": "c1", "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "hi"},
                                                  {"timestamp": "06:00", "speaker": "system", "text": "Чат закрыт"}]}]}}
    r = client.post("/api/dialogs/upload", files={"file": ("ctrl.json", json.dumps(payload), "application/json")})
    assert r.status_code == 200 and r.json()["dataset"] == "CTRL"
