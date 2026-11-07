"""Маскирование персональных данных перед отправкой диалога в модель.

Данные заменяются метками ([PHONE_1], [IIN_1], [ИМЯ_ОПЕРАТОРА_1] …), а не удаляются:
модель видит, что оператор назвал имя или раскрыл номер, но не видит сами данные.
Одинаковое значение в диалоге получает одну и ту же метку. Таблица замен остаётся у нас:
по ней цитаты и объяснения модели возвращаются к исходному тексту.
"""
import re
from collections import Counter


def _iin_ok(d: str) -> bool:
    """Контрольная цифра ИИН/БИН Казахстана."""
    n = [int(c) for c in d]
    for weights in (range(1, 12), [3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2]):
        s = sum(a * w for a, w in zip(n[:11], weights)) % 11
        if s != 10:
            return s == n[11]
    return False


def _luhn_ok(d: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(d)):
        x = int(c)
        if i % 2:
            x = x * 2 - 9 if x > 4 else x * 2
        total += x
    return total % 10 == 0


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


# Порядок важен: сначала длинные и специфичные форматы.
PATTERNS = [
    # домен без точки в конце: «пишите на a@b.kz.» — точка относится к предложению
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), None),
    ("IBAN", re.compile(r"\bKZ\d{2}(?:\s?[0-9A-Z]{4}){4}\b", re.I), None),
    ("CARD", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), lambda m: _luhn_ok(_digits(m))),
    ("IIN", re.compile(r"(?<!\d)\d{12}(?!\d)"), _iin_ok),
    ("PHONE", re.compile(r"(?<![\w+])(?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"), None),
]

# Имя после «меня зовут / я / мен / I'm / my name is …» с заглавной буквы.
NAME_CONTEXT = re.compile(
    r"(?<![\w])(?:меня зовут|моё имя|мое имя|это|я|менің атым|атым|мен|my name is|i am|i'm|this is)\s+"
    r"([A-ZА-ЯЁӘҒҚҢӨҰҮҺІ][a-zа-яёәғқңөұүһі]{1,20})(?=[\s,.!?)]|$)",
    re.I)
NOT_NAMES = {"freedom", "aim", "broker", "оператор", "бот", "support", "агент", "специалист", "sorry", "here",
             "ваш", "ваша", "сіздің", "your", "from", "сейчас", "тоже", "уже", "не", "рад", "рада", "glad", "happy",
             "able", "afraid", "checking", "проверю", "уточню", "қазір",
             # вежливые местоимения с заглавной: «Я Вам помогу», «Мен Сізге көмектесемін»
             "вы", "вам", "вас", "вами", "ваше", "вашего", "вашей", "вашему", "вашим", "ваши", "ваших",
             "сіз", "сізге", "сізді", "сізбен", "сізден", "сізде", "сіздер", "сіздерге", "сіздерді",
             "клиент", "клиентка", "you"}

ROLE_LABEL = {"operator": "ИМЯ_ОПЕРАТОРА", "client": "ИМЯ_КЛИЕНТА"}


def mask_dialog(dialog: dict) -> tuple[dict, dict, Counter]:
    """Возвращает (замаскированный диалог, {метка: оригинал}, счётчик по типам)."""
    to_label: dict[tuple[str, str], str] = {}
    mapping: dict[str, str] = {}
    counts: Counter = Counter()

    def label_for(kind: str, key: str, original: str) -> str:
        if (kind, key) not in to_label:
            n = sum(1 for k, _ in to_label if k == kind) + 1
            lab = f"[{kind}_{n}]"
            to_label[(kind, key)] = lab
            mapping[lab] = original
        counts[kind] += 1
        return to_label[(kind, key)]

    # имена: сначала собираем по всему диалогу, чтобы маскировать и повторные упоминания
    names: dict[str, str] = {}
    for t in dialog["transcript"]:
        kind = ROLE_LABEL.get(t.get("speaker"))
        if not kind:
            continue
        for m in NAME_CONTEXT.finditer(t.get("text", "")):
            w = m.group(1)
            if m.group(1)[0].isupper() and w.lower() not in NOT_NAMES:
                names.setdefault(w, kind)
    name_re = re.compile(r"(?<![\w])(" + "|".join(map(re.escape, sorted(names, key=len, reverse=True))) + r")(?![\w])") \
        if names else None

    out = []
    for t in dialog["transcript"]:
        text = t.get("text", "")
        for kind, rx, check in PATTERNS:
            def repl(m, kind=kind, check=check):
                s = m.group(0)
                if check and not check(_digits(s) if kind != "EMAIL" else s):
                    return s
                key = s.lower() if kind == "EMAIL" else _digits(s) or s
                return label_for(kind, key, s)
            text = rx.sub(repl, text)
        if name_re:
            text = name_re.sub(lambda m: label_for(names[m.group(1)], m.group(1), m.group(1)), text)
        out.append({**t, "text": text})
    return {**dialog, "transcript": out}, mapping, counts


_LABEL_RE = re.compile(r"\[(?:EMAIL|IBAN|CARD|IIN|PHONE|ИМЯ_ОПЕРАТОРА|ИМЯ_КЛИЕНТА)_\d+\]")


def unmask(text: str, mapping: dict) -> str:
    """Возвращает исходные значения на место меток (для показа человеку)."""
    if not text or not mapping:
        return text
    return _LABEL_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)
