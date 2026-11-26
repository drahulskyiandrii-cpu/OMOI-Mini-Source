"""Provider-Grenzfälle ohne echte API-Aufrufe."""
import os
import pytest
from omoi_mini.providers import ProviderFailure, cloud_answer_stream,transcribe_openai,cartesia_wav

def test_missing_keys_fail_without_request(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    monkeypatch.delenv("CARTESIA_API_KEY",raising=False)
    with pytest.raises(ProviderFailure,match="OPENAI_API_KEY"):
        list(cloud_answer_stream([{"role":"user","content":"Hallo"}],"de"))
    with pytest.raises(ProviderFailure,match="OPENAI_API_KEY"):
        transcribe_openai(b"RIFF","de")
    with pytest.raises(ProviderFailure,match="CARTESIA_API_KEY"):
        cartesia_wav("Hallo","de")
