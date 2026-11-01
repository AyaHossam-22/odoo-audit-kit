"""The read-only guarantee is the main promise of this toolkit, so it is tested."""
import pytest

from audit.connection import READ_ONLY_METHODS, OdooConnection, ReadOnlyViolation


class _FakeObject:
    def __init__(self):
        self.calls = []

    def execute_kw(self, db, uid, key, model, method, args, kwargs):
        self.calls.append((model, method))
        return []


def _connection():
    conn = OdooConnection("http://localhost:8069", "db", "user", "key")
    conn.uid = 1
    conn._object = _FakeObject()
    return conn


@pytest.mark.parametrize(
    "method",
    ["write", "create", "unlink", "copy", "action_post", "button_confirm", "_write", "execute", "sudo"],
)
def test_write_and_action_methods_are_blocked(method):
    conn = _connection()
    with pytest.raises(ReadOnlyViolation):
        conn.call("res.partner", method, [[1]])
    assert conn._object.calls == []  # nothing reached the server


def test_only_reading_methods_are_allowed():
    assert READ_ONLY_METHODS == {"search", "search_read", "read", "search_count", "fields_get", "read_group"}


def test_read_methods_reach_the_server():
    conn = _connection()
    conn.call("res.partner", "search_read", [[]], {"fields": ["name"]})
    assert conn._object.calls == [("res.partner", "search_read")]


def test_requires_connect_first():
    conn = OdooConnection("http://localhost:8069", "db", "user", "key")
    with pytest.raises(RuntimeError):
        conn.call("res.partner", "search", [[]])


def test_source_never_mentions_write_calls():
    """Guard against someone adding a mutating call to the checks later."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    pattern = re.compile(r"""["'](write|create|unlink|copy)["']""")
    for path in list((root / "audit" / "checks").glob("*.py")) + [root / "run_audit.py"]:
        assert not pattern.search(path.read_text(encoding="utf-8")), path
