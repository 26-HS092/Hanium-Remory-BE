"""memories.original_image_url 컬럼 추가

추억 사진은 앱에서 인형 화면 비율(5:3)로 잘라 올린다. 인형은 사진 내용을
읽어 기억 문장을 만드는데, 잘린 부분이 빠지지 않게 자르기 전 사진을 따로 둔다.


Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-11-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c0d1e2f3a4b5'
down_revision: Union[str, Sequence[str], None] = 'b9c0d1e2f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("memories") as batch:
        batch.add_column(sa.Column("original_image_url", sa.String(500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("memories") as batch:
        batch.drop_column("original_image_url")
