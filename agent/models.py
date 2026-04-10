# agent/models.py — All SQLAlchemy models
from datetime import datetime
from sqlalchemy import String, Text, Integer, Boolean, Numeric, DateTime, ForeignKey, Float
from sqlalchemy.orm import Mapped, mapped_column
from agent.database import Base
from typing import Optional


class Message(Base):
    __tablename__ = "messages"

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone:      Mapped[str]      = mapped_column(String(50), index=True)
    role:       Mapped[str]      = mapped_column(String(20))
    content:    Mapped[str]      = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Lead(Base):
    __tablename__ = "leads"

    id:            Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone:         Mapped[str]            = mapped_column(String(50), unique=True, index=True)
    name:          Mapped[str | None]     = mapped_column(String(200))
    company:       Mapped[str | None]     = mapped_column(String(200))
    business_need: Mapped[str | None]     = mapped_column(Text)
    status:        Mapped[str]            = mapped_column(String(50), default="new", index=True)
    score:         Mapped[int]            = mapped_column(Integer, default=0)
    source:        Mapped[str]            = mapped_column(String(50), default="whatsapp")
    notes:         Mapped[str | None]     = mapped_column(Text)
    ai_summary:    Mapped[str | None]     = mapped_column(Text)
    next_action:   Mapped[str | None]     = mapped_column(Text)
    last_seen_at:  Mapped[datetime | None]= mapped_column(DateTime)
    created_at:    Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:    Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FunnelEvent(Base):
    __tablename__ = "funnel_events"

    id:           Mapped[int]         = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone:        Mapped[str]         = mapped_column(String(50), index=True)
    from_status:  Mapped[str | None]  = mapped_column(String(50))
    to_status:    Mapped[str]         = mapped_column(String(50))
    triggered_by: Mapped[str]         = mapped_column(String(50), default="agent")
    created_at:   Mapped[datetime]    = mapped_column(DateTime, default=datetime.utcnow)


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id:            Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider:      Mapped[str]          = mapped_column(String(50), index=True)
    event_type:    Mapped[str]          = mapped_column(String(50))
    tokens_in:     Mapped[int]          = mapped_column(Integer, default=0)
    tokens_out:    Mapped[int]          = mapped_column(Integer, default=0)
    cost_usd:      Mapped[float]        = mapped_column(Numeric(10, 6), default=0)
    latency_ms:    Mapped[int]          = mapped_column(Integer, default=0)
    success:       Mapped[bool]         = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None]   = mapped_column(Text)
    phone:         Mapped[str | None]   = mapped_column(String(50))
    created_at:    Mapped[datetime]     = mapped_column(DateTime, default=datetime.utcnow)


class HealthCheck(Base):
    __tablename__ = "health_checks"

    id:         Mapped[int]        = mapped_column(Integer, primary_key=True, autoincrement=True)
    service:    Mapped[str]        = mapped_column(String(50), index=True)
    status:     Mapped[str]        = mapped_column(String(20))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    detail:     Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow)


# ── CLIENT MANAGEMENT ────────────────────────────────────────────────────────

class Client(Base):
    """
    A paying client whose bot we operate and manage.
    Each client has their own Next2Bot instance. You can activate/deactivate
    their bot from the control panel, and track their payment status.
    """
    __tablename__ = "clients"

    id:                      Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    name:                    Mapped[str]           = mapped_column(String(200), index=True)
    owner_name:              Mapped[str | None]    = mapped_column(String(200))
    phone:                   Mapped[str | None]    = mapped_column(String(50))
    email:                   Mapped[str | None]    = mapped_column(String(200))
    niche:                   Mapped[str | None]    = mapped_column(String(100))
    plan:                    Mapped[str]           = mapped_column(String(50), default="starter")
    bot_active:              Mapped[bool]          = mapped_column(Boolean, default=True)
    monthly_price_mxn:       Mapped[float]         = mapped_column(Float, default=0.0)
    setup_price_mxn:         Mapped[float]         = mapped_column(Float, default=0.0)
    billing_day:             Mapped[int]           = mapped_column(Integer, default=1)
    last_payment_at:         Mapped[datetime|None] = mapped_column(DateTime)
    next_payment_at:         Mapped[datetime|None] = mapped_column(DateTime)
    payment_status:          Mapped[str]           = mapped_column(String(30), default="pending")
    bot_phone_number:        Mapped[str | None]    = mapped_column(String(50))
    deployment_url:          Mapped[str | None]    = mapped_column(String(500))
    notes:                   Mapped[str | None]    = mapped_column(Text)
    # Phase 7 — usage limits
    plan_id:                 Mapped[int | None]    = mapped_column(Integer)                        # FK to plans.id (soft ref)
    msg_limit:               Mapped[int | None]    = mapped_column(Integer)                        # Monthly message cap
    cost_limit_usd:          Mapped[float]         = mapped_column(Float, default=0.0)             # Monthly cost cap USD
    alert_threshold_pct:     Mapped[int]           = mapped_column(Integer, default=80)            # Alert at X% of limit
    # Phase 7 — partner bot support
    is_partner_bot:          Mapped[bool]          = mapped_column(Boolean, default=False)         # Managed by a partner?
    partner_name:            Mapped[str | None]    = mapped_column(String(200))                    # Partner person/company
    partner_monthly_cost_mxn: Mapped[float]        = mapped_column(Float, default=0.0)             # What we pay partner
    partner_api_excluded:    Mapped[bool]          = mapped_column(Boolean, default=False)         # Exclude from N2H API tracking?
    created_at:              Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:              Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── PATTERN LEARNING ─────────────────────────────────────────────────────────

class LearnedPattern(Base):
    """
    Stores successful conversation patterns extracted from won/booked leads.
    These are injected back into the bot's system prompt as live examples,
    so the bot learns what works and improves over time.
    """
    __tablename__ = "learned_patterns"

    id:             Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    pattern_type:   Mapped[str]          = mapped_column(String(50), index=True)  # opener | close | objection_handle | discovery
    lead_phone:     Mapped[str | None]   = mapped_column(String(50))              # Source lead
    outcome:        Mapped[str]          = mapped_column(String(50))              # demo_booked | won
    summary:        Mapped[str]          = mapped_column(Text)                    # What worked (human-readable)
    example_exchange: Mapped[str | None] = mapped_column(Text)                   # Key 2-3 message exchange that worked
    used_count:     Mapped[int]          = mapped_column(Integer, default=0)
    effectiveness:  Mapped[float]        = mapped_column(Float, default=1.0)      # 0-1, updated as we learn
    active:         Mapped[bool]         = mapped_column(Boolean, default=True)
    created_at:     Mapped[datetime]     = mapped_column(DateTime, default=datetime.utcnow)


# ── SERVICE BILLING ──────────────────────────────────────────────────────────

class ServiceBilling(Base):
    """
    Tracks billing for external services (OpenAI, Whapi, Railway, etc.).
    Generates payment reminders in the dashboard.
    """
    __tablename__ = "service_billing"

    id:                          Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_name:                Mapped[str]           = mapped_column(String(100), index=True)
    display_name:                Mapped[str]           = mapped_column(String(100))
    plan_name:                   Mapped[str | None]    = mapped_column(String(100))
    monthly_cost_usd:            Mapped[float]         = mapped_column(Float, default=0.0)
    monthly_cost_mxn:            Mapped[float]         = mapped_column(Float, default=0.0)
    billing_day:                 Mapped[int]           = mapped_column(Integer, default=1)
    billing_cycle:               Mapped[str]           = mapped_column(String(20), default="monthly")
    last_paid_at:                Mapped[datetime|None] = mapped_column(DateTime)
    next_due_at:                 Mapped[datetime|None] = mapped_column(DateTime)
    auto_pay:                    Mapped[bool]          = mapped_column(Boolean, default=False)
    notes:                       Mapped[str | None]    = mapped_column(Text)
    # Phase 7 — balance & alert tracking
    balance_usd:                 Mapped[float | None]  = mapped_column(Float)                      # Manually entered current balance
    balance_alert_threshold_usd: Mapped[float]         = mapped_column(Float, default=5.0)         # Alert when below this
    alert_threshold_usd:         Mapped[float]         = mapped_column(Float, default=0.0)         # Alert if monthly cost exceeds this
    created_at:                  Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:                  Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── SUBSCRIPTION PLANS ────────────────────────────────────────────────────────

class Plan(Base):
    """
    Predefined subscription tiers that can be assigned to clients.
    Sets message limits, cost limits, and suggested pricing.
    """
    __tablename__ = "plans"

    id:               Mapped[int]         = mapped_column(Integer, primary_key=True, autoincrement=True)
    name:             Mapped[str]         = mapped_column(String(50), unique=True)          # starter | pro | enterprise
    display_name:     Mapped[str]         = mapped_column(String(100))
    msg_limit:        Mapped[int | None]  = mapped_column(Integer)                          # Monthly message cap (None = unlimited)
    cost_limit_usd:   Mapped[float]       = mapped_column(Float, default=0.0)               # Monthly cost cap USD (0 = unlimited)
    price_mxn:        Mapped[float]       = mapped_column(Float, default=0.0)               # Suggested monthly price
    description:      Mapped[str | None]  = mapped_column(Text)
    active:           Mapped[bool]        = mapped_column(Boolean, default=True)
    created_at:       Mapped[datetime]    = mapped_column(DateTime, default=datetime.utcnow)


# ── ALERTS ────────────────────────────────────────────────────────────────────

class Alert(Base):
    """
    System-generated alerts for the dashboard owner.
    Types: usage_warning, usage_exceeded, payment_missed, api_balance_low, partner_payment_due
    """
    __tablename__ = "alerts"

    id:         Mapped[int]         = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_type: Mapped[str]         = mapped_column(String(50), index=True)   # usage_warning | usage_exceeded | payment_missed | ...
    ref_id:     Mapped[str]         = mapped_column(String(100), index=True)   # e.g. "client_3_msg" — for dedup
    title:      Mapped[str]         = mapped_column(String(200))
    body:       Mapped[str]         = mapped_column(Text)
    severity:   Mapped[str]         = mapped_column(String(20), default="info")  # info | warning | error
    read:       Mapped[bool]        = mapped_column(Boolean, default=False)
    dismissed:  Mapped[bool]        = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime]    = mapped_column(DateTime, default=datetime.utcnow)
    read_at:    Mapped[datetime | None] = mapped_column(DateTime)


# ── PARTNER PAYMENTS ─────────────────────────────────────────────────────────

class PartnerPayment(Base):
    """
    Tracks payments made to partners for partner-managed client bots.
    Keeps a clean history separate from our own revenue.
    """
    __tablename__ = "partner_payments"

    id:          Mapped[int]         = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id:   Mapped[int]         = mapped_column(Integer, index=True)   # FK to clients.id
    partner_name: Mapped[str | None] = mapped_column(String(200))
    amount_mxn:  Mapped[float]       = mapped_column(Float, default=0.0)
    notes:       Mapped[str | None]  = mapped_column(Text)
    paid_at:     Mapped[datetime]    = mapped_column(DateTime, default=datetime.utcnow)
    created_at:  Mapped[datetime]    = mapped_column(DateTime, default=datetime.utcnow)
