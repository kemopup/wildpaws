"""Add Markdown descriptions to posts.

Revision ID: b78d9c12e6f4
"""

import sqlalchemy as sa
from alembic import op

revision = "b78d9c12e6f4"
down_revision = "adcd63ff76a2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("post", sa.Column("description", sa.UnicodeText()))


def downgrade():
    op.drop_column("post", "description")
