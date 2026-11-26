"""Modellkatalog ohne Netzwerk und ohne Inferenz."""
from omoi_mini.model_catalog import catalog_rows


def test_catalog_contains_accepted_reference_chain():
    rows = catalog_rows()
    assert {r["id"] for r in rows if r["role"] == "Referenz"} == {
        "gemma4-e2b-gguf", "whisper-small-cuda", "qwen3-tts-1.7b"
    }
    assert len({r["id"] for r in rows}) == len(rows)


def test_all_downloads_are_blocked():
    assert all(r["download_allowed"] is False for r in catalog_rows())


def test_catalog_is_independent_of_caller_mutations():
    rows = catalog_rows()
    rows[0]["download_allowed"] = True
    assert not catalog_rows()[0]["download_allowed"]


def test_metadata_has_no_local_paths_or_download_commands():
    for row in catalog_rows():
        assert not any("\\\\" in str(value) or "C:" in str(value) for value in row.values())
        assert "download_url" not in row
        if row["size_mb"] is not None:
            assert row["size_mb"] > 0
            assert row["repository"] and row["upstream_license"]
        assert row["integrity_status"]
