"""Static contract checks between source modules, HTML, JS and Qt."""
import ast
from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "python" / "omoi_mini"
FRONTEND = ROOT / "web" / "src" / "main.js"


def tree(name):
    return ast.parse((PACKAGE / (name + ".py")).read_text(encoding="utf-8-sig"))


class IdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        value = dict(attrs).get("id")
        if value is not None:
            self.ids.append(value)


def test_relative_python_imports_exist():
    modules = {p.stem for p in PACKAGE.glob("*.py")}
    for name in modules:
        for node in ast.walk(tree(name)):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                assert node.module.split(".")[0] in modules, (name, node.module)


def test_html_ids_unique_and_literal_js_references_exist():
    parser = IdParser()
    parser.feed((ROOT / "web" / "index.html").read_text(encoding="utf-8"))
    assert len(parser.ids) == len(set(parser.ids))
    references = set(re.findall(r'\$\(["\']([^"\']+)["\']\)', FRONTEND.read_text(encoding="utf-8")))
    assert references
    assert not references.difference(parser.ids)


def test_relative_web_imports_exist():
    for source in (ROOT / "web" / "src").glob("*.js"):
        code = source.read_text(encoding="utf-8")
        for name in re.findall(r'(?:from\s+|import\s+)["\'](\.[^"\']+)["\']', code):
            assert (source.parent / name).is_file(), (source.name, name)


def test_js_bridge_calls_are_exposed_as_qt_slots():
    bridge = next(n for n in tree("gui").body if isinstance(n, ast.ClassDef) and n.name == "Bridge")
    slots = {
        n.name for n in bridge.body if isinstance(n, ast.FunctionDef)
        and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "Slot"
                for d in n.decorator_list)
    }
    code = FRONTEND.read_text(encoding="utf-8")
    called = set(re.findall(r'\bsend\(["\']([A-Za-z][A-Za-z0-9]+)["\']', code))
    called.update(re.findall(r'\bbridge\.([A-Za-z][A-Za-z0-9]+)\(', code))
    # The warmup button deliberately switches between two slots.
    for pair in re.findall(r'\bsend\([^;\n]*?\?\s*"([A-Za-z][A-Za-z0-9]+)"\s*:\s*"([A-Za-z][A-Za-z0-9]+)"', code):
        called.update(pair)
    assert called
    assert not called.difference(slots)


def literal_event_types():
    events = set()
    for module in ("gui", "controller", "audio"):
        for node in ast.walk(tree(module)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "emit" and node.args):
                continue
            arg = node.args[0]
            if module == "controller" and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                events.add(arg.value)
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) and arg.func.attr == "dumps" and arg.args:
                payload = arg.args[0]
                if isinstance(payload, ast.Dict):
                    for key, value in zip(payload.keys, payload.values):
                        if isinstance(key, ast.Constant) and key.value == "type" and isinstance(value, ast.Constant):
                            events.add(value.value)
    return events


def test_backend_events_have_ui_handlers_or_are_diagnostic_only():
    code = FRONTEND.read_text(encoding="utf-8")
    handlers = set(re.findall(r'case "([^"]+)":', code))
    # These events carry measurements for tests/diagnostics, not UI commands.
    diagnostic_only = {"latency", "stt_speech_guard"}
    events = literal_event_types()
    assert events
    assert not events.difference(handlers | diagnostic_only)
