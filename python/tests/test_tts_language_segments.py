import pytest

from omoi_mini.tts_language_segments import plan_ru_de


@pytest.mark.parametrize("text, fallback, languages", [
    ("Привет! Как дела?", "ru", ["ru"]),
    ("Guten Morgen! Wie geht es dir?", "de", ["de"]),
    ("Привет! Wie geht es dir? Спасибо.", "ru", ["ru", "de", "ru"]),
    ("Guten Morgen, сегодня хороший день.", "de", ["de", "ru"]),
    ("Сначала DNS, VPN, GPU. Потом проверим.", "ru", ["ru"]),
    ("Heute prüfen wir DNS. Затем продолжим.", "de", ["de", "ru"]),
    ("Привет! 42. Guten Morgen!", "ru", ["ru", "de"]),
    ("Привет. Python 3.11. Спасибо.", "ru", ["ru"]),
    ("Смотри: Wie geht es dir?\nОтлично.", "ru", ["ru", "de", "ru"]),
    ("   Guten Morgen!  \nПривет.  ", "ru", ["de", "ru"]),
    ("Das ist gut. Привет! 123. Das ist schön.", "de", ["de", "ru", "de"]),
    ("Запусти Docker и Python, пожалуйста.", "ru", ["ru"]),
    ("Hello world.", "ru", ["ru"]),
])
def test_plan_preserves_text(text, fallback, languages):
    result = plan_ru_de(text, fallback=fallback)
    assert [segment.language for segment in result] == languages
    assert "".join(segment.text for segment in result) == text


def test_empty():
    assert plan_ru_de("   ", fallback="de") == ()


def test_invalid_language():
    with pytest.raises(ValueError):
        plan_ru_de("Test", fallback="en")


def test_limit():
    with pytest.raises(ValueError):
        plan_ru_de("x" * 16001, fallback="ru")
