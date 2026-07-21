from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.enums import OperationStatus


class Event(Base):
    __tablename__ = "events"

    __table_args__ = (UniqueConstraint("operation_id", "sequence_no"),)

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    operation_id: Mapped[str] = mapped_column(
        ForeignKey("operations.operation_id"),
        nullable=False,
    )

    sequence_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    from_status: Mapped[OperationStatus | None] = mapped_column(
        Enum(OperationStatus),
    )

    to_status: Mapped[OperationStatus] = mapped_column(
        Enum(OperationStatus),
        nullable=False,
    )

    message: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    operation = relationship(
        "Operation",
        back_populates="events",
    )
