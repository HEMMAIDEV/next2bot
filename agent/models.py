# agent/models.py — All SQLAlchemy models
from datetime import datetime
from sqlalchemy import String, Text, Integer, Boolean, Numeric, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from agent.database import Base


class Message(Base):
    __tablename__ = "messages"

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone:      Mapped[str]      = mapped_column(String(50), index=True)
    role:       Mapped[str]      = mapped_column(String(20))
    content:    Mapped[str]      = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Lead(Base):
    __tablename__ = "leads"

    id:           Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone:        Mapped[str]           = mapped_column(String(50), unique=True, index=True)
    name:         Mapped[str | None]    = mapped_column(String(200))
    company:      Mapped[str | None]    = mapped_column(String(200))
    business_need:Mapped[str | None]    = mapped_column(Text)
    status:       Mapped[str]           = mapped_column(String(50), default="new", index=True)
    score:        Mapped[int]           = mapped_column(Integer, default=0)
    source:       Mapped[str]           = mapped_column(String(50), default="whatsapp")
    notes:        Mapped[str | None]    = mapped_column(Text)
    ai_summary:   Mapped[str | None]    = mapped_column(Text)
    next_action:  Mapped[str | None]    = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at:   Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:   Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FunnelEvent(Base):
    __tablename__ = "funnel_events"

    id:           Mapped[int]        = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone:        Mapped[str]        = mapped_column(String(50), index=True)
    from_status:  Mapped[str | None] = mapped_column(String(50))
    to_status:    Mapped[str]        = mapped_column(String(50))
    triggered_by: Mapped[str]        = mapped_column(String(50), default="agent")
    created_at:   Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow)


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id:            Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider:      Mapped[str]           = mapped_column(String(50), index=True)
    event_type:    Mapped[str]           = mapped_column(String(50))
    tokens_in:     Mapped[int]           = mapped_column(Integer, default=0)
    tokens_out:    Mapped[int]           = mapped_column(Integer, default=0)
    cost_usd:      Mapped[float]         = mapped_column(Numeric(10, 6), default=0)
    latency_ms:    Mapped[int]           = mapped_column(Integer, default=0)
    success:       Mapped[bool]          = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None]    = mapped_column(Text)
    phone:         Mapped[str | None]    = mapped_column(String(50))
    created_at:    Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)


class HealthCheck(Base):
    __tablename__ = "health_checks"

    id:         Mapped[int]        = mapped_column(Integer, primary_key=True, autoincrement=True)
    service:    Mapped[str]        = mapped_column(String(50), index=True)
    status:     Mapped[str]        = mapped_column(String(20))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    detail:     Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow)
