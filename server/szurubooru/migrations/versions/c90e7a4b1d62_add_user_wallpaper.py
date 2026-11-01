"""Add account site wallpapers selected from image posts.

Revision ID: c90e7a4b1d62
"""

import sqlalchemy as sa
from alembic import op

revision = "c90e7a4b1d62"
down_revision = "b78d9c12e6f4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user", sa.Column("wallpaper_post_id", sa.Integer()))
    op.create_index("ix_user_wallpaper_post_id", "user", ["wallpaper_post_id"])
    op.create_foreign_key(
        "fk_user_wallpaper_post_id",
        "user",
        "post",
        ["wallpaper_post_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint("fk_user_wallpaper_post_id", "user", type_="foreignkey")
    op.drop_index("ix_user_wallpaper_post_id", table_name="user")
    op.drop_column("user", "wallpaper_post_id")
