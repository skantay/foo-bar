"""Сквозные сценарии через HTTP API на мок-оценщике."""
import io

import pytest

import app as app_module
from conftest import FIXTURES, latest_eval, wait_job


def active(client):
    return client.get("/api/checklists/active").json()


# ---------- запуск и данные ----------

def test_health_reports_mock(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["backend"] == "mock" and h["llm"] is False


def test_startup_seeds_default_checklist(client):
    cls = client.get("/api/checklists").json()
    assert len(cls) == 1 and cls[0]["active"] and cls[0]["version"] == 1
    assert len(cls[0]["criteria"]) == 8


def test_index_and_static_served(client):
    assert "<cqa-app" in client.get("/").text
    assert client.get("/static/js/app.js").status_code == 200


def test_upload_organizers_format(loaded):
    dialogs = {d["id"]: d for d in loaded.get("/api/dialogs").json()}
    assert set(dialogs) == {"t-ru-001", "t-kz-001", "t-call-001"}
    assert dialogs["t-call-001"]["channel"] == "voice call" and dialogs["t-call-001"]["audio_url"]
    assert dialogs["t-ru-001"]["operator_turns"] == 2
    assert loaded.get("/api/datasets").json() == [{"id": "TEST", "count": 3}]


def test_reupload_does_not_duplicate(loaded):
    loaded.post("/api/dialogs/upload", files={"file": ("d.json", (FIXTURES / "dialogs.json").read_bytes())})
    assert len(loaded.get("/api/dialogs").json()) == 3


def test_upload_plain_text_transcript(client):
    txt = "[00:01] клиент: Здравствуйте\n[00:20] оператор: Добрый день, меня зовут Ира\nбот: служебное"
    r = client.post("/api/dialogs/upload", files={"file": ("call-42.txt", io.BytesIO(txt.encode()), "text/plain")})
    assert r.status_code == 200
    d = client.get("/api/dialogs/call-42").json()
    assert [t["speaker"] for t in d["transcript"]] == ["client", "operator", "bot"]
    assert d["transcript"][1]["timestamp"] == "00:20"


def test_upload_rejects_bad_record(client):
    r = client.post("/api/dialogs/upload", files={"file": ("bad.json", b'[{"foo": 1}]')})
    assert r.status_code == 400


def test_sync_requires_source_url(client, monkeypatch):
    monkeypatch.setattr(app_module, "SOURCE_URL", "")
    r = client.post("/api/dialogs/sync", json={"group_id": "DEV"})
    assert r.status_code == 400 and "CQA_SOURCE_URL" in r.json()["detail"]


def test_source_url_substitutes_group_id(monkeypatch):
    monkeypatch.setattr(app_module, "SOURCE_URL", "https://x.invalid/run?api-version=1&group_id=OLD")
    url = app_module.source_url("CTRL")
    assert "group_id=CTRL" in url and "OLD" not in url and "api-version=1" in url


# ---------- чек-листы ----------

def minimal_checklist(**kw):
    return {"name": "Приветствие", "criteria": [{"id": "greeting", "name": "Приветствие", "type": "binary",
            "weight": 10, "description": "Да — поздоровался"}], **kw}


@pytest.mark.parametrize("body,msg", [
    ({"name": "", "criteria": []}, "название"),
    ({"name": "x", "criteria": []}, "критерий"),
    ({"name": "x", "criteria": [{"id": "a", "name": "a", "type": "binary", "weight": 0}]}, "вес"),
    ({"name": "x", "criteria": [{"id": "a", "name": "a", "type": "scale", "weight": 5, "scale": {"steps": [5]}}]}, "шкала"),
    ({"name": "x", "criteria": [{"id": "g", "name": "g", "type": "flag", "weight": 0}]}, "с весом"),
    ({"name": "x", "criteria": [{"id": "a", "name": "a", "type": "binary", "weight": 1},
                                {"id": "a", "name": "b", "type": "binary", "weight": 1}]}, "id"),
])
def test_checklist_validation(client, body, msg):
    r = client.post("/api/checklists", json=body)
    assert r.status_code == 400 and msg in r.json()["detail"].lower()


def test_new_checklist_is_new_family_and_active(client):
    old = active(client)
    new = client.post("/api/checklists", json=minimal_checklist()).json()
    assert new["family_id"] != old["family_id"] and new["version"] == 1 and new["active"]
    assert active(client)["id"] == new["id"]
    assert sum(c["active"] for c in client.get("/api/checklists").json()) == 1


def test_saving_same_family_bumps_version(client):
    v1 = active(client)
    body = {k: v for k, v in v1.items() if k not in ("id", "created_at", "active", "version")}
    v2 = client.post("/api/checklists", json=body).json()
    assert v2["version"] == 2 and v2["family_id"] == v1["family_id"]
    assert client.get(f"/api/checklists/{v1['id']}").json()["criteria"] == v1["criteria"]  # старая версия не меняется


def test_activate_switches_active(client):
    v1 = active(client)
    client.post("/api/checklists", json=minimal_checklist())
    client.post(f"/api/checklists/{v1['id']}/activate")
    assert active(client)["id"] == v1["id"]


def test_scale_steps_normalised_on_save(client):
    body = minimal_checklist(criteria=[{"id": "s", "name": "s", "type": "scale", "weight": 10,
                                        "description": "d", "scale": {"steps": [10, 0, 5]}}])
    s = client.post("/api/checklists", json=body).json()["criteria"][0]["scale"]
    assert s["steps"] == [0, 5, 10] and s["min"] == 0 and s["max"] == 10


# ---------- оценка ----------

def test_run_evaluation_job(evaluated):
    rows = evaluated.get("/api/dialogs").json()
    assert all(r["evaluation"]["status"] == "done" for r in rows)
    assert all(len(r["evaluation"]["marks"]) == 8 for r in rows)
    e = latest_eval(evaluated, "t-ru-001")
    assert set(e["answers"]) == {c["id"] for c in e["checklist"]["criteria"]}
    assert 0 <= e["final_result"]["total"] <= 100
    assert e["timing_facts"] and e["checklist_version"] == 1


def test_only_missing_skips_evaluated(evaluated):
    r = evaluated.post("/api/evaluations/run", json={"only_missing": True})
    assert r.status_code == 400  # всё уже оценено этой версией


def test_new_version_is_evaluated_separately(evaluated):
    v1 = active(evaluated)
    body = {k: v for k, v in v1.items() if k not in ("id", "created_at", "active", "version")}
    v2 = evaluated.post("/api/checklists", json=body).json()
    job = evaluated.post("/api/evaluations/run", json={"only_missing": True}).json()
    assert job["total"] == 3 and job["checklist_version"] == 2
    wait_job(evaluated, job["id"])
    d = evaluated.get("/api/dialogs/t-ru-001").json()
    assert {e["checklist_id"] for e in d["evaluations"]} == {v1["id"], v2["id"]}


def test_run_selected_dialogs(loaded):
    job = loaded.post("/api/evaluations/run", json={"dialog_ids": ["t-kz-001"]}).json()
    assert job["total"] == 1
    wait_job(loaded, job["id"])
    assert loaded.get("/api/dialogs/t-ru-001").json()["evaluations"] == []


def test_stats_after_evaluation(evaluated):
    s = evaluated.get("/api/stats").json()
    assert s["evaluated"] == 3 and s["dialogs"] == 3
    assert "gross_errors" not in s["criteria"]  # флаг без баллов не участвует в выполнении критериев
    assert set(s["by_language"]) == {"RU", "KZ"}


# ---------- апелляции ----------

def appeal(client, eid, cid="contact", comment="Не согласен"):
    return client.post("/api/appeals", json={"evaluation_id": eid, "criterion_id": cid, "comment": comment})


def test_appeal_lifecycle_change(evaluated):
    e = latest_eval(evaluated, "t-ru-001")
    a = appeal(evaluated, e["id"]).json()
    assert a["status"] == "open"
    assert evaluated.get(f"/api/evaluations/{e['id']}").json()["case_status"] == "disputed"
    assert appeal(evaluated, e["id"]).status_code == 409  # вторая открытая по тому же критерию

    new_value = 0 if e["answers"]["contact"]["value"] else 1
    r = evaluated.post(f"/api/appeals/{a['id']}/decision",
                       json={"decision": "change", "value": new_value, "reason": "см. реплику [3]"}).json()
    assert r["status"] == "resolved" and r["new_value"] == new_value
    e2 = evaluated.get(f"/api/evaluations/{e['id']}").json()
    assert e2["case_status"] == "reviewed"
    assert e2["answers"]["contact"]["value"] == e["answers"]["contact"]["value"]  # исходная оценка ИИ сохранена
    assert e2["overrides"]["contact"]["value"] == new_value
    assert r["total_after"] == e2["final_result"]["total"]


def test_appeal_confirm_keeps_score(evaluated):
    e = latest_eval(evaluated, "t-kz-001")
    a = appeal(evaluated, e["id"]).json()
    r = evaluated.post(f"/api/appeals/{a['id']}/decision", json={"decision": "confirm", "reason": "верно"}).json()
    assert r["total_before"] == r["total_after"]


@pytest.mark.parametrize("body", [{"decision": "change", "value": 1}, {"decision": "nope", "reason": "x"}])
def test_decision_validation(evaluated, body):
    e = latest_eval(evaluated, "t-ru-001")
    a = appeal(evaluated, e["id"]).json()
    assert evaluated.post(f"/api/appeals/{a['id']}/decision", json=body).status_code == 400


def test_appeal_requires_comment(evaluated):
    e = latest_eval(evaluated, "t-ru-001")
    assert appeal(evaluated, e["id"], comment=" ").status_code == 400


def test_reviewer_sets_only_allowed_values(evaluated):
    """Ручных баллов нет: проверяющий выбирает только значение критерия (Да / Нет / ступень)."""
    e = latest_eval(evaluated, "t-call-001")
    a = appeal(evaluated, e["id"]).json()
    r = evaluated.post(f"/api/appeals/{a['id']}/decision",
                       json={"decision": "change", "value": 1, "manual_points": 3, "reason": "выполнен"}).json()
    assert "manual_points" not in r
    row = next(x for x in evaluated.get(f"/api/evaluations/{e['id']}").json()["final_result"]["criteria"]
               if x["criterion_id"] == "contact")
    assert row["ratio"] == 1 and row["points"] == row["effective_weight"]


# ---------- эталон, точность, экспорт ----------

def test_accuracy_against_uploaded_ground_truth(evaluated):
    e = latest_eval(evaluated, "t-ru-001")
    labels = {c["id"]: app_module.answer_label(c, e["answers"][c["id"]]) for c in e["checklist"]["criteria"]}
    assert evaluated.post("/api/ground-truth", json=[{"conversation_id": "t-ru-001", "criteria": labels}]).json()["saved"] == 1
    acc = evaluated.get("/api/accuracy").json()
    assert acc["exact_match"] == 1 and acc["total_mae"] == 0 and acc["n_dialogs"] == 1

    wrong = dict(labels, contact="Нет" if labels["contact"] == "Да" else "Да")
    evaluated.post("/api/ground-truth", json={"records": [{"conversation_id": "t-ru-001", "criteria": wrong}]})
    acc = evaluated.get("/api/accuracy").json()
    assert acc["criteria"]["contact"]["accuracy"] == 0
    assert acc["dialogs"][0]["mismatches"][0]["criterion_id"] == "contact"


def test_ground_truth_from_reviewed_evaluation(evaluated):
    e = latest_eval(evaluated, "t-kz-001")
    assert evaluated.post(f"/api/ground-truth/from-evaluation/{e['id']}").json()["ok"]
    assert evaluated.get("/api/accuracy").json()["exact_match"] == 1


@pytest.mark.parametrize("v,crit_type,expected", [
    ("Да", "binary", {"na": False, "value": 1.0}), ("нет", "binary", {"na": False, "value": 0.0}),
    ("N/A", "binary", {"na": True, "value": None}), ("15", "scale", {"na": False, "value": 15.0}),
    ("13", "scale", {"na": False, "value": 15.0}), ("мусор", "binary", None),
])
def test_ground_truth_value_parsing(v, crit_type, expected):
    c = {"type": crit_type, "scale": {"min": 0, "max": 20, "steps": [0, 5, 10, 15, 20]}}
    assert app_module.gt_value(c, v) == expected


def test_export_json_and_csv(evaluated):
    data = evaluated.get("/api/export").json()
    assert len(data["records"]) == 3 and set(data["records"][0]["criteria"]) >= {"contact", "gross_errors"}
    csv = evaluated.get("/api/export?fmt=csv")
    assert csv.headers["content-type"].startswith("text/csv")
    header = csv.text.lstrip("﻿").splitlines()[0].split(",")
    assert header[:5] == ["conversation_id", "dataset", "language", "total", "has_gross_error"]
