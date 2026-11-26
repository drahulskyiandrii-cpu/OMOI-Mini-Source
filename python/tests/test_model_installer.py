import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from omoi_mini import model_installer as mi


DATA = b"synthetic model bytes"
FILE = mi.ModelFile("sub/model.bin", len(DATA), hashlib.sha256(DATA).hexdigest())
MANIFEST = mi.Manifest("fake-model", "a" * 40, (FILE,))


def source(revision, file):
    assert revision == MANIFEST.revision
    assert file == FILE
    yield DATA[:5]
    yield DATA[5:]


def test_install_progress_remove(tmp_path):
    events = []
    path = mi.install(tmp_path, MANIFEST, source, approved=True,
                      progress=lambda done, total: events.append((done, total)))
    assert (path / FILE.path).read_bytes() == DATA
    assert events == [(0, len(DATA)), (5, len(DATA)), (len(DATA), len(DATA))]
    mi.remove(tmp_path, MANIFEST, approved=True)
    assert not path.exists()


@pytest.mark.parametrize("name", ["../bad", "C:/bad", "a\\bad", "NUL.bin",
                                  "a/../b", "a./b", ".omoi-managed.json"])
def test_reject_paths(tmp_path, name):
    with pytest.raises(mi.InstallError):
        mi.install(tmp_path, replace(MANIFEST, files=(replace(FILE, path=name),)),
                   source, approved=True)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("manifest", [
    replace(MANIFEST, revision="main"),
    replace(MANIFEST, files=(FILE, replace(FILE, path="SUB/MODEL.BIN"))),
    replace(MANIFEST, files=(FILE, replace(FILE, path="sub"))),
    replace(MANIFEST, files=(replace(FILE, sha256="wrong"),)),
])
def test_invalid_manifest(tmp_path, manifest):
    with pytest.raises(mi.InstallError):
        mi.install(tmp_path, manifest, source, approved=True)


def test_no_approval_or_disk(tmp_path, monkeypatch):
    with pytest.raises(mi.InstallError):
        mi.install(tmp_path, MANIFEST, source)
    monkeypatch.setattr(mi.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(mi.InstallError):
        mi.install(tmp_path, MANIFEST, source, approved=True)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("data", [b"x", b"x" * len(DATA), DATA + b"x"])
def test_invalid_bytes_cleanup(tmp_path, data):
    with pytest.raises(mi.InstallError):
        mi.install(tmp_path, MANIFEST, lambda *_: [data], approved=True)
    assert not list(tmp_path.iterdir())


def test_cancel_cleanup(tmp_path):
    done = []
    with pytest.raises(mi.InstallCancelled):
        mi.install(tmp_path, MANIFEST, source, approved=True,
                   progress=lambda n, _: done.append(n), cancel=lambda: any(done))
    assert not list(tmp_path.iterdir())


def test_existing_foreign_directory(tmp_path):
    path = tmp_path / MANIFEST.model_id
    path.mkdir()
    foreign = path / "private.gguf"
    foreign.write_bytes(DATA)
    with pytest.raises(FileExistsError):
        mi.install(tmp_path, MANIFEST, source, approved=True)
    with pytest.raises(OSError):
        mi.remove(tmp_path, MANIFEST, approved=True)
    assert foreign.read_bytes() == DATA


@pytest.mark.parametrize("foreign", [True, False])
def test_remove_refuses_changes(tmp_path, foreign):
    path = mi.install(tmp_path, MANIFEST, source, approved=True)
    if foreign:
        (path / "private.bin").write_bytes(DATA)
    else:
        (path / FILE.path).write_bytes(b"x" * len(DATA))
    with pytest.raises(mi.InstallError):
        mi.remove(tmp_path, MANIFEST, approved=True)
    assert (path / mi.MARKER).exists()
    assert (path / FILE.path).exists()


def test_remove_requires_approval(tmp_path):
    path = mi.install(tmp_path, MANIFEST, source, approved=True)
    with pytest.raises(mi.InstallError):
        mi.remove(tmp_path, MANIFEST)
    assert path.exists()


def test_symlink_root_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink-Recht fehlt")
    with pytest.raises(mi.InstallError):
        mi.install(link, MANIFEST, source, approved=True)
    assert not list(root.iterdir())
