"""Итоговый балл: шкалы с учётом веса, N/A перераспределяет вес, грубые ошибки отдельно."""
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal


def round_score(value: float, rounding: dict) -> float:
    digits = int(rounding.get("digits", 0))
    mode = ROUND_HALF_EVEN if rounding.get("mode") == "half_even" else ROUND_HALF_UP
    q = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(q, rounding=mode))


def value_range(criterion: dict) -> tuple[float, float]:
    if criterion.get("type") == "scale":
        s = criterion.get("scale") or {}
        steps = scale_steps(criterion)
        if steps:
            return min(steps), max(steps)
        return float(s.get("min", 0)), float(s.get("max", 1))
    return 0.0, 1.0


def scale_steps(criterion: dict) -> list[float]:
    """Допустимые ступени шкалы (например 0, 5, 10, 15, 20). Пусто — любое целое в диапазоне."""
    steps = (criterion.get("scale") or {}).get("steps") or []
    return sorted(float(x) for x in steps)


def is_flag(criterion: dict) -> bool:
    return criterion.get("type") == "flag"


def normalize_value(criterion: dict, value) -> float | None:
    """Приводит ответ к допустимому значению: 0/1 или ближайшая ступень шкалы."""
    if value is None:
        return None
    lo, hi = value_range(criterion)
    v = min(max(float(value), lo), hi)
    if criterion.get("type") != "scale":
        return float(round(v))
    steps = scale_steps(criterion)
    if steps:
        return min(steps, key=lambda s: (abs(s - v), -s))
    return float(round(v))


def compute(checklist: dict, answers: dict[str, dict]) -> dict:
    """answers: criterion_id -> {"value": float|None, "na": bool, "critical_error": bool}.

    Возвращает разбивку по критериям, секциям и итог (0–100).
    """
    rounding = checklist.get("rounding") or {"digits": 0, "mode": "half_up"}
    criteria = checklist["criteria"]
    scored = [c for c in criteria if not is_flag(c)]

    def applicable(c):
        a = answers.get(c["id"]) or {}
        return not a.get("na") and a.get("value") is not None

    def block(cs: list[dict]) -> dict:
        app_weight = sum(float(c["weight"]) for c in cs if applicable(c))
        rows, earned = [], 0.0
        for c in cs:
            a = answers.get(c["id"]) or {}
            lo, hi = value_range(c)
            if applicable(c) and app_weight > 0:
                eff = float(c["weight"]) / app_weight * 100
                ratio = (float(a["value"]) - lo) / (hi - lo) if hi > lo else 0.0
                pts = ratio * eff
                earned += pts
                rows.append({"criterion_id": c["id"], "weight": c["weight"],
                             "effective_weight": round(eff, 2), "ratio": round(ratio, 4),
                             "points": round(pts, 2), "na": False})
            else:
                rows.append({"criterion_id": c["id"], "weight": c["weight"],
                             "effective_weight": 0, "ratio": None, "points": 0, "na": True})
        score = round_score(earned, rounding) if app_weight > 0 else None
        return {"rows": rows, "score": score, "applicable_weight": app_weight}

    overall = block(scored)
    for c in criteria:
        if is_flag(c):
            a = answers.get(c["id"]) or {}
            overall["rows"].append({"criterion_id": c["id"], "weight": 0, "effective_weight": 0,
                                    "ratio": None, "points": 0, "na": False, "flag": True,
                                    "flagged": bool(a.get("value"))})
    sections = []
    for s in checklist.get("sections") or []:
        cs = [c for c in scored if c.get("section_id") == s["id"]]
        if cs:
            b = block(cs)
            sections.append({"id": s["id"], "name": s["name"], "score": b["score"],
                             "na": b["score"] is None})

    criticals = []
    for c in criteria:
        a = answers.get(c["id"]) or {}
        if (is_flag(c) and a.get("value")) or (c.get("critical") and a.get("critical_error")):
            criticals.append(c["id"])
    total = overall["score"]
    raw_total = total
    if criticals and checklist.get("critical_mode") == "zero" and total is not None:
        total = 0.0
    threshold = checklist.get("pass_threshold")
    passed = None
    if total is not None and threshold not in (None, ""):
        passed = total >= float(threshold) and not criticals
    return {
        "total": total,
        "raw_total": raw_total,
        "critical_errors": criticals,
        "passed": passed,
        "criteria": overall["rows"],
        "sections": sections,
    }
