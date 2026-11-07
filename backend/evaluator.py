"""Оценка диалога по чек-листу через Claude (или мок без ключа).

Точность важнее всего, поэтому:
- факты о времени ответа считаются в коде и передаются модели готовыми;
- диалог оценивается несколько раз (CQA_SAMPLES), по каждому критерию берётся ответ большинства;
- ответ модели сверяется с чек-листом и транскриптом (ступени шкал, N/A, номера реплик, цитаты).
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import statistics
import tempfile
import time
from collections import Counter
from datetime import datetime

import anthropic

import pii
from scoring import is_flag, normalize_value, scale_steps, value_range

log = logging.getLogger("cqa.evaluator")

MODEL = os.getenv("CQA_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("CQA_EFFORT", "high")
SAMPLES = max(1, int(os.getenv("CQA_SAMPLES", "3")))
# api — Anthropic API по ключу; cli — локальный `claude -p` (Claude Code); mock — заглушка; auto — что доступно
BACKEND = os.getenv("CQA_BACKEND", "auto")
CLI_PARALLEL = int(os.getenv("CQA_CLI_PARALLEL", "16"))
# маскировать персональные данные перед отправкой в модель (1/0)
PII_MASK = os.getenv("CQA_PII_MASK", "1") != "0"

# $ за 1M токенов: input, output, cache write (5m), cache read
PRICING = {
    "claude-fable-5-1": (10.0, 50.0, 12.5, 0.25),
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 2.5, 0.20),
    "claude-haiku-4-5": (1.0, 5.0, 1.25, 0.10),
}

SPEAKER_LABEL = {"operator": "OPERATOR", "client": "CLIENT", "bot": "BOT", "system": "SYSTEM"}

SYSTEM_RULES = """Ты — опытный эксперт отдела контроля качества (QC) контакт-центра брокерской компании Freedom Broker.
Ты оцениваешь работу ОПЕРАТОРА (живого сотрудника) в одном диалоге строго по выданному чек-листу. Твои оценки сравнят с эталонными оценками QC-экспертов, поэтому применяй формулировки критериев буквально и последовательно.

Как читать диалог:
- Оценивается только OPERATOR. BOT и SYSTEM — контекст: что клиент уже сообщил боту, эскалация, подтверждение личности, закрытие чата. Действия бота не засчитываются оператору; но детали, которые клиент сообщил боту, оператор должен учитывать.
- Диалоги на русском, казахском и английском; транскрипты звонков могут содержать ошибки распознавания — оценивай смысл.
- Блок «ФАКТЫ О ВРЕМЕНИ» посчитан программой по меткам времени — опирайся на него, а не пересчитывай сам.
- Персональные данные заменены метками: [ИМЯ_ОПЕРАТОРА_1], [ИМЯ_КЛИЕНТА_1], [PHONE_1], [IIN_1], [CARD_1], [IBAN_1], [EMAIL_1]. Метка значит, что в реплике было это значение: «Меня зовут [ИМЯ_ОПЕРАТОРА_1]» — оператор представился; [CARD_1] в реплике оператора — номер карты прозвучал. В цитатах сохраняй метки как есть.

Как отвечать по каждому критерию:
- binary: value 1 = «Да», 0 = «Нет».
- scale: value — ровно одна из перечисленных ступеней шкалы.
- flag (грубые ошибки, без баллов): value 1 = нарушение выявлено, 0 = не выявлено.
- na=true только если у критерия разрешён N/A и его условие действительно выполнено; тогда value=0 и в na_reason объясни, какое условие неприменимости выполнено. Если действие было нужно, но не сделано — это «Нет»/низкая ступень, а не N/A.
- evidence: реплики [N] и дословная короткая цитата из этой реплики на языке оригинала, которые подтверждают вывод (можно ссылаться и на SYSTEM-сообщения).
- Если действие пропущено — укажи в checked_range участок, где оно ожидалось (первая и последняя реплика), а в evidence — реплику, где его не оказалось. Если выполнено — checked_range это участок, где оно найдено.
- explanation: 1–3 предложения на русском в стиле «<Критерий> — <ответ>. <Почему>. Основание — [N] …».
- critical_error / critical_reason заполняй только для критериев с пометкой «может быть грубой ошибкой»; иначе false и "".
- confidence: high / medium / low — насколько однозначно применимо правило.

summary: 1–2 предложения на русском о сути обращения и работе оператора.
Не выдумывай реплики и цитаты."""


# ---------- подготовка промпта ----------

def render_checklist(checklist: dict) -> str:
    sections = {s["id"]: s["name"] for s in checklist.get("sections") or []}
    lines = [f"ЧЕК-ЛИСТ: {checklist['name']} (версия {checklist.get('version', 1)})"]
    if checklist.get("guidelines"):
        lines += ["", "ОБЩИЕ ПРАВИЛА ОЦЕНКИ:", checklist["guidelines"]]
    lines.append("\nКРИТЕРИИ:")
    for c in checklist["criteria"]:
        lines.append(f"\n### criterion_id: {c['id']}")
        lines.append(f"Название: {c['name']}")
        if c.get("section_id") in sections:
            lines.append(f"Секция: {sections[c['section_id']]}")
        lines.append(f"Правило: {c.get('description', '')}")
        if c.get("type") == "scale":
            s = c.get("scale") or {}
            labels = s.get("labels") or {}
            steps = scale_steps(c)
            if steps:
                lines.append("Тип: scale, допустимые ступени (выбери одну):")
                for v in sorted(steps, reverse=True):
                    key = str(int(v)) if v == int(v) else str(v)
                    lines.append(f"  {key} — {labels.get(key, '')}")
            else:
                lab = "; ".join(f"{k} — {v}" for k, v in labels.items())
                lines.append(f"Тип: scale, целое от {s.get('min', 0)} до {s.get('max', 1)}" + (f" ({lab})" if lab else ""))
        elif is_flag(c):
            lines.append("Тип: flag (1 — нарушение выявлено, 0 — не выявлено), баллов не даёт")
        else:
            lines.append("Тип: binary (1 — Да, 0 — Нет)")
        lines.append(f"N/A: {'разрешён. ' + c.get('na_rule', '') if c.get('na_allowed') else 'не разрешён'}")
        if c.get("critical"):
            lines.append(f"Может быть грубой ошибкой: {c.get('critical_rule', '')}")
    return "\n".join(lines)


def parse_ts(ts: str) -> float | None:
    """MM:SS / H:MM:SS → секунды от начала; ISO 8601 → epoch."""
    ts = (ts or "").strip()
    if re.fullmatch(r"\d+(:\d{1,2}){1,2}", ts):
        sec = 0
        for p in ts.split(":"):
            sec = sec * 60 + int(p)
        return float(sec)
    try:
        return datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", ts)).timestamp()
    except ValueError:
        return None


def mmss(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def timing_facts(dialog: dict, limit_s: int) -> list[str]:
    turns = dialog["transcript"]
    ts = [parse_ts(t.get("timestamp", "")) for t in turns]
    if sum(x is not None for x in ts) < 2:
        return ["Метки времени недоступны — скорость по времени не оценивается."]
    ops = [i for i, t in enumerate(turns) if t["speaker"] == "operator" and ts[i] is not None]
    if not ops:
        return ["Оператор в диалоге не отвечал."]
    facts = []
    first = ops[0]
    # точка отсчёта первой реакции: последнее системное событие (эскалация) или сообщение клиента до оператора
    ref = next((i for i in range(first - 1, -1, -1) if turns[i]["speaker"] == "system" and ts[i] is not None), None)
    if ref is None:
        ref = next((i for i in range(first) if turns[i]["speaker"] == "client" and ts[i] is not None), None)
    if ref is not None:
        d = ts[first] - ts[ref]
        verdict = "в пределах порога" if d <= limit_s else "ПРЕВЫШЕН порог"
        facts.append(f"Первая реакция оператора [{first}] через {mmss(d)} после [{ref}] — {verdict} {mmss(limit_s)}.")
    # ожидание ответа оператора после подключения
    long_waits, pending = [], None
    for i in range(first + 1, len(turns)):
        sp = turns[i]["speaker"]
        if ts[i] is None:
            continue
        if sp == "client" and pending is None:
            pending = i
        elif sp == "operator":
            if pending is not None:
                d = ts[i] - ts[pending]
                if d > limit_s:
                    long_waits.append(f"[{pending}]→[{i}] {mmss(d)}")
            pending = None
    if long_waits:
        facts.append(f"Ответ оператора на сообщение клиента дольше {mmss(limit_s)}: " + ", ".join(long_waits) + ".")
    else:
        facts.append(f"После подключения оператор отвечал клиенту быстрее {mmss(limit_s)}.")
    if pending is not None:
        facts.append(f"Последнее сообщение клиента [{pending}] осталось без ответа оператора.")
    return facts


def render_transcript(dialog: dict, limit_s: int) -> str:
    out = [f"Диалог {dialog['id']} · канал: {dialog.get('channel')} · язык набора: {dialog.get('language')}", ""]
    for i, t in enumerate(dialog["transcript"]):
        sp = SPEAKER_LABEL.get(t.get("speaker"), str(t.get("speaker")).upper())
        out.append(f"[{i}] {t.get('timestamp', '')} {sp}: {t.get('text', '')}")
    out += ["", "ФАКТЫ О ВРЕМЕНИ (посчитаны программой):"] + [f"- {f}" for f in timing_facts(dialog, limit_s)]
    return "\n".join(out)


def output_schema(checklist: dict) -> dict:
    ids = [c["id"] for c in checklist["criteria"]]
    item = {
        "type": "object",
        "properties": {
            "criterion_id": {"type": "string", "enum": ids},
            "na": {"type": "boolean"},
            "na_reason": {"type": "string"},
            "value": {"type": "number"},
            "explanation": {"type": "string"},
            "evidence": {"type": "array", "items": {
                "type": "object",
                "properties": {"turn": {"type": "integer"}, "quote": {"type": "string"}},
                "required": ["turn", "quote"], "additionalProperties": False}},
            "checked_range": {"type": "object",
                              "properties": {"from_turn": {"type": "integer"}, "to_turn": {"type": "integer"}},
                              "required": ["from_turn", "to_turn"], "additionalProperties": False},
            "critical_error": {"type": "boolean"},
            "critical_reason": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["criterion_id", "na", "na_reason", "value", "explanation", "evidence",
                     "checked_range", "critical_error", "critical_reason", "confidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"summary": {"type": "string"},
                       "criteria": {"type": "array", "items": item}},
        "required": ["summary", "criteria"],
        "additionalProperties": False,
    }


# ---------- проверка ответа ----------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[«»\"'“”.,!?…:;()\-—]", " ", s.lower())).strip()


def postprocess(checklist: dict, dialog: dict, raw: dict) -> tuple[dict, list[str]]:
    """Сверяет ответ модели с чек-листом и диалогом. Возвращает answers и предупреждения."""
    turns = dialog["transcript"]
    n = len(turns)
    by_id = {r.get("criterion_id"): r for r in raw.get("criteria", [])}
    answers, warnings = {}, []
    for c in checklist["criteria"]:
        r = by_id.get(c["id"])
        if r is None:
            warnings.append(f"{c['id']}: модель не вернула оценку")
            answers[c["id"]] = {"value": None, "na": True, "na_reason": "Не оценено моделью",
                                "explanation": "", "evidence": [], "checked_range": None,
                                "critical_error": False, "critical_reason": "", "confidence": "low",
                                "needs_review": True}
            continue
        na = bool(r.get("na"))
        if na and not c.get("na_allowed"):
            warnings.append(f"{c['id']}: модель поставила N/A, но он не разрешён — взято значение")
            na = False
        lo, _ = value_range(c)
        raw_value = r.get("value", lo)
        value = None if na else normalize_value(c, raw_value)
        if value is not None and raw_value is not None and float(raw_value) != value:
            warnings.append(f"{c['id']}: значение {raw_value} приведено к допустимой ступени {value:g}")
        evidence = []
        for e in r.get("evidence") or []:
            t = e.get("turn")
            if not isinstance(t, int) or not (0 <= t < n):
                warnings.append(f"{c['id']}: ссылка на несуществующую реплику [{t}] отброшена")
                continue
            quote = (e.get("quote") or "").strip()
            text = turns[t].get("text", "")
            verified = bool(quote) and _norm(quote) in _norm(text)
            if not verified:
                quote = text if len(text) <= 160 else text[:157] + "…"
            evidence.append({"turn": t, "quote": quote, "verified": verified,
                             "speaker": turns[t].get("speaker"), "timestamp": turns[t].get("timestamp")})
        cr = r.get("checked_range") or {}
        f, to = cr.get("from_turn"), cr.get("to_turn")
        checked = None
        if isinstance(f, int) and isinstance(to, int) and n:
            f, to = max(0, min(f, n - 1)), max(0, min(to, n - 1))
            checked = {"from_turn": min(f, to), "to_turn": max(f, to)}
        crit = bool(r.get("critical_error")) and bool(c.get("critical"))
        answers[c["id"]] = {
            "value": value, "na": na, "na_reason": r.get("na_reason", "") if na else "",
            "explanation": r.get("explanation", ""), "evidence": evidence, "checked_range": checked,
            "critical_error": crit, "critical_reason": r.get("critical_reason", "") if crit else "",
            "confidence": r.get("confidence", "medium"), "needs_review": r.get("confidence") == "low",
        }
    return answers, warnings


def vote(checklist: dict, runs: list[dict]) -> dict:
    """Консенсус нескольких прогонов: по каждому критерию — ответ большинства."""
    if len(runs) == 1:
        return runs[0]
    out = {}
    for c in checklist["criteria"]:
        opts = [r[c["id"]] for r in runs if c["id"] in r]
        keys = ["na" if a["na"] else a["value"] for a in opts]
        cnt = Counter(keys).most_common()
        top = cnt[0][1]
        tied = [k for k, n in cnt if n == top]
        if len(tied) == 1:
            win = tied[0]
        else:  # ничья: для шкалы — медианная ступень, иначе более строгий вариант
            nums = [k for k in keys if k != "na"]
            win = normalize_value(c, statistics.median(nums)) if nums else "na"
            if win not in keys:
                win = tied[0]
        rep = next(a for a, k in zip(opts, keys) if k == win)
        agree = keys.count(win) / len(keys)
        out[c["id"]] = {**rep, "votes": [("N/A" if k == "na" else k) for k in keys], "agreement": round(agree, 2),
                        "confidence": rep["confidence"] if agree == 1 else ("medium" if agree >= 0.6 else "low"),
                        "needs_review": rep.get("needs_review") or agree < 0.6}
    return out


# ---------- вызов модели ----------

def cost_usd(model: str, usage: dict) -> float:
    p_in, p_out, p_cw, p_cr = PRICING.get(model, PRICING["claude-opus-5-5"])
    return round((usage.get("input_tokens", 0) * p_in + usage.get("output_tokens", 0) * p_out
                  + usage.get("cache_creation_input_tokens", 0) * p_cw
                  + usage.get("cache_read_input_tokens", 0) * p_cr) / 1e6, 5)


_client: anthropic.AsyncAnthropic | None = None


def backend() -> str:
    """api / cli / mock — как задано; auto — что доступно, иначе none.

    Мок только по явному CQA_BACKEND=mock: без модели оценка честно падает с ошибкой,
    а не подменяется правдоподобной заглушкой.
    """
    if BACKEND in ("api", "cli", "mock"):
        return BACKEND
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return "api"
    return "cli" if shutil.which("claude") else "none"


def has_llm() -> bool:
    return backend() in ("api", "cli")


NO_MODEL = "Модель не подключена: задайте ANTHROPIC_API_KEY или установите Claude Code (claude -p)"


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(max_retries=4)
    return _client


async def call_llm(dialog: dict, checklist: dict) -> dict:
    limit = int(checklist.get("first_response_limit_s") or 180)
    resp = await client().beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": output_schema(checklist)}},
        system=[
            {"type": "text", "text": SYSTEM_RULES},
            # правила и чек-лист одинаковы для всего пакета — кешируем префикс
            {"type": "text", "text": render_checklist(checklist), "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": render_transcript(dialog, limit)
                   + "\n\nОцени оператора по всем критериям чек-листа."}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("Модель отказалась оценивать диалог")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Ответ модели обрезан (max_tokens)")
    text = next(b.text for b in resp.content if b.type == "text")
    usage = resp.usage.model_dump() if resp.usage else {}
    usage = {k: usage.get(k) or 0 for k in
             ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}
    return {"raw": json.loads(text), "model": resp.model, "usage": usage}


_cli_sem: asyncio.Semaphore | None = None


async def call_cli(dialog: dict, checklist: dict) -> dict:
    """Тот же промпт и та же JSON-схема, но через `claude -p` (подписка Claude Code).

    --safe-mode отключает CLAUDE.md, скиллы и плагины, --tools "" — инструменты:
    модель видит только наш системный промпт и диалог.
    """
    global _cli_sem
    _cli_sem = _cli_sem or asyncio.Semaphore(CLI_PARALLEL)
    limit = int(checklist.get("first_response_limit_s") or 180)
    args = ["claude", "-p", "--safe-mode", "--no-session-persistence", "--tools", "",
            "--model", MODEL, "--effort", EFFORT, "--output-format", "json",
            "--system-prompt", SYSTEM_RULES + "\n\n" + render_checklist(checklist),
            "--json-schema", json.dumps(output_schema(checklist), ensure_ascii=False)]
    prompt = render_transcript(dialog, limit) + "\n\nОцени оператора по всем критериям чек-листа."
    async with _cli_sem:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=tempfile.gettempdir(), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=900)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("claude -p: превышено время ожидания")
    try:
        data = json.loads(out.decode())
    except json.JSONDecodeError:
        raise RuntimeError(f"claude -p: {(err or out).decode()[:300]}")
    if data.get("is_error") or not data.get("structured_output"):
        raise RuntimeError(f"claude -p: {str(data.get('result') or data.get('subtype'))[:300]}")
    u = data.get("usage") or {}
    usage = {k: u.get(k) or 0 for k in
             ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}
    model = next(iter(data.get("modelUsage") or {}), MODEL)
    return {"raw": data["structured_output"], "model": model, "usage": usage,
            "cost_usd": float(data.get("total_cost_usd") or 0)}


def mock_raw(dialog: dict, checklist: dict, seed: int) -> dict:
    """Детерминированная заглушка: позволяет показать UI без ключа API."""
    turns = dialog["transcript"]
    op = [i for i, t in enumerate(turns) if t.get("speaker") == "operator"]
    crit = []
    for c in checklist["criteria"]:
        h = int(hashlib.md5(f"{dialog['id']}:{c['id']}:{seed % 2}".encode()).hexdigest(), 16)
        lo, hi = value_range(c)
        steps = scale_steps(c) or [lo, hi]
        na = bool(c.get("na_allowed")) and (h % 5 == 0 or not op)
        value = (1 if h % 9 == 0 else 0) if is_flag(c) else (hi if h % 4 else steps[max(0, len(steps) - 2)])
        t = op[h % len(op)] if op else 0
        crit.append({
            "criterion_id": c["id"], "na": na, "na_reason": "Мок: условие N/A выполнено" if na else "",
            "value": 0 if na else value,
            "explanation": f"Мок-оценка (ANTHROPIC_API_KEY не задан). Основание — реплика [{t}].",
            "evidence": [] if na else [{"turn": t, "quote": turns[t]["text"][:60] if turns else ""}],
            "checked_range": {"from_turn": op[0] if op else 0, "to_turn": op[-1] if op else 0},
            "critical_error": False, "critical_reason": "", "confidence": "medium",
        })
    return {"summary": "Мок-оценка: подключите ANTHROPIC_API_KEY для реальной оценки.", "criteria": crit}


def unmask_answers(answers: dict, mapping: dict) -> dict:
    """Метки ПДн в ответе модели → исходные значения (для показа человеку)."""
    if not mapping:
        return answers
    for a in answers.values():
        for k in ("explanation", "na_reason", "critical_reason"):
            a[k] = pii.unmask(a.get(k, ""), mapping)
        for e in a.get("evidence") or []:
            e["quote"] = pii.unmask(e["quote"], mapping)
    return answers


async def evaluate(dialog: dict, checklist: dict) -> dict:
    """Возвращает готовые answers (после проверки и голосования) и метаданные вызова."""
    started = time.time()
    # в модель уходит только замаскированный текст; цитаты сверяются с ним же
    sent, mapping, counts = pii.mask_dialog(dialog) if PII_MASK else (dialog, {}, Counter())
    runs, warnings = [], []

    def absorb(results: list) -> list[dict]:
        good = []
        for r in results:
            if isinstance(r, Exception):
                log.warning("sample failed for %s: %s", dialog["id"], r)
                continue
            a, w = postprocess(checklist, sent, r["raw"])
            runs.append(a)
            warnings.extend(x for x in w if x not in warnings)
            good.append(r)
        return good

    def unanimous() -> bool:
        keys = lambda a: "na" if a["na"] else a["value"]
        return all(len({keys(r[c["id"]]) for r in runs if c["id"] in r}) <= 1 for c in checklist["criteria"])

    if has_llm():
        call = call_cli if backend() == "cli" else call_llm
        # Сначала столько прогонов, сколько нужно для большинства. Если они единогласны по всем
        # критериям, оставшиеся прогоны исход голосования уже не изменят — их не запускаем.
        first = SAMPLES // 2 + 1
        results = await asyncio.gather(*(call(sent, checklist) for _ in range(first)), return_exceptions=True)
        ok = absorb(results)
        if len(ok) < SAMPLES and (len(ok) < first or not unanimous()):
            more = await asyncio.gather(*(call(sent, checklist) for _ in range(SAMPLES - len(ok))), return_exceptions=True)
            ok += absorb(more)
        if not ok:
            raise next(r for r in results if isinstance(r, Exception))
        model = ok[0]["model"]
        usage = {k: sum(r["usage"][k] for r in ok) for k in ok[0]["usage"]}
        cost = round(sum(r["cost_usd"] for r in ok), 5) if backend() == "cli" else cost_usd(MODEL, usage)
    elif backend() == "mock":
        ok = absorb([{"raw": mock_raw(sent, checklist, i)} for i in range(SAMPLES)])
        model, usage, cost = "mock", {}, 0.0
    else:
        raise RuntimeError(NO_MODEL)
    planned = SAMPLES if not has_llm() or len(ok) >= SAMPLES or not unanimous() else len(ok)
    if len(ok) < planned:
        warnings.append(f"Успешных прогонов {len(ok)} из {planned}")
    orig = dialog["transcript"]
    return {
        "answers": unmask_answers(vote(checklist, runs), mapping),
        "pii": {"enabled": PII_MASK, "counts": dict(counts),
                # что именно ушло в модель — только изменённые реплики
                "masked_turns": [{"turn": i, "text": t["text"]} for i, t in enumerate(sent["transcript"])
                                 if t["text"] != orig[i].get("text", "")]},
        "warnings": warnings,
        "summary": pii.unmask(ok[0]["raw"].get("summary", ""), mapping),
        "raw": [r["raw"] for r in ok],
        "samples": len(ok),
        "model": model,
        "usage": usage,
        "cost_usd": cost,
        "latency_s": round(time.time() - started, 1),
        "timing_facts": timing_facts(dialog, int(checklist.get("first_response_limit_s") or 180)),
    }
