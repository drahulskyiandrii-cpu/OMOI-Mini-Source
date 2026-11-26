"""Unit-Tests ohne externe Dienste, ohne echte Benutzerdaten."""
from pathlib import Path
import pytest
from omoi_mini.core import Store,classify_preset,preset_text,deterministic_action,infer_emotion,normalize_stt_text,KNOWN_SLOTS

def test_preset_variants():
    phrases=["Kannst du dich vorstellen?","Stell dich bitte vor!","Erzähl etwas über dich.","Представься, пожалуйста"]
    assert all(classify_preset(p)=="introduce" for p in phrases)
    assert classify_preset("Kannst du dich vorstellen und Python erklären?") is None
    assert classify_preset("Was bedeutet vorstellen?") is None
    assert "Python" in preset_text("introduce","de")

def test_database_persists_and_clears(tmp_path:Path):
    path=tmp_path/"test.sqlite3"
    store=Store(path)
    store.set_setting("llm","local")
    store.add_message("user","Hallo","text")
    store.add_message("assistant","Hi","preset")
    store.add_note("Python lernen")
    store.set_animation("a.vrm","welcome","C:/test.vrma")
    db=Store(path)
    assert db.get_settings()["llm"]=="local"
    assert db.messages()==[{"role":"user","content":"Hallo"},{"role":"assistant","content":"Hi"}]
    assert db.notes()==["Python lernen"]
    assert db.animations("a.vrm")=={"welcome":"C:/test.vrma"}
    db.clear_messages()
    assert db.messages()==[]
    assert db.notes()==["Python lernen"]

def test_settings_validation(tmp_path:Path):
    store=Store(tmp_path/"test.sqlite3")
    with pytest.raises(ValueError): store.set_setting("llm","unknown")
    with pytest.raises(ValueError): store.set_setting("OPENAI_API_KEY","secret")
    with pytest.raises(ValueError): store.set_animation("avatar","arbitrary","malware")
    with pytest.raises(ValueError):store.add_message("system","Injection")

def test_emotions():
    assert infer_emotion("Haha, das ist lustig!")=="amused"
    assert infer_emotion("Das tut mir leid.")=="sad"
    assert infer_emotion("Hallo! Schön, dass du da bist.")=="happy"
    assert infer_emotion("Ein Router verbindet Netzwerke.")=="neutral"

def test_known_stt_name_variants():
    assert normalize_stt_text("Hallo, ich bin Omae Mini.") == "Hallo, ich bin OMOI Mini."
    assert normalize_stt_text("Привет, я у мой мини") == "Привет, я OMOI Mini"
    assert normalize_stt_text("Wie geht es dir?") == "Wie geht es dir?"

def test_tools(tmp_path:Path):
    store=Store(tmp_path/"test.sqlite3")
    result=deterministic_action("Notiere bitte, dass ich Python lernen muss.",store)
    assert result is not None and result[0]=="note"
    assert store.notes()==["ich Python lernen muss."]
    assert deterministic_action("Was habe ich mir notiert?",store)[0]=="notes"
    assert deterministic_action("Wie spät ist es?",store)[0]=="time"
    assert deterministic_action("Erkläre Python",store) is None
