"""Project isolation, as a structural guard rather than a hope.

⚠️ WHY A STRUCTURAL TEST. `quantity-overrides` shipped with SIX endpoints and
ZERO access checks: any authenticated user could list, delete or review the
quantities of any project. It went unnoticed because nothing tested for the
absence of a check, and nothing can — an endpoint that forgot one looks exactly
like an endpoint that did not need one.

So this asserts the property directly: every route in a router that reads or
writes project-scoped data resolves the project and checks access. A new
endpoint that forgets fails here rather than in production.

The routers below are declared explicitly rather than discovered, because the
list is a decision about which data is project-scoped and that decision should
be visible and reviewed, not inferred.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: Routers whose every endpoint touches one project's data.
#: `documents` already complied; `quantity_overrides` did not until 2026-09-28.
#: `projects` did not until 2026-10-12 (any signed-in user could read or edit any
#: project, the client included).
#: `drawing_analysis` did not until 2026-10-12 either (read, build, approve and
#: the sheet view all took any signed-in user).
#: `scopes` did not until 2026-10-12 (and PATCH took any signed-in user).
GUARDED_ROUTERS = ["quantity_overrides", "documents", "drawings", "projects",
                   "drawing_analysis", "scopes", "workbench", "estimates",
                   "ai_jobs", "drafts", "daily_reports", "time_tracking",
                   "finish", "money", "milestones", "submittals"]

#: Endpoints that legitimately need no project check, with the reason. Anything
#: not listed here must check — an empty reason is not accepted.
EXEMPT: dict[str, str] = {
    "documents:GET:/checklist": "static reference data, no project involved",
    "documents:GET:/search": "resolves scope from the caller's own access instead",
    "documents:POST:/suggest-categories": "filenames in, guesses out; reads and writes nothing",
    "projects:GET:": "the list itself; scoped by accessible_project_ids instead",
    "projects:POST:": "creates a project, so there is none to check yet; office-only",
    "time_tracking:GET:/me/active-punch": "the caller's own open punch only",
    "time_tracking:POST:/me/punch-out": "closes the caller's own open punch only",
}


def _endpoints(module: str):
    src = (ROOT / "app" / "routers" / f"{module}.py").read_text()
    parts = re.split(r"(@router\.(?:get|post|patch|put|delete)\([^)]*\))", src)
    for i in range(1, len(parts), 2):
        m = re.search(r'@router\.(\w+)\(\s*"([^"]*)"', parts[i])
        if not m:
            continue
        body = parts[i + 1].split("@router")[0]
        yield m.group(1).upper(), m.group(2) or "", body


def _router_level_guard(module: str) -> bool:
    """True when the module's APIRouter(...) itself declares the project guard,
    which then covers every route under a `{project_id}` prefix."""
    src = (ROOT / "app" / "routers" / f"{module}.py").read_text()
    m = re.search(r"router = APIRouter\((.*?)\)\n", src, re.S)
    return bool(m and "{project_id}" in m.group(1) and "require_project_member" in m.group(1))


@pytest.mark.parametrize("module", GUARDED_ROUTERS)
def test_every_project_scoped_endpoint_checks_access(module):
    if _router_level_guard(module):
        return
    missing = []
    for verb, path, body in _endpoints(module):
        key = f"{module}:{verb}:{path}"
        if key in EXEMPT:
            assert EXEMPT[key], f"{key} is exempt with no stated reason"
            continue
        if "assert_project_access" not in body:
            missing.append(key)
    assert not missing, (
        "these endpoints read or write project data without checking access: "
        + ", ".join(missing)
        + " — add assert_project_access, or add an EXEMPT entry with a reason"
    )


@pytest.mark.parametrize("module", GUARDED_ROUTERS)
def test_the_guard_is_actually_resolvable_in_the_module(module):
    """⚠️ A check that is written but not IMPORTED is worse than no check.

    The first version of this suite grepped for the call and passed while
    `assert_project_access` was undefined in quantity_overrides — so five
    endpoints raised NameError, a 500, for every caller including superadmins.
    Broken rather than merely unguarded, and the string-matching test could not
    see it.

    Importing the module and asking for the attribute is the only form of this
    check that cannot be satisfied by text.
    """
    import importlib

    import app.auth as auth_mod

    mod = importlib.import_module(f"app.routers.{module}")
    src = (ROOT / "app" / "routers" / f"{module}.py").read_text()

    # Every guard the module MENTIONS must also be resolvable in it. Checking
    # only assert_project_access was not enough: require_office was later used
    # without being imported, and a role guard sits in a module-level Depends(),
    # so that one failed at import rather than at call time. Both are the same
    # mistake and both belong here.
    for guard in [n for n in auth_mod.__all__ if n.startswith(("require_", "assert_"))]:
        if guard in src:
            assert hasattr(mod, guard), (
                f"app/routers/{module}.py uses {guard} but does not import it"
            )


def test_the_exempt_list_has_not_gone_stale():
    """An exemption for an endpoint that no longer exists hides a real gap."""
    live = {f"{m}:{v}:{p}" for m in GUARDED_ROUTERS for v, p, _ in _endpoints(m)}
    assert not (set(EXEMPT) - live), f"exemptions for routes that no longer exist: {set(EXEMPT) - live}"


# ── the scope helper both cross-project surfaces depend on ────────────────

def test_a_non_member_sees_no_projects(db_session):
    from app.auth import CurrentUser, accessible_project_ids
    from sqlalchemy import text as _T

    db = db_session
    pid, uid = uuid4(), uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) "
                  "values (:i, 'Someone elses', :s, 'preconstruction')"),
               {"i": str(pid), "s": f"x-{str(pid)[:8]}"})
    db.execute(_T("insert into users (id, email, role) values (:i, :e, 'manager')"),
               {"i": str(uid), "e": f"nobody-{str(uid)[:8]}@example.com"})
    db.flush()

    user = CurrentUser(id=uid, email="nobody@example.com", role="manager")
    assert accessible_project_ids(db, user) == []


def test_a_member_sees_only_their_own(db_session):
    from app.auth import CurrentUser, accessible_project_ids
    from sqlalchemy import text as _T

    db = db_session
    mine, theirs, uid = uuid4(), uuid4(), uuid4()
    for p, n in ((mine, "Mine"), (theirs, "Theirs")):
        db.execute(_T("insert into projects (id, name, slug, status) "
                      "values (:i, :n, :s, 'preconstruction')"),
                   {"i": str(p), "n": n, "s": f"p-{str(p)[:8]}"})
    db.execute(_T("insert into users (id, email, role) values (:i, :e, 'manager')"),
               {"i": str(uid), "e": f"m-{str(uid)[:8]}@example.com"})
    db.execute(_T("insert into project_members (project_id, user_id, role) "
                  "values (:p, :u, 'manager')"), {"p": str(mine), "u": str(uid)})
    db.flush()

    user = CurrentUser(id=uid, email="m@example.com", role="manager")
    assert accessible_project_ids(db, user) == [mine]


def test_a_superadmin_sees_everything_without_membership(db_session):
    from app.auth import CurrentUser, accessible_project_ids
    from sqlalchemy import text as _T

    db = db_session
    pid, uid = uuid4(), uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) "
                  "values (:i, 'Any', :s, 'preconstruction')"),
               {"i": str(pid), "s": f"a-{str(pid)[:8]}"})
    db.execute(_T("insert into users (id, email, role) values (:i, :e, 'superadmin')"),
               {"i": str(uid), "e": f"s-{str(uid)[:8]}@example.com"})
    db.flush()

    user = CurrentUser(id=uid, email="s@example.com", role="superadmin")
    assert pid in accessible_project_ids(db, user)
