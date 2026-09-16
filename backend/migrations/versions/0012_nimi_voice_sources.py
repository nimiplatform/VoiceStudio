"""Retain public reference sources and formal Nimi voice identities."""
from alembic import op
import sqlalchemy as sa

revision = '0012_nimi_voice_sources'
down_revision = '0011_remote_attempt_deadlines'
branch_labels = None
depends_on = None

COLUMNS = {
    'voice_profiles': ['ref_audio_uri', 'ref_audio_mime', 'nimi_voice_asset_id',
                       'locked_nimi_voice_asset_id', 'locked_ref_text'],
    'generation_history': ['nimi_voice_asset_id'],
}


def upgrade():
    for table, names in COLUMNS.items():
        existing = {row[1] for row in op.get_bind().execute(sa.text(f'PRAGMA table_info({table})'))}
        for name in names:
            if name not in existing:
                op.add_column(table, sa.Column(name, sa.Text(), nullable=True,
                    server_default=None if name == 'locked_ref_text' else ''))


def downgrade():
    for table, names in COLUMNS.items():
        for name in reversed(names):
            op.drop_column(table, name)
