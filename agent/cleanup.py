# agent/cleanup.py — Automated maintenance jobs
"""
Background tasks that run on a schedule:
1. Purge leads (+ their messages) inactive for 6+ months
2. Update payment statuses for clients
3. Seed default ServiceBilling records on first run
"""
import logging
import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select, delete
from agent.database import async_session
from agent.models import Lead, Message, FunnelEvent, Client, ServiceBilling

logger = logging.getLogger("agentkit")

INACTIVE_MONTHS = 6
CLEANUP_INTERVAL_HOURS = 24  # Run once per day


# ── LEAD CLEANUP ─────────────────────────────────────────────────────────────

async def purge_inactive_leads() -> int:
    """
    Deletes leads and all their associated data (messages, funnel events)
    if they have been inactive for INACTIVE_MONTHS months.
    Returns the number of leads purged.
    """
    cutoff = datetime.utcnow() - timedelta(days=INACTIVE_MONTHS * 30)
    purged = 0

    async with async_session() as session:
        # Find stale leads (never seen, or last seen before cutoff)
        result = await session.execute(
            select(Lead).where(
                (Lead.last_seen_at < cutoff) |
                (Lead.last_seen_at == None, Lead.created_at < cutoff)
            )
        )
        stale_leads = result.scalars().all()

        for lead in stale_leads:
            # Skip won leads — keep them as historical records
            if lead.status in ("won",):
                continue

            phone = lead.phone

            # Delete messages
            await session.execute(delete(Message).where(Message.phone == phone))
            # Delete funnel events
            await session.execute(delete(FunnelEvent).where(FunnelEvent.phone == phone))
            # Delete lead
            await session.delete(lead)
            purged += 1

        if purged > 0:
            await session.commit()
            logger.info(f"Cleanup: purged {purged} inactive leads (>{INACTIVE_MONTHS} months)")

    return purged


# ── CLIENT PAYMENT STATUS ─────────────────────────────────────────────────────

async def refresh_client_payment_statuses() -> None:
    """
    Checks all clients and marks their payment_status as:
    - 'ok' if next_payment_at is more than 5 days away
    - 'pending' if payment is due within 5 days
    - 'overdue' if payment date has passed
    """
    now = datetime.utcnow()
    async with async_session() as session:
        result = await session.execute(select(Client))
        clients = result.scalars().all()

        for client in clients:
            if not client.next_payment_at:
                continue
            delta = (client.next_payment_at - now).days
            if delta < 0:
                new_status = "overdue"
            elif delta <= 5:
                new_status = "pending"
            else:
                new_status = "ok"
            if client.payment_status != new_status:
                client.payment_status = new_status

        await session.commit()


# ── DEFAULT SERVICE BILLING SEED ─────────────────────────────────────────────

DEFAULT_SERVICES = [
    {
        "service_name": "openai",
        "display_name": "OpenAI",
        "plan_name": "Pay as you go",
        "monthly_cost_usd": 0.0,
        "monthly_cost_mxn": 0.0,
        "billing_day": 1,
        "billing_cycle": "usage",
        "auto_pay": True,
        "notes": "Charged by usage. Monitor via dashboard.",
    },
    {
        "service_name": "whapi",
        "display_name": "Whapi.cloud",
        "plan_name": "Pro",
        "monthly_cost_usd": 39.0,
        "monthly_cost_mxn": 702.0,
        "billing_day": 1,
        "billing_cycle": "monthly",
        "auto_pay": False,
        "notes": "WhatsApp API provider. Check renewal date.",
    },
    {
        "service_name": "railway",
        "display_name": "Railway",
        "plan_name": "Hobby",
        "monthly_cost_usd": 5.0,
        "monthly_cost_mxn": 90.0,
        "billing_day": 1,
        "billing_cycle": "monthly",
        "auto_pay": True,
        "notes": "Hosting for Next2Bot. Scales with usage.",
    },
    {
        "service_name": "google_workspace",
        "display_name": "Google Workspace",
        "plan_name": "Business Starter",
        "monthly_cost_usd": 6.0,
        "monthly_cost_mxn": 108.0,
        "billing_day": 1,
        "billing_cycle": "monthly",
        "auto_pay": True,
        "notes": "Calendar + email integration.",
    },
]


async def seed_default_billing() -> None:
    """Seeds default service billing records if none exist."""
    async with async_session() as session:
        count = (await session.execute(
            select(ServiceBilling)
        )).scalars().first()

        if count is not None:
            return  # Already seeded

        for svc in DEFAULT_SERVICES:
            record = ServiceBilling(**svc, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
            session.add(record)
        await session.commit()
        logger.info("Seeded default service billing records")


# ── BACKGROUND LOOP ───────────────────────────────────────────────────────────

async def run_cleanup_loop() -> None:
    """
    Infinite async loop that runs maintenance jobs once per day.
    Launched as a background task from main.py lifespan.
    """
    logger.info("Cleanup loop started")
    await asyncio.sleep(10)  # Brief startup delay

    while True:
        try:
            purged = await purge_inactive_leads()
            await refresh_client_payment_statuses()
            if purged:
                logger.info(f"Daily cleanup complete: {purged} leads purged")
        except Exception as e:
            logger.error(f"Cleanup loop error: {e}")

        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)
