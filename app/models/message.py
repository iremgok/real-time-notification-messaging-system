import uuid
from datetime import datetime, timezone
from sqlalchemy import ForeignKey, DateTime, Text, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING
from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.room import Room


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        default=lambda: datetime.now(timezone.utc),
    )
    status: Mapped[str] = mapped_column(
        String(20), default="SENT", nullable=False  # SENT, DELIVERED, READ
    )

    # Relationships
    room: Mapped["Room"] = relationship(back_populates="messages")
    sender: Mapped["User"] = relationship(back_populates="messages")
