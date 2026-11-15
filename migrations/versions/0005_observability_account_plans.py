"""Retained telemetry, alert lifecycle and reviewed account changes."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "monitor_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("host_id", sa.String(64), nullable=False, index=True),
        sa.Column("collected_at", sa.String(32), nullable=False, index=True),
        sa.Column("observer", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
    )
    op.create_index("ix_monitor_history_host_time", "monitor_history", ["host_id", "collected_at"])
    op.create_table(
        "monitor_alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("host_id", sa.String(64), nullable=False, index=True),
        sa.Column("rule", sa.String(256), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("opened_at", sa.String(32), nullable=False, index=True),
        sa.Column("last_seen_at", sa.String(32), nullable=False),
        sa.Column("resolved_at", sa.String(32)),
    )
    op.create_table(
        "account_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("host_id", sa.String(64), nullable=False),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.String(32), nullable=False),
        sa.Column("finished_at", sa.String(32)),
    )


def downgrade():
    op.drop_table("account_plans")
    op.drop_table("monitor_alerts")
    op.drop_table("monitor_history")
