"""Prepared documents: suppliers, company practices, prep requests, drawing
facts, an API spend ledger with a monthly cap, and the Mac worker heartbeat.

Seeds (Amir, 2026-10-13): Sample Lumber and Sample Form Rentals, the practices
behind his five corrections to the 1234 forming order, and an $8/month cap on
API spend for preparing documents.

Revision ID: 0071_prepared_documents
Revises: 0070_money_sent_times
"""
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0071_prepared_documents"
down_revision = "0070_money_sent_times"
branch_labels = None
depends_on = None

SRC = "Amir, corrections to the 1234 forming order, 2026-10-13"
PRACTICES = [
    ("foundation_forming",
     "Footing form stakes are bought ready-made from the lumber supplier (24 in, pointed wood stakes). "
     "Do not cut stakes from 2x4s."),
    ("foundation_forming",
     "Foundation wall forms are RENTED WOOD PANELS (not aluminium or steel-ply), from Sample Form Rentals. "
     "The rental company supplies every forming accessory: ties, wedges or clamps, walers, footing keys, "
     "bracing and form release. Do not quantify those for them; give them the wall geometry (lengths of "
     "each face, wall thickness, height, corners, junctions) and the rental period."),
    ("foundation_forming",
     "Form height follows the wall: walls up to 3 ft above the footing use 4 ft forms."),
    ("foundation_forming",
     "Footing forms are 2x10 on edge from the lumber supplier. Stakes at 4 ft o/c maximum on both sides, "
     "plus one at every board joint and two at every corner. 2x4 spreaders across the top at 4 ft o/c."),
    ("drawings",
     "Never add an element that a drawing only titles without drawing it (1234: 'Detail B, L-shaped "
     "retaining wall' is a heading with nothing under it, and no retaining wall is built). Report it as a "
     "conflict for a person to decide instead."),
    ("drawings",
     "Read interior footing and wall sizes from the drawing; do not assume they are smaller than the "
     "exterior ones (1234: the interior garage/office wall has the full 24 in footing and 8 in wall)."),
    ("suppliers",
     "Lumber: Sample Lumber. Concrete wall forms: Sample Form Rentals (1 day delivery lead time)."),
    ("orders",
     "Orders and price requests ask the supplier to confirm pricing and the delivery window, to call before "
     "substituting anything, and to round up to their bundle or pack size."),
]


def upgrade() -> None:
    uid = lambda: sa.Column("id", UUID(as_uuid=True), primary_key=True,  # noqa: E731
                            server_default=sa.text("gen_random_uuid()"))
    ts = lambda: [  # noqa: E731
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]
    op.create_table(
        "suppliers", uid(),
        sa.Column("name", sa.Text, nullable=False), sa.Column("trade", sa.Text),
        sa.Column("contact_name", sa.Text), sa.Column("email", sa.Text), sa.Column("phone", sa.Text),
        sa.Column("address", sa.Text), sa.Column("notes", sa.Text),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"), *ts())
    op.create_table(
        "company_practices", uid(),
        sa.Column("topic", sa.Text, nullable=False), sa.Column("rule", sa.Text, nullable=False),
        sa.Column("source", sa.Text),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")), *ts())
    op.create_table(
        "prep_requests", uid(),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("requested_by_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("request", sa.Text, nullable=False),
        sa.Column("document_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.Text, nullable=False, server_default="queued", index=True),
        sa.Column("stage", sa.Text, nullable=False, server_default="plan"),
        sa.Column("engine", sa.Text), sa.Column("api_after", sa.DateTime(timezone=True)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("plan", JSONB), sa.Column("answers", JSONB), sa.Column("correction", sa.Text),
        sa.Column("progress", JSONB, nullable=False, server_default="[]"), sa.Column("error", sa.Text),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=False, server_default="0"), *ts())
    op.create_table(
        "prepared_documents", uid(),
        sa.Column("prep_request_id", UUID(as_uuid=True), sa.ForeignKey("prep_requests.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("kind", sa.Text, nullable=False), sa.Column("title", sa.Text, nullable=False),
        sa.Column("supplier_id", UUID(as_uuid=True), sa.ForeignKey("suppliers.id", ondelete="SET NULL")),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL")), *ts())
    op.create_table(
        "drawing_facts", uid(),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("page_no", sa.Integer, nullable=False), sa.Column("facts_md", sa.Text, nullable=False),
        sa.Column("engine", sa.Text), sa.Column("model", sa.Text), *ts(),
        sa.UniqueConstraint("document_id", "page_no", name="uq_drawing_facts_page"))
    op.create_table(
        "ai_spend", uid(),
        sa.Column("purpose", sa.Text, nullable=False),
        sa.Column("prep_request_id", UUID(as_uuid=True), sa.ForeignKey("prep_requests.id", ondelete="SET NULL")),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=False), *ts())
    op.create_index("ix_ai_spend_created", "ai_spend", ["created_at"])
    op.create_table(
        "app_settings",
        sa.Column("key", sa.Text, primary_key=True), sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_by_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.create_table(
        "worker_heartbeats",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("info", JSONB, nullable=False, server_default="{}"))

    conn = op.get_bind()
    conn.execute(sa.text("INSERT INTO suppliers (name, trade) VALUES "
                         "('Sample Lumber', 'Lumber and building materials'), "
                         "('Sample Form Rentals', 'Concrete form rentals')"))
    for topic, rule in PRACTICES:
        conn.execute(sa.text("INSERT INTO company_practices (topic, rule, source) VALUES (:t, :r, :s)"),
                     {"t": topic, "r": rule, "s": SRC})
    conn.execute(sa.text("INSERT INTO app_settings (key, value, updated_at) VALUES "
                         "('prep_api_monthly_cap_usd', cast(:v as jsonb), now())"),
                 {"v": json.dumps({"usd": 8})})


def downgrade() -> None:
    for t in ("worker_heartbeats", "app_settings", "ai_spend", "drawing_facts", "prepared_documents",
              "prep_requests", "company_practices", "suppliers"):
        op.drop_table(t)
