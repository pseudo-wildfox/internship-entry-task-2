from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, Numeric, String, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.models.enums import OperationStatus


class Operation(Base):
    __tablename__ = "operations"

    __table_args__ = (Index("ix_operations_status", "status"),)

    operation_id: Mapped[str] = mapped_column(
        String(45),
        primary_key=True,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        String(255),
    )

    status: Mapped[OperationStatus] = mapped_column(
        Enum(OperationStatus),
        nullable=False,
        default=OperationStatus.CREATED,
    )

    provider_payment_id: Mapped[str | None] = mapped_column(
        String(128),
        unique=True,
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

    events = relationship(
        "Event",
        back_populates="operation",
        cascade="all, delete-orphan",
    )

    send_job = relationship(
        "SendJob",
        back_populates="operation",
        uselist=False,
        cascade="all, delete-orphan",
    )
