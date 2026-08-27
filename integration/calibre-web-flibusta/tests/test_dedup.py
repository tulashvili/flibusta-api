import sys, types

cps = types.ModuleType("cps")
cps.db = types.SimpleNamespace(Books=object, Identifiers=types.SimpleNamespace(type=None, val=None))
sys.modules.setdefault("cps", cps)

from cps_flibusta import dedup


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def join(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeSession:
    def __init__(self, result):
        self._result = result

    def query(self, *a, **k):
        return _FakeQuery(self._result)


class _FakeCalibreDb:
    def __init__(self, result):
        self.session = _FakeSession(result)


def test_find_existing_returns_book_id_when_identifier_matches():
    class Book:  # noqa
        id = 42
    assert dedup.find_existing(_FakeCalibreDb(Book()), 416925) == 42


def test_find_existing_returns_none_when_no_match():
    assert dedup.find_existing(_FakeCalibreDb(None), 416925) is None
