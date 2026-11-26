"""RU-TTS-Texttests ohne Download, Sprachprobe, GPU oder Netzanfrage."""
import pytest
from omoi_mini.tts_text import (
    flag_ambiguous_russian_words,
    prepare_russian_speech,
    split_for_tts,
)

def test_opt_in_terms_are_readable():
    text = prepare_russian_speech("Проверь DNS и VPN, затем Wi-Fi и IPv6.", expand_it_terms=True)
    assert text == "Проверь дэ эн эс и ви пи эн, затем вай фай и ай пи версии шесть."

def test_terms_are_opt_in_not_implicit():
    assert prepare_russian_speech("Проверь DNS и IPv4.") == "Проверь DNS и IPv4."

def test_no_rewriting_inside_words():
    assert prepare_russian_speech("MYDNS и ipV6.", expand_it_terms=True) == "MYDNS и ай пи версии шесть."

def test_markdown_and_links_are_spoken_safely():
    content = "### Отчёт\n- [Проверь сеть](https://example.org/network)\n- DNS\n"
    assert prepare_russian_speech(content) == "Отчёт. Проверь сеть. DNS"

def test_remove_code_and_bare_urls():
    fence = chr(96)*3
    text = "Информация здесь: https://example.org/test.\n" + fence + "python\nprint('secret')\n" + fence
    result = prepare_russian_speech(text)
    assert "print" not in result
    assert "example.org" not in result
    assert "Информация здесь:" in result

def test_homographs_are_only_flagged():
    text = "Все увидели древний замок, а я плачу."
    assert prepare_russian_speech(text) == text
    assert flag_ambiguous_russian_words(text) == ("все", "замок", "плачу")

def test_explicit_pronunciation_mapping_without_guesswork():
    assert prepare_russian_speech(
        "Мы смотрим на замок.", pronunciations={"замок": "старинный замок"}
    ) == "Мы смотрим на старинный замок."

def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        prepare_russian_speech("")
    with pytest.raises(TypeError):
        prepare_russian_speech(None)
    with pytest.raises(ValueError):
        prepare_russian_speech("Слово", pronunciations={"": "неправильно"})

def test_long_text_preserves_words():
    text = "Сначала проверим маршрутизатор. Затем проверим адреса. После этого запустим диагностику."
    chunks = split_for_tts(text, max_chars=60)
    assert len(chunks) > 1
    assert " ".join(chunks) == text

def test_split_rejects_tiny_window():
    with pytest.raises(ValueError):
        split_for_tts("Привет!", max_chars=8)
