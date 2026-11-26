"""Layout geometry and DOM contract, without Qt windows or model processes."""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
import re

import pytest

from omoi_mini.window_layout import Rect, dock_geometry
from omoi_mini.gui import Bridge

ROOT = Path(__file__).resolve().parents[2]


class LayoutParser(HTMLParser):
    void = {"area","base","br","col","embed","hr","img","input","link","meta","param","source","track","wbr"}
    def __init__(self, source):
        super().__init__()
        self.stack = []
        self.nodes = {}
        self.ids = Counter()
        self.tabs = {}
        self.pages = {}
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        ident = attrs.get("id")
        if ident:
            self.ids[ident] += 1
            self.nodes[ident] = (tuple(i for _,i in self.stack if i), attrs)
        if "data-settings-tab" in attrs:
            self.tabs[attrs["data-settings-tab"]] = attrs
        if "data-settings-page" in attrs:
            self.pages[attrs["data-settings-page"]] = attrs
        if tag not in self.void:
            self.stack.append((tag,ident))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag,attrs)
        if tag not in self.void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1][0] == tag, (tag, self.stack)
        self.stack.pop()


@pytest.fixture
def dom():
    return LayoutParser((ROOT/"web/index.html").read_text(encoding="utf-8"))


def test_geometry_open_close_and_right_edge():
    screen = Rect(0,0,1920,1080)
    current = Rect(1500,100,520,720)
    opened = dock_geometry(current,screen,True,520)
    assert opened == Rect(950,100,970,720)
    assert dock_geometry(opened,screen,False,520) == Rect(950,100,520,720)


def test_geometry_small_or_negative_monitor():
    bounds = Rect(-700,40,700,550)
    opened = dock_geometry(Rect(-600,20,520,720),bounds,True,520)
    assert opened == bounds
    closed = dock_geometry(opened,bounds,False,520)
    assert closed == Rect(-700,40,520,550)


def test_bridge_dock_only_changes_geometry():
    calls = []
    fake = SimpleNamespace(window=SimpleNamespace(set_dock_open=calls.append))
    Bridge.setDockOpen(fake,True)
    Bridge.setDockOpen(fake,False)
    assert calls == [True,False]


def test_all_screens_are_separate_from_scene(dom):
    assert not dom.stack
    assert all(count == 1 for count in dom.ids.values())
    assert "stage" in dom.nodes["scene"][0]
    for name in ("settings","chat","stt-review-panel"):
        parents, attrs = dom.nodes[name]
        assert "dock" in parents and "stage" not in parents
        assert "hidden" in attrs
    for name in ("bubble","stt-diagnostics","live-transcript","mic-level","animation-preview-actions"):
        assert "stage" not in dom.nodes[name][0]
    assert "hidden" in dom.nodes["dock"][1]


def test_categories_have_one_initial_page_and_matching_targets(dom):
    expected = {"general","stt","tts","models","avatar","diagnose","advanced"}
    assert set(dom.tabs) == set(dom.pages) == expected
    assert [k for k,v in dom.pages.items() if "hidden" not in v] == ["general"]
    for key,tab in dom.tabs.items():
        assert tab["aria-controls"] == dom.pages[key]["id"]
    assert dom.tabs["general"]["aria-pressed"] == "true"
    for ident,page in {"llm_mode":"general","microphone-select":"stt","tts":"tts",
                       "standard-model-status":"models","animation-list":"avatar",
                       "runtime-diagnose":"diagnose","runtime-settings":"advanced"}.items():
        assert "settings-"+page in dom.nodes[ident][0]


def test_provider_controls_have_independent_groups(dom):
    for ident,parent in {"stt_hotwords":"stt-cuda-options",
                         "stt_auto_language":"stt-cuda-options",
                         "stt_speech_guard":"stt-filter-options",
                         "stt_profile":"stt-local-options",
                         "qwen_variant":"qwen-settings",
                         "cartesia-voice-id":"cartesia-settings"}.items():
        assert parent in dom.nodes[ident][0]
    js = (ROOT/"web/src/main.js").read_text(encoding="utf-8")
    assert 'stage.clientWidth' in js and 'stage.clientHeight' in js
    assert 'ResizeObserver(resize)' in js
    assert 'reviewTurn !== null ? "stt-review-panel" : activeScreen' in js
    for ident in re.findall(r'\$\("([\w-]+)"\)',js):
        assert ident in dom.ids, ident
