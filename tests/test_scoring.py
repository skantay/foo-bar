"""Формула итога: порядок расчёта из PDF организаторов."""
import pytest

import scoring


def binary(cid, weight, **kw):
    return {"id": cid, "name": cid, "type": "binary", "weight": weight, **kw}


def scale(cid, weight, steps, **kw):
    return {"id": cid, "name": cid, "type": "scale", "weight": weight,
            "scale": {"min": min(steps), "max": max(steps), "steps": steps}, **kw}


def checklist(*criteria, **kw):
    return {"name": "t", "criteria": list(criteria), "rounding": {"digits": 2, "mode": "half_up"}, **kw}


def ans(value=None, na=False, **kw):
    return {"value": value, "na": na, **kw}


def test_all_yes_is_100_and_all_no_is_0():
    cl = checklist(binary("a", 10), binary("b", 30))
    assert scoring.compute(cl, {"a": ans(1), "b": ans(1)})["total"] == 100
    assert scoring.compute(cl, {"a": ans(0), "b": ans(0)})["total"] == 0


def test_weights_are_proportional():
    cl = checklist(binary("a", 10), binary("b", 30))
    assert scoring.compute(cl, {"a": ans(1), "b": ans(0)})["total"] == 25


def test_na_weight_is_redistributed():
    cl = checklist(binary("a", 10), binary("b", 30, na_allowed=True))
    r = scoring.compute(cl, {"a": ans(1), "b": ans(na=True)})
    assert r["total"] == 100
    row = {x["criterion_id"]: x for x in r["criteria"]}
    assert row["a"]["effective_weight"] == 100 and row["b"]["na"]


def test_scale_steps_give_proportional_points():
    # шаг 15 из 20 при весе 20 + «Да» при весе 10: (0.75·20 + 10) / 30 = 83.33
    cl = checklist(scale("c", 20, [0, 5, 10, 15, 20]), binary("a", 10))
    assert scoring.compute(cl, {"c": ans(15), "a": ans(1)})["total"] == 83.33


def test_top_step_gives_full_weight():
    cl = checklist(scale("m", 15, [0, 4, 7, 10]))
    assert scoring.compute(cl, {"m": ans(10)})["total"] == 100
    assert scoring.compute(cl, {"m": ans(7)})["total"] == 70


def test_all_na_gives_no_total():
    cl = checklist(binary("a", 10, na_allowed=True))
    assert scoring.compute(cl, {"a": ans(na=True)})["total"] is None


@pytest.mark.parametrize("digits,expected", [(0, 67), (1, 66.7), (2, 66.67)])
def test_rounding_digits(digits, expected):
    cl = checklist(binary("a", 10), binary("b", 20), rounding={"digits": digits, "mode": "half_up"})
    assert scoring.compute(cl, {"a": ans(0), "b": ans(1)})["total"] == expected


def test_round_half_up():
    assert scoring.round_score(0.125, {"digits": 2, "mode": "half_up"}) == 0.13
    assert scoring.round_score(0.125, {"digits": 2, "mode": "half_even"}) == 0.12


def test_flag_has_no_points_and_is_reported_separately():
    flag = {"id": "g", "name": "g", "type": "flag", "weight": 0}
    cl = checklist(binary("a", 10), flag)
    r = scoring.compute(cl, {"a": ans(1), "g": ans(1)})
    assert r["total"] == 100
    assert r["critical_errors"] == ["g"]
    assert scoring.compute(cl, {"a": ans(1), "g": ans(0)})["critical_errors"] == []


def test_critical_mode_zero_zeroes_total():
    flag = {"id": "g", "name": "g", "type": "flag", "weight": 0}
    cl = checklist(binary("a", 10), flag, critical_mode="zero")
    r = scoring.compute(cl, {"a": ans(1), "g": ans(1)})
    assert r["total"] == 0 and r["raw_total"] == 100


def test_pass_threshold():
    cl = checklist(binary("a", 10), binary("b", 10), pass_threshold=60)
    assert scoring.compute(cl, {"a": ans(1), "b": ans(1)})["passed"] is True
    assert scoring.compute(cl, {"a": ans(1), "b": ans(0)})["passed"] is False
    assert scoring.compute(checklist(binary("a", 10)), {"a": ans(1)})["passed"] is None


def test_sections_are_scored_separately():
    cl = checklist(binary("a", 10, section_id="s1"), binary("b", 10, section_id="s2"),
                   sections=[{"id": "s1", "name": "S1"}, {"id": "s2", "name": "S2"}])
    secs = {s["id"]: s["score"] for s in scoring.compute(cl, {"a": ans(1), "b": ans(0)})["sections"]}
    assert secs == {"s1": 100, "s2": 0}


def test_manual_ratio_overrides_value():
    cl = checklist(binary("a", 10), binary("b", 10))
    r = scoring.compute(cl, {"a": ans(1, manual_ratio=0.5), "b": ans(1)})
    assert r["total"] == 75


@pytest.mark.parametrize("value,expected", [(13, 15), (12, 10), (2.4, 0), (25, 20), (-3, 0)])
def test_normalize_snaps_to_nearest_step(value, expected):
    assert scoring.normalize_value(scale("c", 20, [0, 5, 10, 15, 20]), value) == expected


def test_normalize_binary_clamps_and_rounds():
    assert scoring.normalize_value(binary("a", 1), 0.7) == 1
    assert scoring.normalize_value(binary("a", 1), 5) == 1
    assert scoring.normalize_value(binary("a", 1), None) is None


def test_default_checklist_matches_pdf(default_checklist):
    crit = {c["id"]: c for c in default_checklist["criteria"]}
    assert len(crit) == 8
    assert sum(c["weight"] for c in crit.values() if c["type"] != "flag") == 100
    assert crit["consultation"]["scale"]["steps"] == [0, 5, 10, 15, 20]
    assert crit["management"]["scale"]["steps"] == [0, 4, 7, 10]
    assert crit["gross_errors"]["type"] == "flag"
    assert default_checklist["rounding"]["digits"] == 2
    # N/A разрешён ровно там, где он указан в PDF
    assert {k for k, c in crit.items() if c["na_allowed"]} == {"needs", "identification", "consultation", "closing"}
