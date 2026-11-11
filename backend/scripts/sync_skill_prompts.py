"""Backfill skill_versions.prompt_template to mirror the prompts in code.

Git-anchored versioning, Step 1. Idempotent and safe: sync_active_prompt
never overwrites a real, changed prompt (that's the approval gate's job) —
it only backfills the placeholder seed with the prompt that's already
running, so the registry becomes a faithful mirror of code.

Run in the api (or worker) container after deploy:
  docker compose -f deploy/docker-compose.prod.yml exec -T -e PYTHONPATH=/app \
    api python scripts/sync_skill_prompts.py
"""

from app.db import SessionLocal
from app.services.ai import (
    SKILL_PROMPT_VERSIONS,
    build_messages_for_job,
    get_skill_prompt_template,
)
from app.services.skill_engine import (
    get_active_skill_version,
    prompt_fingerprint,
    sync_active_prompt,
)

# skill_id -> the worker `kind` that runs it (for the byte-identity check)
_SKILL_TO_KIND = {
    "scope_analyzer": "scope_analysis",
    "estimate_generator": "estimate_generation",
    "document_summary": "document_summary",
}


def main() -> None:
    db = SessionLocal()
    try:
        print("=== SYNC ===")
        for skill_id in SKILL_PROMPT_VERSIONS:
            print(sync_active_prompt(db, skill_id))
        db.commit()

        print("\n=== VERIFY: registry row mirrors code ===")
        for skill_id in SKILL_PROMPT_VERSIONS:
            template = get_skill_prompt_template(skill_id) or ""
            row = get_active_skill_version(db, skill_id)
            code_fp = prompt_fingerprint(template)
            stored_fp = prompt_fingerprint(row.prompt_template) if row else None
            match = code_fp == stored_fp
            print(
                f"{skill_id:20} v{row.version if row else '?':5} "
                f"code={code_fp} stored={stored_fp} match={match} len={len(template)}"
            )

        print("\n=== VERIFY: template is byte-identical to what the worker sends ===")
        # scope/estimate: system prompt == the template verbatim.
        # document_summary: template is the stable prefix of the composed system.
        for skill_id, kind in _SKILL_TO_KIND.items():
            template = get_skill_prompt_template(skill_id) or ""
            try:
                system, _, _ = build_messages_for_job(kind, {})
            except Exception as e:  # noqa: BLE001 — builders may need real input
                print(f"{skill_id:20} (skipped sent-check: {type(e).__name__})")
                continue
            if skill_id == "document_summary":
                ok = (system or "").startswith(template)
                print(f"{skill_id:20} sent.startswith(template)={ok}")
            else:
                print(f"{skill_id:20} sent == template: {system == template}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
