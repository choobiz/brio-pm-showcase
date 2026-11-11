#!/usr/bin/env python3
"""Import the permit-derived prospect list into the CRM.

Source is the target list built by the Bid Radar pipeline in brio-marketing-hub:
4,916 firms resolved across eight municipal permit sources, enriched with contact
details and verified against the BC corporate registry.

Two decisions worth knowing before running this:

  Contacts come in through `find_or_create_client`, the existing matcher, rather
  than a bulk insert. Anyone already in brio-pm as a real client is MATCHED, not
  duplicated — the whole point of keeping one contact table.

  Opportunities are created for TIER A AND B ONLY. Creating 4,916 would flood the
  queue on day one and break the product's only promise, which is that opening it
  shows you the handful of people to contact this week. Tier C/D arrive as
  contacts with no opportunity and get promoted when wanted.

Usage:
  python3 -m scripts.import_crm_targets --dry-run
  python3 -m scripts.import_crm_targets --apply --owner amir@sister-co.example
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

DEFAULT_SOURCE = (Path.home() / "claude" / "brio-marketing-hub" / "builtbybrio-seo"
                  / "google-ads" / "data" / "targets")

# Only these get an opportunity, and therefore a place in the queue.
QUEUED_TIERS = {"A", "B"}

# Staggered so 368 opportunities don't all land on the same morning. Tier A is
# worth contacting first, so it comes due first.
TIER_OFFSET_DAYS = {"A": 0, "B": 7}

TIER_ACTION = {
    "A": "Call — they file 20+ permits a year, worth a direct conversation",
    "B": "Email referencing two or three of their actual recent projects",
}


def prospect_kind(current: str | None, *, created: bool) -> str:
    """Kind for a contact touched by the import.

    A firm this import created is a prospect. A firm it matched keeps its kind,
    so a real client is never downgraded. The first version tested
    `c.kind != "client"` AFTER find_or_create_client had flushed the row with
    the column default "client", so it never fired: all 4,857 imported firms
    landed as clients and filled the Clients page.
    """
    if created:
        return "prospect"
    return current or "client"


def latest_targets(src: Path) -> Path:
    fs = sorted(src.glob("*-targets.json"))
    if not fs:
        sys.exit(f"no target list under {src}")
    return fs[-1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--owner", help="email of the user to own new opportunities")
    ap.add_argument("--track", default="construction")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if not a.apply:
        a.dry_run = True

    path = latest_targets(Path(a.source))
    recs = json.loads(path.read_text())
    if a.limit:
        recs = recs[:a.limit]
    print(f"source: {path.name}  ({len(recs):,} firms)")

    stats = Counter()
    for r in recs:
        stats[f"tier_{r.get('tier','?')}"] += 1
        if r.get("registry_status") == "HIS":
            stats["dissolved"] += 1
        if r.get("email") or r.get("phone"):
            stats["with_contact"] += 1
        if r.get("contact_confidence") == "high":
            stats["high_confidence"] += 1
        if r.get("tier") in QUEUED_TIERS and r.get("registry_status") != "HIS":
            stats["will_get_opportunity"] += 1

    print("\nwhat this would import:")
    for k in ("tier_A", "tier_B", "tier_C", "tier_D"):
        print(f"  {k:<22} {stats[k]:>6,}")
    print(f"  {'with a contact':<22} {stats['with_contact']:>6,}")
    print(f"  {'high confidence':<22} {stats['high_confidence']:>6,}")
    print(f"  {'DISSOLVED (excluded)':<22} {stats['dissolved']:>6,}")
    print(f"  {'→ get an opportunity':<22} {stats['will_get_opportunity']:>6,}"
          f"   (tiers {'/'.join(sorted(QUEUED_TIERS))} only)")

    if a.dry_run:
        print("\n[dry run] nothing written. re-run with --apply")
        return 0

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.db import SessionLocal
    from app.models.client import Client
    from app.models.crm import CrmOpportunity
    from app.models.user import User
    from app.services.clients import find_or_create_client
    from sqlalchemy import select

    db = SessionLocal()
    owner_id = None
    if a.owner:
        u = db.execute(select(User).where(User.email == a.owner)).scalar_one_or_none()
        if not u:
            sys.exit(f"no user with email {a.owner}")
        owner_id = u.id
        print(f"owner: {u.name or u.email}")

    made = Counter()
    today = date.today()
    try:
        for i, r in enumerate(recs, 1):
            name = (r.get("firm") or "").strip()
            if not name:
                made["skipped_no_name"] += 1
                continue

            res = find_or_create_client(
                db, name=name,
                email=r.get("email") or None,
                phone=r.get("phone") or None,
                address=r.get("address") or None,
            )
            c: Client = res.client
            # `created` is the field the service actually exposes. The first
            # version guessed at `matched`, which does not exist, so getattr
            # defaulted to False and every row — including 612 that were MERGED
            # into an existing contact — was reported as "created". The import
            # looked clean while quietly collapsing distinct firms.
            made["created" if getattr(res, "created", True) else "matched"] += 1

            c.kind = prospect_kind(c.kind, created=getattr(res, "created", False))
            for field, key in (
                ("segment", "role"), ("tier", "tier"),
                ("permits_total", "permits_total"),
                ("permits_as_applicant", "permits_as_applicant"),
                ("value_as_applicant", "value_as_applicant"),
                ("cities", "cities"), ("top_use_category", "top_use_category"),
                ("permit_name", "permit_name"),
                ("bc_registration", "bc_registration"),
                ("legal_name", "legal_name"), ("entity_type", "entity_type"),
                ("registry_status", "registry_status"),
                ("contact_confidence", "contact_confidence"),
                ("website", "website"),
            ):
                v = r.get(key)
                if v not in (None, ""):
                    setattr(c, field, v)

            # HIS = historical. These are dissolved companies and must never be
            # queued — the flag lives on registry_status, not entity_status.
            if r.get("registry_status") == "HIS":
                c.excluded_reason = "dissolved per BC registry"
                made["flagged_dissolved"] += 1

            tier = r.get("tier")
            if tier in QUEUED_TIERS and not c.excluded_reason:
                exists = db.execute(
                    select(CrmOpportunity).where(
                        CrmOpportunity.client_id == c.id,
                        CrmOpportunity.track == a.track)
                ).scalar_one_or_none()
                if not exists:
                    db.add(CrmOpportunity(
                        client_id=c.id, track=a.track, status="new",
                        next_action=TIER_ACTION.get(tier, "Make contact"),
                        next_action_at=today + timedelta(
                            days=TIER_OFFSET_DAYS.get(tier, 14)),
                        owner_id=owner_id,
                    ))
                    made["opportunities"] += 1

            if i % 250 == 0:
                db.commit()
                print(f"  … {i:,}/{len(recs):,}", file=sys.stderr)
        db.commit()
    finally:
        db.close()

    print("\nimported:")
    for k, v in sorted(made.items()):
        print(f"  {k:<22} {v:>6,}")

    # Reconcile against the database rather than trusting the counters. The
    # first run reported success while producing 612 fewer contacts than rows
    # and 71 opportunities on wrong-tier records; nothing in the output said so.
    db2 = SessionLocal()
    try:
        from sqlalchemy import func
        n_clients = db2.execute(select(func.count()).select_from(Client)).scalar()
        n_opps = db2.execute(select(func.count()).select_from(CrmOpportunity)).scalar()
        n_bad = db2.execute(
            select(func.count()).select_from(CrmOpportunity)
            .join(Client, Client.id == CrmOpportunity.client_id)
            .where(Client.tier.notin_(list(QUEUED_TIERS)))
        ).scalar()
        print("\nreconciliation against the database:")
        print(f"  rows in source        {len(recs):>6,}")
        print(f"  contacts in db        {n_clients:>6,}"
              f"   {'✓' if n_clients == len(recs) else '⚠️  MERGES OCCURRED'}")
        print(f"  opportunities in db   {n_opps:>6,}")
        print(f"  on non-A/B contacts   {n_bad:>6,}   {'✓' if n_bad == 0 else '⚠️  WRONG TIER'}")
        if n_clients != len(recs) or n_bad:
            print("\n  ⚠️  Do not treat this import as clean. Investigate before use.")
            return 1
    finally:
        db2.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
