"""labour_rates: drop the implicit 20% discount on published wage sources

Published wage data (Job Bank / WorkBC) is now stored AS POSTED. The column
default of 20 meant every non-own labour rate was silently reduced by a fifth,
which understated the cost basis and — applied to an apprentice "low" figure —
produced $16.00/hr, below BC's $17.85 statutory minimum.

A discount is still supported, but must now be set deliberately (e.g. a
negotiated association rate) rather than arriving by default.

The two rows seeded in 0025 (framer, drywaller) are rebased to their posted
Job Bank figures. base_rate is unchanged and already correct; only the discount
and the derived effective_rate move.

Revision ID: 0055_labour_no_disc
Revises: 0054_client_recent_proj
"""

from alembic import op

revision = "0055_labour_no_disc"
down_revision = "0054_client_recent_proj"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE labour_rates ALTER COLUMN discount_pct SET DEFAULT 0")
    # Rebase only rows still carrying the implicit default, and only where the
    # effective rate is consistent with that discount — i.e. rows nobody has
    # hand-edited since. A deliberately discounted row is left alone.
    op.execute(
        """
        UPDATE labour_rates
           SET discount_pct   = 0,
               effective_rate = base_rate,
               updated_at     = now()
         WHERE discount_pct = 20
           AND source <> 'own'
           AND abs(effective_rate - base_rate * 0.80) < 0.01
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE labour_rates ALTER COLUMN discount_pct SET DEFAULT 20")
    op.execute(
        """
        UPDATE labour_rates
           SET discount_pct   = 20,
               effective_rate = round(base_rate * 0.80, 4),
               updated_at     = now()
         WHERE discount_pct = 0
           AND source <> 'own'
           AND abs(effective_rate - base_rate) < 0.01
        """
    )
