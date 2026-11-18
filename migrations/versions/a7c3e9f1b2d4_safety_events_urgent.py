"""safety_events 에 urgent · source 추가

인형이 위험 신호를 두 단계로 보낸다. urgent=False(지켜볼 신호)는 즉시 알리지
않고 리포트에만 담는다. source 는 누가 잡았는지(rule / llm / llm+model / model).
기존 행은 모두 즉시 알렸던 신호라 urgent=True 로 채운다.

Revision ID: a7c3e9f1b2d4
Revises: c0d1e2f3a4b5
Create Date: 2026-11-18 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e9f1b2d4'
down_revision: Union[str, Sequence[str], None] = 'c0d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "safety_events",
        sa.Column("urgent", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("safety_events", sa.Column("source", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("safety_events", "source")
    op.drop_column("safety_events", "urgent")
