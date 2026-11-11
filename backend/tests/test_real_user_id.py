"""A real user whose id starts with 0 is still a real user (found 2026-11-02).

Routes recorded created_by / decided_by as `UUID(user.id) if user.id[0] != "0"`,
meant to skip the all-zero dev user. It also dropped 1 in 16 real users: their
change-order decisions, messages and selections had no author, and the
office-notification query failed on the NULL ("could not determine data type
of parameter") — a flaky CI failure whenever a test user's random id began
with 0.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import DEV_USER_ID, CurrentUser, real_user_id  # noqa: E402
from app.services.notify import _office_emails  # noqa: E402


def test_an_id_starting_with_zero_is_kept():
    uid = "0" + uuid4().hex[1:]
    u = CurrentUser(id=str(UUID(uid)), email="x@t.local", role="manager")
    assert real_user_id(u) == UUID(uid)


def test_only_the_all_zero_dev_user_has_no_id():
    assert real_user_id(CurrentUser(id=DEV_USER_ID, email="dev@brio.local", role="superadmin")) is None


def test_no_route_uses_the_first_character_test_any_more():
    app_dir = ROOT / "app"
    bad = [str(p.relative_to(ROOT)) for p in app_dir.rglob("*.py")
           if 'id[0] != "0"' in p.read_text() and p.name != "auth.py"]
    assert not bad, bad


def test_office_recipients_query_takes_no_author(db_session):
    """created_by None (the dev user, or an import) must not break the query."""
    assert isinstance(_office_emails(db_session, str(uuid4()), None), list)
