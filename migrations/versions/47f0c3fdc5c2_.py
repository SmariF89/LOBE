"""empty message

Revision ID: 47f0c3fdc5c2
Revises: e3bc9cfe3fb7
Create Date: 2020-08-21 12:30:14.922850

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '47f0c3fdc5c2'
down_revision = 'e3bc9cfe3fb7'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('CustomToken', sa.Column('pron', sa.String(), nullable=True))
    op.add_column('CustomToken', sa.Column('score', sa.Float(), nullable=True))
    op.add_column('CustomToken', sa.Column('source', sa.String(), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('CustomToken', 'source')
    op.drop_column('CustomToken', 'score')
    op.drop_column('CustomToken', 'pron')
    # ### end Alembic commands ###