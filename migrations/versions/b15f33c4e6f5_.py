"""empty message

Revision ID: b15f33c4e6f5
Revises: 47f0c3fdc5c2
Create Date: 2020-08-26 17:02:38.869028

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b15f33c4e6f5'
down_revision = '47f0c3fdc5c2'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_unique_constraint(None, 'MosRating', ['mos_instance_id', 'user_id'])
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, 'MosRating', type_='unique')
    # ### end Alembic commands ###