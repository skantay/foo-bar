"""Маскирование персональных данных перед отправкой в модель."""
import pii


def dialog(*turns):
    return {"id": "x", "transcript": [{"timestamp": "00:00", "speaker": s, "text": t} for s, t in turns]}


def masked_text(d):
    out, mapping, counts = pii.mask_dialog(d)
    return " | ".join(t["text"] for t in out["transcript"]), mapping, counts


def test_phone_and_email_are_masked_and_restorable():
    text, mapping, counts = masked_text(dialog(("client", "Мой номер +7 (701) 123-45-67, почта Ivan.P@mail.kz")))
    assert "701" not in text and "mail.kz" not in text
    assert counts["PHONE"] == 1 and counts["EMAIL"] == 1
    assert pii.unmask(text, mapping) == "Мой номер +7 (701) 123-45-67, почта Ivan.P@mail.kz"


def test_iin_masked_only_with_valid_checksum():
    valid, invalid = "880101300010", "880101300011"
    assert pii._iin_ok(valid) and not pii._iin_ok(invalid)
    text, _, counts = masked_text(dialog(("client", f"ИИН {valid}, а это {invalid}")))
    assert valid not in text and invalid in text and counts["IIN"] == 1


def test_card_masked_only_with_valid_luhn():
    text, _, counts = masked_text(dialog(("client", "Карта 4111 1111 1111 1111, не карта 1234 5678 9012 3456")))
    assert "4111" not in text and "1234 5678 9012 3456" in text and counts["CARD"] == 1


def test_names_masked_consistently_across_turns():
    text, mapping, _ = masked_text(dialog(("operator", "Здравствуйте, меня зовут Алина."),
                                          ("client", "Алина, спасибо!"),
                                          ("operator", "I'm here to help, Алина на связи")))
    assert "Алина" not in text
    labels = [k for k in mapping if mapping[k] == "Алина"]
    assert labels == ["[ИМЯ_ОПЕРАТОРА_1]"] and text.count("[ИМЯ_ОПЕРАТОРА_1]") == 3


def test_service_words_are_not_treated_as_names():
    text, mapping, _ = masked_text(dialog(("bot", "Я AIM, ассистент Freedom Broker"),
                                          ("operator", "Я Оператор поддержки, это Freedom")))
    assert mapping == {} and "Freedom" in text


def test_same_value_gets_same_label():
    text, mapping, counts = masked_text(dialog(("client", "+77011234567"), ("client", "повторю: +7 701 123 45 67")))
    assert len([k for k in mapping if k.startswith("[PHONE")]) == 1 and counts["PHONE"] == 2


def test_unmask_without_mapping_is_noop():
    assert pii.unmask("текст [PHONE_1]", {}) == "текст [PHONE_1]"
