"""Browser-managed SSH observation settings and persistent hardware profiles."""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("monitor_sessions", sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="2"))
    op.create_table("ssh_hosts",
        sa.Column("node_id", sa.String(64), sa.ForeignKey("nodes.id"), primary_key=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("host_key_sha256", sa.String(128), nullable=True),
        sa.Column("pending_host_key_sha256", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("profile", sa.JSON(), nullable=True),
        sa.Column("first_seen_at", sa.String(32), nullable=True),
        sa.Column("profile_updated_at", sa.String(32), nullable=True),
        sa.Column("requested_at", sa.String(32), nullable=True),
        sa.Column("claim_until", sa.String(32), nullable=False))


def downgrade():
    op.drop_table("ssh_hosts")
    op.drop_column("monitor_sessions", "interval_seconds")
