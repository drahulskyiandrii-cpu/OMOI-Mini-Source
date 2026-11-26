"""Metadaten einer synthetischen CP311-Umgebung, kein Import."""
import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
from check_runtime_candidate import REQUIRED,check_candidate


def install_metadata(site,name,version,*,tag="py3-none-any",requires=(),python=">=3.11"):
    path=site/(name.replace("-","_")+"-"+version+".dist-info")
    path.mkdir(parents=True)
    text="Metadata-Version: 2.1\nName: "+name+"\nVersion: "+version+"\nRequires-Python: "+python+"\n"
    for dep in requires:text+="Requires-Dist: "+dep+"\n"
    (path/"METADATA").write_text(text,encoding="utf-8")
    (path/"WHEEL").write_text("Wheel-Version: 1.0\nTag: "+tag+"\n",encoding="utf-8")
    return path


@pytest.fixture
def candidate(tmp_path):
    (tmp_path/"Scripts").mkdir()
    (tmp_path/"Scripts/python.exe").write_bytes(b"NOT AN EXECUTABLE")
    (tmp_path/"pyvenv.cfg").write_text("version = 3.11.9\ninclude-system-site-packages = false\n")
    site=tmp_path/"Lib/site-packages"
    for name,version in REQUIRED.items():install_metadata(site,name,version)
    return tmp_path,site


def test_candidate_only_reads_metadata(candidate):
    root,site=candidate
    install_metadata(site,"synthetic", "1.0",tag="cp311-cp311-win_amd64",
                     requires=("filelock>=4.3","pillow>=13; python_version >= '3.11'",
                               "missing; extra == 'test'"))
    result=check_candidate(root)
    assert result["static_pass"] and result["packages"]==len(REQUIRED)+1
    assert str(root) not in str(result)


def test_incompatible_old_wheels_and_dependencies(candidate):
    root,site=candidate
    install_metadata(site,"synthetic","1.0",tag="cp310-cp310-win_amd64",
                     python="<3.11",requires=("filelock<4","missing"))
    result=check_candidate(root)
    assert not result["static_pass"]
    for key in ("requires_python_conflicts","incompatible_or_missing_wheel_tags",
                "missing_dependencies","version_conflicts"):
        assert result[key]==1


def test_overlay_cannot_shadow_candidate(candidate,tmp_path):
    root,site=candidate
    overlay=tmp_path/"overlay"
    install_metadata(overlay,"filelock","4.1.0")
    result=check_candidate(root,overlay)
    assert not result["static_pass"] and result["duplicate_packages"]==1


def test_old_python_rejected(candidate):
    root,site=candidate
    (root/"pyvenv.cfg").write_text("version = 3.10.11\ninclude-system-site-packages = false\n")
    with pytest.raises(ValueError):check_candidate(root)


def test_missing_candidate_pin_rejected(candidate):
    root,site=candidate
    path=site/"filelock-4.3.1.dist-info"
    (path/"METADATA").write_text("Name: filelock\nVersion: 4.1.0\n")
    assert check_candidate(root)["required_mismatch"]==["filelock"]


def test_missing_wheel_and_direct_url_require_review(candidate):
    root,site=candidate
    path=install_metadata(site,"synthetic","1.0")
    (path/"WHEEL").unlink()
    (path/"direct_url.json").write_text('{"url":"file:///private/synthetic"}')
    result=check_candidate(root)
    assert not result["static_pass"] and result["direct_url_review"]==1
    assert "private" not in str(result)
