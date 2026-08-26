import uuid
from datetime import datetime, timezone
from sqlalchemy import ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING
from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.room import Room


class Membership(Base):
    __tablename__ = "memberships"

    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    room: Mapped["Room"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship(back_populates="memberships")
