from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.enums import SendJobState


class SendJob(Base):
    __tablename__ = "send_jobs"

    operation_id: Mapped[str] = mapped_column(
        ForeignKey("operations.operation_id"),
        primary_key=True,
    )

    state: Mapped[SendJobState] = mapped_column(
        Enum(SendJobState),
        nullable=False,
        default=SendJobState.PENDING,
    )

    attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    last_error: Mapped[str | None] = mapped_column(
        String(500),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    operation = relationship(
        "Operation",
        back_populates="send_job",
    )
