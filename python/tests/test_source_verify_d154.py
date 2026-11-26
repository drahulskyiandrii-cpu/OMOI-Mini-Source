from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile
import pytest

SPEC=importlib.util.spec_from_file_location("export_source_test",Path(__file__).resolve().parents[2]/"scripts"/"export_source.py")
export=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export)

@pytest.fixture
def minimal(monkeypatch):
    monkeypatch.setattr(export,"EXACT",frozenset({"README.md"}))
    monkeypatch.setattr(export,"APPROVED_ANIMATIONS",{})

def archive(path,*,data=b"safe",expected=b"safe",extra=None,manifest_override=None):
    manifest={"schema":1,"kind":"source-review","commit":"a"*40,"files":[{"path":"README.md","size":len(expected),"sha256":hashlib.sha256(expected).hexdigest()}]}
    if manifest_override:manifest.update(manifest_override)
    with zipfile.ZipFile(path,"w") as out:
        out.writestr("OMOI-Mini/README.md",data)
        out.writestr("OMOI-Mini/SOURCE_MANIFEST.json",json.dumps(manifest))
        if extra:out.writestr(*extra)
    return path

def test_verify_valid_source_zip(tmp_path,minimal):
    assert export.verify_export(archive(tmp_path/"valid.zip"))["commit"]=="a"*40

@pytest.mark.parametrize("data",[b"evil",b"longer"])
def test_verify_hash_or_size(tmp_path,minimal,data):
    with pytest.raises(ValueError):
        export.verify_export(archive(tmp_path/"bad.zip",data=data))

@pytest.mark.parametrize("entry",["../escape","OMOI-Mini/../escape","OMOI-Mini/readme.md","OMOI-Mini/extra.txt","OMOI-Mini/a\\\\b","OMOI-Mini/a:stream"])
def test_verify_rejects_unsafe_or_unlisted_entry(tmp_path,minimal,entry):
    with pytest.raises(ValueError):
        export.verify_export(archive(tmp_path/"bad.zip",extra=(entry,b"x")))

@pytest.mark.parametrize("override",[{"schema":2},{"commit":"unknown"},{"files":[]},{"files":[None]}])
def test_verify_rejects_invalid_manifest(tmp_path,minimal,override):
    with pytest.raises(ValueError):
        export.verify_export(archive(tmp_path/"bad.zip",manifest_override=override))

def test_verify_rejects_symlink(tmp_path,minimal):
    item=zipfile.ZipInfo("OMOI-Mini/link")
    item.create_system=3
    item.external_attr=0o120777<<16
    with pytest.raises(ValueError):
        export.verify_export(archive(tmp_path/"bad.zip",extra=(item,b"README.md")))

def test_verify_rejects_secrets_in_valid_hash(tmp_path,minimal):
    data=b"sk-"+b"x"*40
    with pytest.raises(ValueError):
        export.verify_export(archive(tmp_path/"bad.zip",data=data,expected=data))


def test_demo_screenshot_only_exact_reviewed_bytes():
    root=Path(__file__).resolve().parents[2]
    data=(root/export.DEMO_IMAGE).read_bytes()
    assert export.selected(export.DEMO_DOC)
    assert export.selected(export.DEMO_IMAGE)
    assert not export.selected("docs/screenshots/private.png")
    assert export.scan(export.DEMO_IMAGE,data)==data
    with pytest.raises(ValueError,match="Unapproved demonstration"):
        export.scan(export.DEMO_IMAGE,data+b"x")


def test_verify_rejects_incomplete_demo(tmp_path,minimal):
    data=b"demo"
    item={"path":export.DEMO_DOC,"size":len(data),"sha256":hashlib.sha256(data).hexdigest()}
    files=[{"path":"README.md","size":4,"sha256":hashlib.sha256(b"safe").hexdigest()},item]
    path=archive(tmp_path/"bad.zip",extra=("OMOI-Mini/"+export.DEMO_DOC,data),manifest_override={"files":files})
    with pytest.raises(ValueError,match="Incomplete demonstration"):
        export.verify_export(path)
