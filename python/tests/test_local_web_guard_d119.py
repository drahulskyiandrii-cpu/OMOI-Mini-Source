"""Lokale Assetgrenzen ohne Browserfenster, Modelle oder GPU."""
import httpx
import pytest
from PySide6.QtCore import QUrl
from omoi_mini.core import Store
from omoi_mini.gui import local_page_url
from omoi_mini.web_server import WebAssets


def test_asset_server_rejects_foreign_host_and_null_path(tmp_path):
    dist=tmp_path/"dist";dist.mkdir()
    (dist/"index.html").write_text("local",encoding="utf-8")
    server=WebAssets(dist,Store(tmp_path/"db"))
    port=server.start()
    try:
        base=f"http://127.0.0.1:{port}"
        assert server.start()==port
        with httpx.Client(trust_env=False) as client:
            assert client.get(base).text=="local"
            assert client.get(base,headers={"Host":"foreign.invalid"}).status_code==403
            assert client.get(base+"/%00").status_code==400
            assert client.get(base+"/%5C").status_code==400
    finally:
        server.close()
        server.close()


@pytest.mark.parametrize("url,allowed",[
    ("http://127.0.0.1:8123/",True),
    ("http://127.0.0.1:8123/api/avatar",True),
    ("http://127.0.0.1:8124/",False),
    ("http://localhost:8123/",False),
    ("https://127.0.0.1:8123/",False),
    ("http://user@127.0.0.1:8123/",False),
    ("file:///C:/secret.txt",False),
    ("https://example.invalid/",False),
])
def test_page_navigation_keeps_webchannel_on_local_origin(url,allowed):
    assert local_page_url(QUrl(url),8123)==allowed
