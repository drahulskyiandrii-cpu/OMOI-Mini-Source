"""Sicherheitsgrenzen des lokalen Assetservers."""
from pathlib import Path
import httpx
from omoi_mini.core import Store
from omoi_mini.web_server import WebAssets

def test_static_and_api_are_sandboxed(tmp_path:Path):
    dist=tmp_path/"dist"
    dist.mkdir()
    (dist/"index.html").write_text("<html>OMOI Mini</html>",encoding="utf-8")
    db=Store(tmp_path/"db.sqlite3")
    service=WebAssets(dist,db)
    service.avatar=None
    port=service.start()
    try:
        url=f"http://127.0.0.1:{port}"
        assert httpx.get(url+"/").status_code==200
        assert httpx.get(url+"/api/avatar").status_code==404
        channel=httpx.get(url+"/qwebchannel.js")
        assert channel.status_code==200
        assert "QWebChannel" in channel.text
        assert httpx.get(url+"/unknown").status_code==404
        # A path must not permit a traversal of the static dist directory.
        assert httpx.get(url+"/%2e%2e/%2e%2e/README.md").status_code in (400,404)
    finally:service.close()
