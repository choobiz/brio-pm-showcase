"""Project-type-driven division sets

Estimate generation previously told the model to bucket work into 19 generic
categories inherited from a discontinued skill's cost-categories reference.
BRIO's actual standard is different, and it differs BY PROJECT TYPE:

  · residential -> BRIO's own 18 numbered divisions, in construction sequence
  · commercial  -> CSI MasterFormat

so a single hard-coded list could never be right for both. Division sets are
therefore data, not code: adding or revising a set is a row change, and each set
is addressed by code so estimates stay reproducible.

EXISTING ESTIMATES ARE NOT TOUCHED. Amir's instruction (2026-09-27) is that
whatever is on the record stays on the record; sets apply to estimates generated
from here forward. That is also why no legacy set is seeded for the two daycare
projects — their stored estimates keep their own category names regardless.

projects.type becomes a constrained enum, but stays NULLABLE: 19 projects could
not be classified from their names and Amir declined to guess. A null type falls
back to the generic list with the gap logged, rather than silently guessing.

Revision ID: 0056_division_sets
Revises: 0055_labour_no_disc
"""

from alembic import op
import sqlalchemy as sa

revision = "0056_division_sets"
down_revision = "0055_labour_no_disc"
branch_labels = None
depends_on = None

PROJECT_TYPES = ("residential_new", "residential_reno", "commercial_ti", "commercial_new")

# ── BRIO residential — 18 divisions, numbered in CONSTRUCTION SEQUENCE. The
# number is part of the name. 01 carries no trade contingency (general
# conditions/permits); every trade division closes at 5%.
BRIO_18 = [
    ("01", "General Conditions, Permits & Closeout", 0),
    ("02", "Excavation, Demolition & Site Clearing", 5),
    ("03", "Foundation & Concrete", 5),
    ("04", "Rough Carpentry & Framing", 5),
    ("05", "Doors, Frames, Hardware & Glazing", 5),
    ("06", "Insulation, Drywall & Acoustic", 5),
    ("07", "Painting & Finishes", 5),
    ("08", "Flooring & Tile", 5),
    ("09", "Millwork & Casework", 5),
    ("10", "Specialties & Fire Detection", 5),
    ("11", "Appliances & Equipment", 5),
    ("12", "Mechanical / HVAC", 5),
    ("13", "Plumbing & Fixtures", 5),
    ("14", "Electrical & Low-Voltage", 5),
    ("15", "Roofing", 5),
    ("16", "Exterior Cladding, Siding & Trim", 5),
    ("17", "Site Work", 5),
    ("18", "Stormwater Management", 5),
]

# ── CSI MasterFormat. All 50 numbers are recorded so the standard is complete
# and auditable, but only the 21 BRIO plausibly prices are active by default.
# Reserved numbers are stored as reserved, never offered.
#
# WHY THE DEFAULT SUBSET MATTERS: BRIO's convention keeps EMPTY divisions at $0
# so a missing scope is visible (on 960 W Willow an empty Specialties division
# is what exposed unpriced smoke/CO alarms). That works when every division is
# plausible. Applied to all 35 active CSI divisions it would print ~14 empty
# lines for things like marine construction, and the gap signal would drown in
# noise. Empty-but-kept is therefore scoped to the ACTIVE set.
#
# (number, title, contingency_pct, active_by_default, reserved)
CSI = [
    ("00", "Procurement and Contracting Requirements", 0, False, False),
    ("01", "General Requirements", 0, True, False),
    ("02", "Existing Conditions", 5, True, False),
    ("03", "Concrete", 5, True, False),
    ("04", "Masonry", 5, True, False),
    ("05", "Metals", 5, True, False),
    ("06", "Wood, Plastics, and Composites", 5, True, False),
    ("07", "Thermal and Moisture Protection", 5, True, False),
    ("08", "Openings", 5, True, False),
    ("09", "Finishes", 5, True, False),
    ("10", "Specialties", 5, True, False),
    ("11", "Equipment", 5, True, False),
    ("12", "Furnishings", 5, True, False),
    ("13", "Special Construction", 5, False, False),
    ("14", "Conveying Equipment", 5, False, False),
    ("15", "Reserved for future expansion", 0, False, True),
    ("16", "Reserved for future expansion", 0, False, True),
    ("17", "Reserved for future expansion", 0, False, True),
    ("18", "Reserved for future expansion", 0, False, True),
    ("19", "Reserved for future expansion", 0, False, True),
    ("20", "Reserved for future expansion", 0, False, True),
    ("21", "Fire Suppression", 5, True, False),
    ("22", "Plumbing", 5, True, False),
    ("23", "Heating, Ventilating, and Air Conditioning (HVAC)", 5, True, False),
    ("24", "Reserved for future expansion", 0, False, True),
    ("25", "Integrated Automation", 5, False, False),
    ("26", "Electrical", 5, True, False),
    ("27", "Communications", 5, True, False),
    ("28", "Electronic Safety and Security", 5, True, False),
    ("29", "Reserved for future expansion", 0, False, True),
    ("30", "Reserved for future expansion", 0, False, True),
    ("31", "Earthwork", 5, True, False),
    ("32", "Exterior Improvements", 5, True, False),
    ("33", "Utilities", 5, True, False),
    ("34", "Transportation", 5, False, False),
    ("35", "Waterways and Marine Construction", 5, False, False),
    ("36", "Reserved for future expansion", 0, False, True),
    ("37", "Reserved for future expansion", 0, False, True),
    ("38", "Reserved for future expansion", 0, False, True),
    ("39", "Reserved for future expansion", 0, False, True),
    ("40", "Process Interconnections", 5, False, False),
    ("41", "Material Processing and Handling Equipment", 5, False, False),
    ("42", "Process Heating, Cooling, and Drying Equipment", 5, False, False),
    ("43", "Process Gas and Liquid Handling, Purification, and Storage Equipment", 5, False, False),
    ("44", "Pollution Control Equipment", 5, False, False),
    ("45", "Industry-Specific Manufacturing Equipment", 5, False, False),
    ("46", "Water and Wastewater Equipment", 5, False, False),
    ("47", "Reserved for future expansion", 0, False, True),
    ("48", "Electrical Power Generation", 5, False, False),
    ("49", "Reserved for future expansion", 0, False, True),
]

# Amir-approved classifications (2026-09-27). The 19 he could not classify from
# a bare address stay NULL deliberately — an unclassified project is honest; a
# guessed one silently mis-seeds every future estimate on it.
APPROVED_TYPES = {
    "commercial_ti": [
        "Sample Office Fit-out",
        "Sample Daycare",
        "Sample School Upgrade",
    ],
    "residential_new": [
        "Sample Laneway House",
        "Sample Coach House",
        "Sample Garage & Office",
    ],
    "residential_reno": [
        "Sample Kitchen Reno",
        "Sample Condo Reno",
    ],
}


def upgrade() -> None:
    conn = op.get_bind()

    # ── division_sets ────────────────────────────────────────────────────────
    op.create_table(
        "division_sets",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("standard", sa.Text, nullable=False),      # brio | csi
        sa.Column("version", sa.Text),
        sa.Column("source_ref", sa.Text),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "divisions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("set_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("division_sets.id", ondelete="CASCADE"), nullable=False, index=True),
        # Text, not int: the leading zero is part of the name ("01", not 1).
        sa.Column("number", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("contingency_pct", sa.Numeric(5, 2), nullable=False, server_default="5"),
        sa.Column("active_by_default", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_reserved", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("notes", sa.Text),
        sa.UniqueConstraint("set_id", "number", name="uq_divisions_set_number"),
    )

    # ── project type -> set. A mapping table rather than a column on the set,
    # so one set can serve several types (both residential types share the 18).
    op.create_table(
        "project_type_division_sets",
        sa.Column("project_type", sa.Text, primary_key=True),
        sa.Column("set_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("division_sets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # ── seed the two sets ────────────────────────────────────────────────────
    res_id = conn.execute(sa.text("""
        INSERT INTO division_sets (code, name, standard, version, source_ref, notes)
        VALUES ('brio-residential-18', 'BRIO Residential — 18 Divisions', 'brio', '2026-08-18',
                'BRIO house standard, confirmed by Amir 2026-08-18',
                'Numbered in construction sequence; the number is part of the name. '
                || 'Div 11 Appliances & Equipment added 2026-08-18, which is why estimates '
                || 'before that date carry a one-number offset from Mechanical onward.')
        RETURNING id""")).scalar()

    csi_id = conn.execute(sa.text("""
        INSERT INTO division_sets (code, name, standard, version, source_ref, notes)
        VALUES ('csi-masterformat', 'CSI MasterFormat', 'csi', '2018',
                'CSI MasterFormat division list; 50 numbers, 35 active, 15 reserved',
                'All 50 numbers recorded for completeness. Only the 21 BRIO plausibly '
                || 'prices are active_by_default — keeping empty divisions visible is only '
                || 'useful where the division is possible. Legacy 1995 numbering (15 '
                || 'Plumbing+HVAC, 16 Electrical) is NOT used: those numbers are reserved '
                || 'and those trades live at 22/23/26.')
        RETURNING id""")).scalar()

    for i, (num, title, cont) in enumerate(BRIO_18, start=1):
        conn.execute(sa.text("""
            INSERT INTO divisions (set_id, number, title, sequence, contingency_pct,
                                   active_by_default, is_reserved)
            VALUES (:s, :n, :t, :q, :c, true, false)"""),
            {"s": res_id, "n": num, "t": title, "q": i, "c": cont})

    for i, (num, title, cont, active, reserved) in enumerate(CSI, start=1):
        conn.execute(sa.text("""
            INSERT INTO divisions (set_id, number, title, sequence, contingency_pct,
                                   active_by_default, is_reserved)
            VALUES (:s, :n, :t, :q, :c, :a, :r)"""),
            {"s": csi_id, "n": num, "t": title, "q": i, "c": cont,
             "a": active, "r": reserved})

    for ptype in ("residential_new", "residential_reno"):
        conn.execute(sa.text(
            "INSERT INTO project_type_division_sets (project_type, set_id) VALUES (:p,:s)"),
            {"p": ptype, "s": res_id})
    for ptype in ("commercial_ti", "commercial_new"):
        conn.execute(sa.text(
            "INSERT INTO project_type_division_sets (project_type, set_id) VALUES (:p,:s)"),
            {"p": ptype, "s": csi_id})

    # ── projects.type: normalise, classify, then constrain ───────────────────
    # One legacy row holds a whole sentence ("Commercial Renovation of an office
    # site"); it is in the approved commercial_ti list and is set below.
    # Asserted, not hoped: a project rename would otherwise make the UPDATE a
    # silent no-op and leave the project unclassified, which then quietly falls
    # back to the generic 19 buckets on its next estimate.
    # ⚠️ This block classifies NAMED PRODUCTION PROJECTS, so it can only run
    # against the database that holds them. A fresh database — CI, a new dev
    # machine, a restore under test — has none of these rows, every UPDATE
    # matches 0, and the assertion below fired on the first name. That is what
    # it did: CI was red from 2026-09-27, the day this landed, until 2026-10-02,
    # and the test step never executed once in those six days because the run
    # died here. A migration must not require production history to succeed.
    #
    # The distinction that matters is not "empty or not" but WHICH database this
    # is. Count the named projects first:
    #   none present  -> not the production database. Nothing to classify.
    #   some present  -> genuinely ambiguous: a rename, a partial restore, or a
    #                    half-applied migration. Refuse, exactly as before.
    #   all present   -> apply.
    all_names = [n for names in APPROVED_TYPES.values() for n in names]
    present = conn.execute(
        sa.text("SELECT count(*) FROM projects WHERE name = ANY(:names)"),
        {"names": all_names},
    ).scalar() or 0

    if present == 0:
        print(
            f"0056: none of the {len(all_names)} named projects are in this "
            f"database, so this is not the production instance. Skipping the "
            f"project-type classification; the schema change still applies."
        )
    elif present != len(all_names):
        missing = [
            n for n in all_names
            if not conn.execute(
                sa.text("SELECT 1 FROM projects WHERE name = :n LIMIT 1"), {"n": n}
            ).scalar()
        ]
        raise RuntimeError(
            f"{present} of {len(all_names)} named projects are present, so this "
            f"database is partially populated and classifying it would be a "
            f"guess. Missing: {missing}. Reconcile the names before migrating "
            f"rather than skipping them."
        )
    else:
        applied = 0
        for ptype, names in APPROVED_TYPES.items():
            for name in names:
                n = conn.execute(sa.text(
                    "UPDATE projects SET type = :t WHERE name = :n"),
                    {"t": ptype, "n": name}).rowcount
                if n != 1:
                    raise RuntimeError(
                        f"expected exactly 1 project named {name!r}, matched {n}. "
                        "Reconcile the name before migrating rather than skipping it."
                    )
                applied += n
        if applied != len(all_names):
            raise RuntimeError(
                f"expected {len(all_names)} classifications, applied {applied}")

    # Anything still holding a non-enum value would break the CHECK below.
    # REFUSE rather than repair: nulling it would destroy the only record of what
    # the project was, and a wrong guess silently mis-seeds every future estimate.
    # The operator reclassifies, then re-runs.
    leftover = conn.execute(sa.text(
        f"SELECT name, type FROM projects "
        f"WHERE type IS NOT NULL AND type NOT IN {PROJECT_TYPES!r} ORDER BY name"
    )).fetchall()
    if leftover:
        listed = "; ".join(f"{n!r} -> {t!r}" for n, t in leftover)
        raise RuntimeError(
            f"{len(leftover)} project(s) hold a type outside the new enum: {listed}. "
            f"Reclassify them to one of {PROJECT_TYPES} (or NULL) and re-run. "
            "Refusing to null them automatically — that would discard the only "
            "record of what the project was."
        )

    # NULL stays legal: 19 projects are deliberately unclassified.
    op.create_check_constraint(
        "ck_projects_type_enum", "projects",
        f"type IS NULL OR type IN {PROJECT_TYPES!r}",
    )


def downgrade() -> None:
    op.drop_constraint("ck_projects_type_enum", "projects", type_="check")
    op.drop_table("project_type_division_sets")
    op.drop_table("divisions")
    op.drop_table("division_sets")
