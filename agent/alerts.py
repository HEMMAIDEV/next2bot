# agent/alerts.py — Alert generation engine
"""
Generates alerts for:
- Client nearing/exceeding message or cost limits
- Missed client payments (overdue)
- Low API balance (OpenAI, etc.)
- Partner payment due
Stores alerts in the Alert table; dashboard reads unread ones.
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, func
from agent.database import async_session
from agent.models import Alert, Client, ServiceBilling, UsageLog

logger = logging.getLogger("agentkit")


async def generate_all_alerts() -> int:
    """
    Run all alert checks. Returns the number of new alerts created.
    Safe to call repeatedly — won't duplicate alerts within 24 hours.
    """
    created = 0
    created += await _check_client_usage_alerts()
    created += await _check_payment_alerts()
    created += await _check_api_balance_alerts()
    created += await _check_partner_payment_alerts()
    if created:
        logger.info(f"Alerts generated: {created} new alerts")
    return created


# ── HELPERS ───────────────────────────────────────────────────────────────────

async def _alert_exists(alert_type: str, ref_id: str, window_hours: int = 24) -> bool:
    """Returns True if an identical alert was already created within the window."""
    since = datetime.utcnow() - timedelta(hours=window_hours)
    async with async_session() as session:
        count = (await session.execute(
            select(func.count(Alert.id)).where(
                Alert.alert_type == alert_type,
                Alert.ref_id == ref_id,
                Alert.created_at >= since,
            )
        )).scalar() or 0
    return count > 0


async def _create_alert(alert_type: str, ref_id: str, title: str, body: str, severity: str = "info") -> None:
    async with async_session() as session:
        alert = Alert(
            alert_type=alert_type,
            ref_id=ref_id,
            title=title,
            body=body,
            severity=severity,
            created_at=datetime.utcnow(),
        )
        session.add(alert)
        await session.commit()


# ── USAGE ALERTS ──────────────────────────────────────────────────────────────

async def _check_client_usage_alerts() -> int:
    """Alert when a client is nearing or has exceeded their message/cost limits."""
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    created = 0

    async with async_session() as session:
        clients = (await session.execute(select(Client))).scalars().all()

        for client in clients:
            # Skip partner bots with API excluded (not our usage)
            if client.is_partner_bot and client.partner_api_excluded:
                continue
            if not client.bot_phone_number:
                continue

            # Get this month's usage
            msgs = (await session.execute(
                select(func.count(UsageLog.id))
                .where(UsageLog.phone == client.bot_phone_number,
                       UsageLog.created_at >= month_start)
            )).scalar() or 0

            cost = float((await session.execute(
                select(func.sum(UsageLog.cost_usd))
                .where(UsageLog.phone == client.bot_phone_number,
                       UsageLog.created_at >= month_start)
            )).scalar() or 0)

            threshold = getattr(client, "alert_threshold_pct", 80) or 80

            # Message limit alerts
            msg_limit = getattr(client, "msg_limit", None)
            if msg_limit and msg_limit > 0:
                pct = (msgs / msg_limit) * 100
                ref = f"client_{client.id}_msg"
                if pct >= 100:
                    if not await _alert_exists("usage_exceeded", ref):
                        await _create_alert(
                            "usage_exceeded", ref,
                            f"⚠️ {client.name} exceeded message limit",
                            f"{client.name} has sent {msgs}/{msg_limit} messages this month ({pct:.0f}%). Consider upgrading their plan.",
                            severity="error",
                        )
                        created += 1
                elif pct >= threshold:
                    if not await _alert_exists("usage_warning", ref):
                        await _create_alert(
                            "usage_warning", ref,
                            f"🔔 {client.name} nearing message limit",
                            f"{client.name} has used {msgs}/{msg_limit} messages this month ({pct:.0f}%).",
                            severity="warning",
                        )
                        created += 1

            # Cost limit alerts
            cost_limit = getattr(client, "cost_limit_usd", None)
            if cost_limit and cost_limit > 0:
                pct = (cost / cost_limit) * 100
                ref = f"client_{client.id}_cost"
                if pct >= 100:
                    if not await _alert_exists("usage_exceeded", ref):
                        await _create_alert(
                            "usage_exceeded", ref,
                            f"⚠️ {client.name} exceeded cost limit",
                            f"{client.name} has used ${cost:.4f}/${cost_limit:.2f} USD this month ({pct:.0f}%). Check usage.",
                            severity="error",
                        )
                        created += 1
                elif pct >= threshold:
                    if not await _alert_exists("usage_warning", ref):
                        await _create_alert(
                            "usage_warning", ref,
                            f"🔔 {client.name} nearing cost limit",
                            f"{client.name} has used ${cost:.4f}/${cost_limit:.2f} USD this month ({pct:.0f}%).",
                            severity="warning",
                        )
                        created += 1

    return created


# ── PAYMENT ALERTS ────────────────────────────────────────────────────────────

async def _check_payment_alerts() -> int:
    """Alert when a client payment is overdue."""
    created = 0
    async with async_session() as session:
        clients = (await session.execute(
            select(Client).where(Client.payment_status == "overdue")
        )).scalars().all()

        for client in clients:
            ref = f"client_{client.id}_payment"
            if not await _alert_exists("payment_missed", ref, window_hours=48):
                days = 0
                if client.next_payment_at:
                    days = (datetime.utcnow() - client.next_payment_at).days
                await _create_alert(
                    "payment_missed", ref,
                    f"💰 {client.name} payment overdue",
                    f"{client.name} owes ${client.monthly_price_mxn:.0f} MXN. "
                    f"Payment was due {days} day{'s' if days != 1 else ''} ago.",
                    severity="error",
                )
                created += 1

    return created


# ── API BALANCE ALERTS ────────────────────────────────────────────────────────

async def _check_api_balance_alerts() -> int:
    """Alert when a tracked API service balance is below threshold."""
    created = 0
    async with async_session() as session:
        services = (await session.execute(select(ServiceBilling))).scalars().all()

        for svc in services:
            balance = getattr(svc, "balance_usd", None)
            threshold = getattr(svc, "balance_alert_threshold_usd", None) or 5.0
            if balance is None:
                continue
            if balance <= threshold:
                ref = f"service_{svc.id}_balance"
                if not await _alert_exists("api_balance_low", ref):
                    await _create_alert(
                        "api_balance_low", ref,
                        f"⚡ {svc.display_name} balance low",
                        f"{svc.display_name} balance is ${balance:.2f} USD — below the ${threshold:.2f} alert threshold. Top up now.",
                        severity="warning" if balance > 0 else "error",
                    )
                    created += 1

    return created


# ── PARTNER PAYMENT ALERTS ────────────────────────────────────────────────────

async def _check_partner_payment_alerts() -> int:
    """Alert when a partner payment is due (checked against billing_day)."""
    from agent.models import PartnerPayment
    created = 0
    now = datetime.utcnow()

    async with async_session() as session:
        partners = (await session.execute(
            select(Client).where(Client.is_partner_bot == True)
        )).scalars().all()

        for client in partners:
            if not client.partner_monthly_cost_mxn:
                continue

            # Check last partner payment
            last_pp = (await session.execute(
                select(PartnerPayment)
                .where(PartnerPayment.client_id == client.id)
                .order_by(PartnerPayment.paid_at.desc())
                .limit(1)
            )).scalar_one_or_none()

            if last_pp:
                days_since = (now - last_pp.paid_at).days
                if days_since < 25:
                    continue  # Not due yet

            ref = f"partner_{client.id}_payment"
            if not await _alert_exists("partner_payment_due", ref, window_hours=72):
                partner = client.partner_name or "Partner"
                await _create_alert(
                    "partner_payment_due", ref,
                    f"🤝 Pay {partner} for {client.name}",
                    f"Partner cost for {client.name}: ${client.partner_monthly_cost_mxn:.0f} MXN/mo to {partner}. Mark as paid when complete.",
                    severity="info",
                )
                created += 1

    return created


# ── UNREAD COUNT (for badge) ──────────────────────────────────────────────────

async def get_unread_count() -> int:
    """Returns number of unread/undismissed alerts."""
    async with async_session() as session:
        count = (await session.execute(
            select(func.count(Alert.id)).where(Alert.read == False)
        )).scalar() or 0
    return count


async def create_booking_failed_alert(phone: str, fecha: str, hora: str, error_detail: str = "") -> None:
    """
    Creates a dashboard alert when the bot fails to book a meeting on Google Calendar.
    Deduped to once per 2 hours per phone to avoid spam.
    """
    ref = f"booking_failed_{phone}_{fecha}_{hora}"
    if await _alert_exists("booking_failed", ref, window_hours=2):
        return
    body = f"Bot failed to book a meeting for {phone} on {fecha} at {hora}."
    if error_detail:
        body += f" Error: {error_detail}"
    body += " Check Google Calendar credentials and fix manually if needed."
    await _create_alert(
        "booking_failed", ref,
        f"📅 Booking failed — {fecha} {hora}",
        body,
        severity="warning",
    )
