"""Project notes: the write path and how images survive being stored.

The image question is the whole reason these tests exist. R2 presigns expire
after minutes, a `data:` URI is silently dropped by react-markdown's URL
sanitizer, and `content_md` is stored for years — so the markdown holds a
document id and the URL is minted on every read. These tests pin that down,
and pin down that a reference cannot reach across projects.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.routers.workbench import (
    _DOC_REF,
    _resolve_narrative_images,
    create_narrative,
    get_narrative,
    list_narratives,
    revise_narrative,
)


def test_doc_refs_are_extracted_and_junk_is_ignored():
    good = str(uuid.uuid4())
    md = (f"![perimeter](doc:{good})\n"
          f"![upper](doc:{good.upper()})\n"
          "![nope](doc:not-a-uuid)\n"
          "![also nope](docs:1234)\n"
          "plain text mentioning doc: and nothing else\n")
    found = {m.group(1).lower() for m in _DOC_REF.finditer(md)}
    assert found == {good}, "only well-formed uuids after `doc:` count"


def _mk_project(db, name):
    from app.models.project import Project

    p = Project(name=name, slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
                status="preconstruction")
    db.add(p)
    db.flush()
    return p


def _mk_document(db, project_id, key):
    from app.models.document import Document

    d = Document(project_id=project_id, category="drawing", phase="construction",
                 filename=key.rsplit("/", 1)[-1], r2_key=key, mime="image/png")
    db.add(d)
    db.flush()
    return d


@pytest.fixture()
def no_r2(monkeypatch):
    """presign_get is imported inside the resolver, so patch it at the source."""
    import app.services.r2 as r2

    monkeypatch.setattr(r2, "presign_get",
                        lambda key, expires=900: f"https://r2.test/{key}?exp={expires}")


def test_image_ref_resolves_to_a_fresh_url(db_session, no_r2):
    p = _mk_project(db_session, "Example")
    d = _mk_document(db_session, p.id, "docs/perimeter.png")

    images = _resolve_narrative_images(db_session, p.id, f"![p](doc:{d.id})")

    assert images == {str(d.id): f"https://r2.test/docs/perimeter.png?exp=3600"}, (
        "the URL is minted at read time, with a window long enough to read a brief")


def test_a_reference_cannot_reach_into_another_project(db_session, no_r2):
    mine = _mk_project(db_session, "Example")
    theirs = _mk_project(db_session, "Someone else")
    foreign = _mk_document(db_session, theirs.id, "docs/private.png")

    images = _resolve_narrative_images(db_session, mine.id, f"![x](doc:{foreign.id})")

    assert images == {}, "a doc id from another project must not resolve"


def test_archived_documents_do_not_resolve(db_session, no_r2):
    p = _mk_project(db_session, "Example")
    d = _mk_document(db_session, p.id, "docs/old.png")
    d.archived = True
    db_session.flush()

    assert _resolve_narrative_images(db_session, p.id, f"![x](doc:{d.id})") == {}


def test_revision_appends_a_version_and_leaves_the_original_intact(db_session, no_r2):
    p = _mk_project(db_session, "Example")
    user = SimpleNamespace(id=None, email="amir@builtbybrio.com", role="superadmin")

    first = create_narrative(
        p.id, {"title": "Site brief", "content_md": "dig starts Tuesday"},
        db=db_session, user=user)
    assert first["version_number"] == 1
    assert first["parent_table"] == "projects"

    second = revise_narrative(
        p.id, uuid.UUID(first["id"]), {"content_md": "dig starts Wednesday"},
        db=db_session, user=user)
    assert second["version_number"] == 2
    assert second["supersedes"] == first["id"]

    # The point of versioning: what the site was told last week still exists.
    old = get_narrative(p.id, uuid.UUID(first["id"]), db=db_session, _=user)
    assert old["content_md"] == "dig starts Tuesday"
    assert old["images"] == {}
    new = get_narrative(p.id, uuid.UUID(second["id"]), db=db_session, _=user)
    assert new["content_md"] == "dig starts Wednesday"
    assert new["title"] == "Site brief", "title carries forward when not restated"


def test_a_revised_narrative_is_marked_superseded(db_session, no_r2):
    """The index must say which version is live.

    Every revision is a new row, so without this the list shows v1 beside v2
    with nothing distinguishing them and a reader works from stale content.
    """
    from app.routers.workbench import list_narratives

    p = _mk_project(db_session, "Example")
    user = SimpleNamespace(id=None, email="amir@builtbybrio.com", role="superadmin")

    a = create_narrative(p.id, {"title": "Brief", "content_md": "first"},
                         db=db_session, user=user)
    untouched = create_narrative(p.id, {"title": "Other", "content_md": "only ever one"},
                                 db=db_session, user=user)
    b = revise_narrative(p.id, uuid.UUID(a["id"]), {"content_md": "second"},
                         db=db_session, user=user)

    rows = {r["id"]: r for r in list_narratives(p.id, db=db_session, _=user)}
    assert rows[uuid.UUID(a["id"])]["is_superseded"] is True
    assert rows[uuid.UUID(b["id"])]["is_superseded"] is False
    assert rows[uuid.UUID(untouched["id"])]["is_superseded"] is False, (
        "a note that was never revised is current, not superseded")

    # The old version must point forward, so an old link is not a dead end.
    old = get_narrative(p.id, uuid.UUID(a["id"]), db=db_session, _=user)
    assert old["superseded_by"] == {"id": b["id"], "version_number": 2}
    new_ = get_narrative(p.id, uuid.UUID(b["id"]), db=db_session, _=user)
    assert new_["superseded_by"] is None


def test_revising_a_superseded_version_is_refused(db_session, no_r2):
    """Forking the history is the failure, and it is silent without this.

    It happened for real during the first backfill: a script held the v1 id,
    revised it after v2 already existed, and left two rows both claiming v2 and
    both looking current. The second edit was also applied to a stale body.
    """
    from fastapi import HTTPException

    p = _mk_project(db_session, "Example")
    user = SimpleNamespace(id=None, email="amir@builtbybrio.com", role="superadmin")

    v1 = create_narrative(p.id, {"title": "Brief", "content_md": "first"},
                          db=db_session, user=user)
    v2 = revise_narrative(p.id, uuid.UUID(v1["id"]), {"content_md": "second"},
                          db=db_session, user=user)

    with pytest.raises(HTTPException) as exc:
        revise_narrative(p.id, uuid.UUID(v1["id"]), {"content_md": "branches off v1"},
                         db=db_session, user=user)
    assert exc.value.status_code == 409
    assert v2["id"] in exc.value.detail, "the refusal must name the live version to revise"

    # Nothing was written, and the chain is still linear.
    rows = list_narratives(p.id, db=db_session, _=user)
    assert len(rows) == 2, "the refused revision must not have created a row"

    # The same edit against the live version succeeds.
    v3 = revise_narrative(p.id, uuid.UUID(v2["id"]), {"content_md": "third"},
                          db=db_session, user=user)
    assert v3["version_number"] == 3


def test_empty_body_is_rejected(db_session):
    from fastapi import HTTPException

    p = _mk_project(db_session, "Example")
    user = SimpleNamespace(id=None, email="amir@builtbybrio.com", role="superadmin")
    with pytest.raises(HTTPException):
        create_narrative(p.id, {"title": "Empty", "content_md": "   "},
                         db=db_session, user=user)
    with pytest.raises(HTTPException):
        create_narrative(p.id, {"title": "", "content_md": "body"},
                         db=db_session, user=user)
