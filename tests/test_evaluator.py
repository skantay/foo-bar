"""Конвейер оценки без модели: подготовка промпта, проверка ответа, голосование, адаптивные прогоны."""
import asyncio
import copy

import pytest

import evaluator


@pytest.fixture
def dialog(dialogs_payload):
    rec = dialogs_payload["data"]["records"][0]
    return {"id": rec["conversation_id"], "channel": rec["channel"], "language": rec["language"],
            "transcript": rec["transcript"]}


def raw_for(checklist, **overrides):
    """Корректный ответ модели: «Да» / верхняя ступень по всем критериям, грубых ошибок нет."""
    crit = []
    for c in checklist["criteria"]:
        top = max(c["scale"]["steps"]) if c["type"] == "scale" else (0 if c["type"] == "flag" else 1)
        item = {"criterion_id": c["id"], "na": False, "na_reason": "", "value": top,
                "explanation": f"{c['name']} — Да. Основание — [3].",
                "evidence": [{"turn": 3, "quote": "Меня зовут Алина"}],
                "checked_range": {"from_turn": 3, "to_turn": 5},
                "critical_error": False, "critical_reason": "", "confidence": "high"}
        item.update(overrides.get(c["id"], {}))
        crit.append(item)
    return {"summary": "ok", "criteria": crit}


# ---------- метки времени ----------

@pytest.mark.parametrize("ts,expected", [("01:15", 75), ("1:02:03", 3723), ("00:00", 0)])
def test_parse_mmss(ts, expected):
    assert evaluator.parse_ts(ts) == expected


def test_parse_iso_with_7_digit_fraction():
    assert evaluator.parse_ts("2026-11-03T15:49:13.1900670+00:00") is not None
    assert evaluator.parse_ts("не время") is None


def test_timing_facts_first_response_and_long_wait(dialog):
    facts = " ".join(evaluator.timing_facts(dialog, 180))
    # эскалация [2] в 00:30, оператор [3] в 01:10 → 0:40, в пределах порога
    assert "[3] через 0:40 после [2]" in facts and "в пределах порога" in facts
    # клиент [4] в 01:20, ответ оператора [5] в 05:00 → 3:40 > 3:00
    assert "[4]→[5] 3:40" in facts


def test_timing_facts_detects_exceeded_threshold(dialog):
    d = copy.deepcopy(dialog)
    d["transcript"][3]["timestamp"] = "04:00"
    assert "ПРЕВЫШЕН" in " ".join(evaluator.timing_facts(d, 180))


def test_timing_facts_unanswered_client(dialogs_payload):
    rec = dialogs_payload["data"]["records"][1]
    facts = " ".join(evaluator.timing_facts({"id": "x", "transcript": rec["transcript"]}, 180))
    assert "осталось без ответа" in facts


def test_timing_facts_without_operator():
    d = {"id": "x", "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "a"},
                                   {"timestamp": "00:05", "speaker": "bot", "text": "b"}]}
    assert evaluator.timing_facts(d, 180) == ["Оператор в диалоге не отвечал."]


# ---------- промпт и схема ----------

def test_render_checklist_lists_steps_na_and_flag(default_checklist):
    text = evaluator.render_checklist(default_checklist)
    assert "criterion_id: consultation" in text and "20 — Все существенные вопросы закрыты" in text
    assert "N/A: разрешён" in text and "N/A: не разрешён" in text
    assert "flag" in text
    assert default_checklist["guidelines"].split("\n")[0] in text


def test_render_transcript_numbers_turns(dialog):
    text = evaluator.render_transcript(dialog, 180)
    assert "[0] 00:00 CLIENT:" in text and "[3] 01:10 OPERATOR:" in text
    assert "ФАКТЫ О ВРЕМЕНИ" in text


def test_output_schema_restricts_criterion_ids(default_checklist):
    schema = evaluator.output_schema(default_checklist)
    ids = schema["properties"]["criteria"]["items"]["properties"]["criterion_id"]["enum"]
    assert ids == [c["id"] for c in default_checklist["criteria"]]


# ---------- проверка ответа модели ----------

def test_postprocess_clean_answer(default_checklist, dialog):
    answers, warnings = evaluator.postprocess(default_checklist, dialog, raw_for(default_checklist))
    assert warnings == []
    assert answers["consultation"]["value"] == 20
    assert answers["contact"]["evidence"][0]["verified"] is True


def test_postprocess_rejects_na_where_not_allowed(default_checklist, dialog):
    raw = raw_for(default_checklist, contact={"na": True, "value": 0})
    answers, warnings = evaluator.postprocess(default_checklist, dialog, raw)
    assert answers["contact"]["na"] is False
    assert any("N/A" in w for w in warnings)


def test_postprocess_snaps_scale_value(default_checklist, dialog):
    answers, warnings = evaluator.postprocess(default_checklist, dialog, raw_for(default_checklist, consultation={"value": 13}))
    assert answers["consultation"]["value"] == 15
    assert any("ступени" in w for w in warnings)


def test_postprocess_drops_bad_turns_and_fixes_quotes(default_checklist, dialog):
    raw = raw_for(default_checklist, contact={"evidence": [{"turn": 99, "quote": "x"},
                                                           {"turn": 3, "quote": "выдуманная цитата"}]})
    answers, warnings = evaluator.postprocess(default_checklist, dialog, raw)
    ev = answers["contact"]["evidence"]
    assert len(ev) == 1 and ev[0]["turn"] == 3
    assert ev[0]["verified"] is False and ev[0]["quote"].startswith("Здравствуйте! Меня зовут Алина")
    assert any("[99]" in w for w in warnings)


def test_postprocess_missing_criterion_needs_review(default_checklist, dialog):
    raw = raw_for(default_checklist)
    raw["criteria"] = [c for c in raw["criteria"] if c["criterion_id"] != "empathy"]
    answers, warnings = evaluator.postprocess(default_checklist, dialog, raw)
    assert answers["empathy"]["needs_review"] is True
    assert any("empathy" in w for w in warnings)


# ---------- голосование ----------

def test_vote_majority_and_agreement(default_checklist):
    runs = []
    for v in (1, 1, 0):
        runs.append({c["id"]: {"value": v if c["id"] == "contact" else 1, "na": False, "confidence": "high",
                               "needs_review": False} for c in default_checklist["criteria"]})
    out = evaluator.vote(default_checklist, runs)
    assert out["contact"]["value"] == 1 and out["contact"]["agreement"] == 0.67
    assert out["contact"]["confidence"] == "medium"
    assert out["empathy"]["agreement"] == 1


def test_vote_three_way_scale_tie_takes_median(default_checklist):
    base = {c["id"]: {"value": 1, "na": False, "confidence": "high", "needs_review": False}
            for c in default_checklist["criteria"]}
    runs = [copy.deepcopy(base) for _ in range(3)]
    for r, v in zip(runs, (20, 10, 5)):
        r["consultation"]["value"] = v
    out = evaluator.vote(default_checklist, runs)
    assert out["consultation"]["value"] == 10
    assert out["consultation"]["needs_review"] is True


# ---------- адаптивные прогоны ----------

def run_with_fake_model(monkeypatch, checklist, dialog, raws):
    calls = []

    async def fake_call(d, cl):
        calls.append(d)
        return {"raw": raws[len(calls) - 1], "model": "fake", "usage": {"input_tokens": 10, "output_tokens": 5,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}

    monkeypatch.setattr(evaluator, "backend", lambda: "api")
    monkeypatch.setattr(evaluator, "call_llm", fake_call)
    monkeypatch.setattr(evaluator, "SAMPLES", 3)
    return asyncio.run(evaluator.evaluate(dialog, checklist)), calls


def test_unanimous_first_runs_skip_third(monkeypatch, default_checklist, dialog):
    raws = [raw_for(default_checklist)] * 3
    res, calls = run_with_fake_model(monkeypatch, default_checklist, dialog, raws)
    assert len(calls) == 2 and res["samples"] == 2
    assert res["warnings"] == []


def test_disagreement_triggers_third_run(monkeypatch, default_checklist, dialog):
    raws = [raw_for(default_checklist), raw_for(default_checklist, contact={"value": 0}), raw_for(default_checklist)]
    res, calls = run_with_fake_model(monkeypatch, default_checklist, dialog, raws)
    assert len(calls) == 3
    assert res["answers"]["contact"]["value"] == 1 and res["answers"]["contact"]["agreement"] == 0.67


def test_model_receives_masked_dialog(monkeypatch, default_checklist, dialog):
    res, calls = run_with_fake_model(monkeypatch, default_checklist, dialog, [raw_for(default_checklist)] * 3)
    sent = " ".join(t["text"] for t in calls[0]["transcript"])
    assert "+7 701 123 45 67" not in sent and "Алина" not in sent
    assert res["pii"]["counts"]["PHONE"] == 1


def test_mock_backend_produces_all_criteria(default_checklist, dialog):
    res = asyncio.run(evaluator.evaluate(dialog, default_checklist))
    assert set(res["answers"]) == {c["id"] for c in default_checklist["criteria"]}
    assert res["model"] == "mock"
