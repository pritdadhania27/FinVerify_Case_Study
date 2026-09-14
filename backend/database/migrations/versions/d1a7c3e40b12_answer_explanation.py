"""answers.explanation - Module 16's output, for recorded answers

The explanation was reaching the run artifacts (RX-045) and nowhere else, so
`AnswerOut.explanation` was null for all 1,646 recorded answers and the
verification screen could never show why an answer was flagged. Nullable, and it
stays that way: 1,376 of those rows come from runs that predate RX-045 and
genuinely carry no explanation. NULL means "this run recorded none", which is a
different statement from "this answer was not explained", and the UI reports the
difference rather than showing an empty panel for both.

Revision ID: d1a7c3e40b12
Revises: c8260f3900e0
Create Date: 2026-09-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1a7c3e40b12"
down_revision: Union[str, Sequence[str], None] = "c8260f3900e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable explanation payload to answers."""
    op.add_column("answers", sa.Column("explanation", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drop it. The artifacts remain canonical, so nothing is lost by reverting."""
    op.drop_column("answers", "explanation")
