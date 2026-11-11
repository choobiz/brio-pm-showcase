"""The guard on the destructive DB fixtures.

On 2026-08-31 this suite was run inside the production API container to borrow a
Python 3.12 interpreter. TEST_DATABASE_URL was unset, the fixture fell back to
DATABASE_URL, and `worker_db`'s wipe destroyed 341 projects and ~5,300 document
rows. These tests exist so that cannot happen twice.
"""
import pytest

from tests.conftest import (
    MISSING_URL_MESSAGE, UnsafeTestDatabase, _assert_safe_to_wipe, _resolve_url,
)


def test_an_absent_url_is_not_inherited_from_DATABASE_URL(monkeypatch):
    """The exact failure. DATABASE_URL must never be picked up by these fixtures."""
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL",
                       "postgresql+psycopg://brio:pw@db:5432/brio_pm")
    assert _resolve_url() is None
    assert "wiped on 2026-08-31" in MISSING_URL_MESSAGE


def test_a_production_looking_name_is_refused(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL",
                       "postgresql+psycopg://brio:pw@db:5432/brio_pm")
    with pytest.raises(UnsafeTestDatabase, match="does not look like a throwaway"):
        _resolve_url()


@pytest.mark.parametrize("name", ["brio_pm_test", "test_brio", "scratch_db",
                                  "throwaway1"])
def test_throwaway_names_are_accepted(monkeypatch, name):
    monkeypatch.setenv("TEST_DATABASE_URL",
                       f"postgresql+psycopg://brio:pw@localhost:5434/{name}")
    assert _resolve_url().endswith(name)


class _Conn:
    def __init__(self, n, has_table=True):
        self._n, self._has = n, has_table

    def execute(self, stmt):
        sql = str(stmt)
        return _Scalar(self._has if "to_regclass" in sql else self._n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Scalar:
    def __init__(self, v):
        self._v = v

    def scalar(self):
        return self._v


class _Engine:
    def __init__(self, n, has_table=True):
        self._n, self._has = n, has_table

    def connect(self):
        return _Conn(self._n, self._has)


def test_a_database_holding_real_projects_is_never_wiped():
    """The backstop. It survives someone pointing TEST_DATABASE_URL at the wrong
    host with a plausible-looking name, and it is the guard that would have
    stopped the incident: production held 341 projects."""
    with pytest.raises(UnsafeTestDatabase, match="holds 341 projects"):
        _assert_safe_to_wipe(_Engine(341))


def test_a_nearly_empty_database_is_allowed():
    _assert_safe_to_wipe(_Engine(0))
    _assert_safe_to_wipe(_Engine(3))          # fixtures may leave a few behind


def test_a_fresh_schema_with_no_projects_table_is_allowed():
    _assert_safe_to_wipe(_Engine(0, has_table=False))


def test_the_row_guard_reads_before_it_writes():
    """It must run BEFORE the delete, not after — a check that runs afterwards
    reports the damage instead of preventing it."""
    import inspect

    import tests.conftest as c
    src = inspect.getsource(c)
    wipe = src[src.index("    def _wipe() -> None:"):]
    guard = wipe.index("_assert_safe_to_wipe")
    delete = wipe.index("__table__.delete()")
    assert guard < delete, "the safety check must precede the delete"
