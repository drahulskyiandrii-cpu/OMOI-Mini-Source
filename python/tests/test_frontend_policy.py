"""Sicherheits-Regressionstest für die gebaute VRM-Weboberfläche.

Three.js nutzt blob:-URLs zum Laden eingebetteter glTF-Texturen. Eine
zu enge CSP blockiert diese auch bei einem erfolgreich geladenen Avatar.
"""
from html.parser import HTMLParser
from pathlib import Path


class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.csp = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("http-equiv") == "Content-Security-Policy":
            self.csp = attributes.get("content")


def test_vrm_textures_allowed_but_remote_fetch_restricted():
    root = Path(__file__).resolve().parents[2]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    parser = _MetaParser()
    parser.feed(html)
    assert parser.csp is not None
    directives = {}
    for rule in parser.csp.split(";"):
        parts = rule.strip().split()
        if parts:
            directives[parts[0]] = parts[1:]
    assert "blob:" in directives["connect-src"]
    assert "'self'" in directives["connect-src"]
    assert all(not source.startswith(("https:", "http:", "*"))
               for source in directives["connect-src"])
    assert "blob:" in directives["img-src"]
    assert "'none'" in directives["object-src"]
