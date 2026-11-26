import hashlib
from dataclasses import replace
from io import BytesIO

import pytest

from omoi_mini import model_service as service
from omoi_mini.model_installer import Manifest, ModelFile, InstallError, InstallCancelled
from omoi_mini.model_releases import ModelRelease, RELEASES
from omoi_mini.model_catalog import catalog_rows

DATA = b"synthetic only"
FILE = ModelFile("model.bin", len(DATA), hashlib.sha256(DATA).hexdigest())
RELEASE = ModelRelease("example/test", "MIT", Manifest("test-model", "a"*40, (FILE,)), True)


def fake_factory(release):
    return lambda revision, file: [DATA]


def create(profile):
    return service.ModelService(profile, releases=(RELEASE,), source_factory=fake_factory)


def test_real_catalog_is_locked(tmp_path):
    model_service = service.ModelService(tmp_path)
    for release in RELEASES:
        assert not release.approved
        with pytest.raises(InstallError):
            model_service.install(release.manifest.model_id, approved=True)
    assert not list(tmp_path.iterdir())


def test_manifest_catalog_status():
    row = next(row for row in catalog_rows() if row["id"] == "whisper-small-cuda")
    assert row["revision"] == RELEASES[0].manifest.revision
    assert row["exact_size_bytes"] == sum(f.size for f in RELEASES[0].manifest.files)
    assert row["download_allowed"] is False


def test_install_remove_only_managed_root(tmp_path):
    foreign = tmp_path / "test-model"
    foreign.mkdir()
    (foreign / "model.bin").write_bytes(DATA)
    manager = create(tmp_path)
    installed = manager.install("test-model", approved=True)
    assert installed == tmp_path / service.ROOT_NAME / "test-model"
    manager.remove("test-model", approved=True)
    assert (foreign / "model.bin").read_bytes() == DATA
    assert not list((tmp_path / service.ROOT_NAME).iterdir())


@pytest.mark.parametrize("model_id", ["../test-model", "unknown", "test-model"])
def test_reject_without_approval(tmp_path, model_id):
    with pytest.raises(InstallError):
        create(tmp_path).install(model_id)
    assert not list(tmp_path.iterdir())


def test_cross_service_lock(tmp_path):
    first, second = create(tmp_path), create(tmp_path)
    with first._operation():
        with pytest.raises(InstallError):
            second.install("test-model", approved=True)
    assert not (tmp_path / service.ROOT_NAME / service.LOCK).exists()


def test_cancel_releases_lock(tmp_path):
    with pytest.raises(InstallCancelled):
        create(tmp_path).install("test-model", approved=True, cancel=lambda: True)
    assert not list((tmp_path / service.ROOT_NAME).iterdir())


@pytest.mark.parametrize("url", ["http://huggingface.co/a", "https://example.com/a",
                                 "https://huggingface.co.evil.test/a",
                                 "https://user@huggingface.co/a",
                                 "https://huggingface.co:444/a"])
def test_reject_url(url):
    with pytest.raises(InstallError):
        service._safe_https(url)


def test_allowed_urls():
    for url in ["https://huggingface.co/a", "https://cdn-lfs.huggingface.co/a",
                "https://cas-bridge.xethub.hf.co/a?signature=synthetic"]:
        service._safe_https(url)


def test_mock_https_pinned_request(monkeypatch):
    calls = []
    class Response(BytesIO):
        status = 200
        headers = {"Content-Length": str(len(DATA))}
        def geturl(self):
            return "https://cdn-lfs.huggingface.co/example"
    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, timeout))
            return Response(DATA)
    monkeypatch.setattr(service, "build_opener", lambda *_: Opener())
    source = service.https_source(RELEASE)
    assert b"".join(source(RELEASE.manifest.revision, FILE)) == DATA
    assert calls == [("https://huggingface.co/example/test/resolve/" + "a"*40 + "/model.bin", 15)]
    with pytest.raises(InstallError):
        list(source("main", FILE))
    with pytest.raises(InstallError):
        list(source(RELEASE.manifest.revision, replace(FILE, path="other.bin")))
    assert len(calls) == 1


def test_blocked_release_cannot_construct_transport():
    with pytest.raises(InstallError):
        service.https_source(replace(RELEASE, approved=False))


def test_all_pinned_manifests_match_catalog_and_remain_locked():
    from omoi_mini.model_installer import validate, RESERVE
    rows = {row["id"]: row for row in catalog_rows()}
    expected = {
        "gemma4-e2b-gguf": (2, 3349545516),
        "whisper-small-cuda": (5, 486214370),
        "qwen3-tts-1.7b": (12, 4544228181),
        "qwen3-tts-0.6b": (12, 2516104532),
        "whisper-medium-ov": (20, 784025543),
    }
    assert len(RELEASES) == len(expected)
    assert {r.manifest.model_id for r in RELEASES} == set(expected)
    for release in RELEASES:
        manifest = release.manifest
        count, size = expected[manifest.model_id]
        assert len(manifest.files) == count
        assert validate(manifest) == size
        row = rows[manifest.model_id]
        assert row["repository"] == release.repository
        assert row["revision"] == manifest.revision
        assert row["exact_size_bytes"] == size
        assert row["required_space_bytes"] == size + RESERVE
        assert row["file_count"] == count
        assert not release.approved and not row["download_allowed"]


def test_gemma_text_only_and_qwen_nested_tokenizer():
    from omoi_mini.model_releases import release_for
    gemma = release_for("gemma4-e2b-gguf")
    assert {f.path for f in gemma.manifest.files} == {
        "README.md", "gemma-4-E2B_q4_0-it.gguf"}
    for mid in ("qwen3-tts-1.7b", "qwen3-tts-0.6b"):
        release = release_for(mid)
        paths = {f.path for f in release.manifest.files}
        assert {"model.safetensors", "speech_tokenizer/model.safetensors",
                "speech_tokenizer/config.json", "vocab.json"} <= paths
