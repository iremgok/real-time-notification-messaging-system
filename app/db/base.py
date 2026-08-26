# Import all the models, so that Base has them before being
# imported by Alembic or database setup utilities.
from app.db.base_class import Base  # noqa
from app.models.user import User  # noqa
from app.models.room import Room  # noqa
from app.models.membership import Membership  # noqa
from app.models.message import Message  # noqa
