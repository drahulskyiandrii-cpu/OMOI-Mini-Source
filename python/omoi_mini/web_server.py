"""Nur an 127.0.0.1 gebundener Web- und Asset-Server (keine Geheimnisse)."""
from __future__ import annotations
import mimetypes
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from .core import KNOWN_SLOTS, resolve_animation, resolve_avatar
from .default_avatar import bundled_default_avatar

class WebAssets:
    def __init__(self, dist: Path, store):
        self.dist=dist.resolve()
        self.store=store
        preferred=store.get_settings().get("avatar","")
        favorites=store.favorite_avatars()
        self.avatar=resolve_avatar(preferred, favorites)
        if self.avatar is None and not preferred and not favorites:
            self.avatar=bundled_default_avatar()
        # Temporäre Vorschau: eine importierte Datei wird erst nach Freigabe aktiv.
        self.staged_animations:dict[str,Path]={}
        self.server:ThreadingHTTPServer|None=None
        self.thread:threading.Thread|None=None
    def set_avatar(self, path:Path):
        self.avatar=path.resolve()
    def _selected_asset(self, route:str)->Path|None:
        if route=="/api/avatar":
            return self.avatar if self.avatar and self.avatar.exists() else None
        if route.startswith("/api/preview/"):
            slot=route.rsplit("/",1)[-1]
            return self.staged_animations.get(slot) if slot in KNOWN_SLOTS else None
        if route.startswith("/api/animation/"):
            slot=route.rsplit("/",1)[-1]
            return resolve_animation(slot,str(self.avatar or ""),self.store)
        return None
    def start(self)->int:
        if self.server is not None:return self.server.server_address[1]
        parent=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,format,*args): pass
            def setup(self):
                self.request.settimeout(5)
                super().setup()

            def do_GET(self):
                try:self._serve()
                except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
                except (OSError,ValueError,sqlite3.Error):
                    # Keine Dateipfade oder privaten Daten an den Browser melden.
                    try:self.send_error(503)
                    except OSError:pass

            def _serve(self):
                host=f"127.0.0.1:{self.server.server_address[1]}"
                # DNS-Rebinding: ein fremder Hostname darf keine lokalen Assets lesen.
                if self.headers.get("Host")!=host:
                    self.send_error(403);return
                route=unquote(urlsplit(self.path).path)
                if not route or ".." in route or chr(92) in route or chr(0) in route:
                    self.send_error(400);return
                if route=="/qwebchannel.js":
                    from PySide6.QtCore import QFile, QIODevice
                    from PySide6.QtWebChannel import QWebChannel
                    resource=QFile(":/qtwebchannel/qwebchannel.js")
                    if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
                        self.send_error(500);return
                    code=bytes(resource.readAll())
                    resource.close()
                    self.send_response(200)
                    self.send_header("Content-Type","text/javascript; charset=utf-8")
                    self.send_header("Content-Length",str(len(code)))
                    self.send_header("Cache-Control","no-store")
                    self.send_header("X-Content-Type-Options","nosniff")
                    self.end_headers()
                    self.wfile.write(code)
                    return
                if route.startswith("/api/"):
                    asset=parent._selected_asset(route)
                    if not asset or not asset.is_file() or asset.is_symlink():
                        self.send_error(404);return
                    suffix=asset.suffix.lower()
                    if suffix not in (".vrm",".vrma"):
                        self.send_error(415);return
                    limit=200*1024*1024 if suffix==".vrm" else 128*1024*1024
                    mime="model/gltf-binary"
                else:
                    if route=="/":route="/index.html"
                    asset=(parent.dist/route.lstrip("/")).resolve()
                    if not asset.is_relative_to(parent.dist) or not asset.is_file():
                        self.send_error(404);return
                    mime=mimetypes.guess_type(str(asset))[0] or "application/octet-stream"
                    if asset.suffix==".js":mime="text/javascript"
                    limit=200*1024*1024
                with asset.open("rb") as src:
                    size=os.fstat(src.fileno()).st_size
                    if size>limit:
                        self.send_error(413);return
                    self.send_response(200)
                    self.send_header("Content-Type",mime)
                    self.send_header("Content-Length",str(size))
                    self.send_header("Cache-Control","no-store")
                    self.send_header("X-Content-Type-Options","nosniff")
                    self.end_headers()
                    remaining=size
                    # Nicht ueber die angekuendigte Dateigroesse hinaus lesen.
                    try:
                        while remaining:
                            data=src.read(min(remaining,1024*1024))
                            if not data:break
                            self.wfile.write(data)
                            remaining-=len(data)
                    except OSError:pass
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,name="omoi-web",daemon=True)
        self.thread.start()
        return self.server.server_address[1]
    def close(self):
        server,thread=self.server,self.thread
        self.server=self.thread=None
        if server:
            server.shutdown()
            server.server_close()
        if thread:thread.join(timeout=2)
