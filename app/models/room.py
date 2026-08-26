import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import List, TYPE_CHECKING
from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.membership import Membership
    from app.models.message import Message


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=True)
    description: Mapped[str] = mapped_column(String(255), nullable=True)
    is_group: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    memberships: Mapped[List["Membership"]] = relationship(
        back_populates="room", cascade="all, delete-orphan"
    )
    messages: Mapped[List["Message"]] = relationship(
        back_populates="room",
        cascade="all, delete-orphan",
        order_by="Message.created_at.asc",
    )
