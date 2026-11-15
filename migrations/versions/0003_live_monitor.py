"""Short-lived, explicit real observation sessions."""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("monitor_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("generation", sa.String(36), nullable=False),
        sa.Column("host_ids", sa.JSON(), nullable=False),
        sa.Column("inventory", sa.JSON(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.String(32), nullable=False),
        sa.Column("next_poll_at", sa.String(32), nullable=False),
        sa.Column("checked_at", sa.String(32), nullable=True))
    op.create_index("ix_monitor_sessions_expires_at", "monitor_sessions", ["expires_at"])


def downgrade():
    op.drop_table("monitor_sessions")
