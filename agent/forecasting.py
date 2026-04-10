# agent/forecasting.py — Revenue, cost, and margin forecasting
"""
Projects month-end revenue, costs, and net margin based on:
- Current confirmed client MRR
- Daily OpenAI usage rate → projected month-end cost
- Fixed service costs
- Partner costs (tracked separately)
"""
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, func
from agent.database import async_session
from agent.models import Client, ServiceBilling, UsageLog

logger = logging.getLogger("agentkit")

USD_TO_MXN = 18.0  # Conservative fixed rate — update as needed


async def build_forecast() -> dict:
    """
    Returns a full forecast dict for the current and next month.
    All money is in MXN unless suffixed _usd.
    """
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days_elapsed = max((now - month_start).days, 1)
    days_in_month = _days_in_month(now.year, now.month)
    days_remaining = days_in_month - days_elapsed

    async with async_session() as session:
        # ── REVENUE ──────────────────────────────────────────────────────────
        clients = (await session.execute(select(Client))).scalars().all()
        active_clients = [c for c in clients if c.bot_active]
        partner_clients = [c for c in clients if getattr(c, "is_partner_bot", False)]

        # MRR = what clients pay us
        mrr_mxn = sum(c.monthly_price_mxn for c in active_clients)

        # Partner costs = what we pay partners (NOT our revenue)
        total_partner_cost_mxn = sum(
            (getattr(c, "partner_monthly_cost_mxn", 0) or 0)
            for c in partner_clients
        )

        # ── OPENAI COSTS ─────────────────────────────────────────────────────
        # Only count non-partner-excluded clients
        excluded_phones = {
            c.bot_phone_number
            for c in clients
            if getattr(c, "is_partner_bot", False) and getattr(c, "partner_api_excluded", False) and c.bot_phone_number
        }

        total_cost_month_usd = 0.0
        phone_usage = {}

        # Total OpenAI cost this month (all)
        all_cost_usd = float((await session.execute(
            select(func.sum(UsageLog.cost_usd))
            .where(UsageLog.created_at >= month_start)
        )).scalar() or 0)

        # Partner-excluded cost this month
        excluded_cost_usd = 0.0
        for phone in excluded_phones:
            ec = float((await session.execute(
                select(func.sum(UsageLog.cost_usd))
                .where(UsageLog.phone == phone, UsageLog.created_at >= month_start)
            )).scalar() or 0)
            excluded_cost_usd += ec

        n2h_cost_usd = all_cost_usd - excluded_cost_usd  # our real AI cost

        # Daily rate → project to end of month
        daily_cost_usd = n2h_cost_usd / days_elapsed
        projected_ai_cost_usd = daily_cost_usd * days_in_month
        projected_ai_cost_mxn = projected_ai_cost_usd * USD_TO_MXN

        # ── FIXED COSTS ───────────────────────────────────────────────────────
        services = (await session.execute(select(ServiceBilling))).scalars().all()
        fixed_cost_usd = sum(s.monthly_cost_usd for s in services if s.billing_cycle == "monthly")
        fixed_cost_mxn = fixed_cost_usd * USD_TO_MXN

        # ── PER-CLIENT BREAKDOWN ─────────────────────────────────────────────
        per_client = []
        for c in active_clients:
            client_cost_usd = 0.0
            client_msgs = 0
            if c.bot_phone_number and c.bot_phone_number not in excluded_phones:
                client_cost_usd = float((await session.execute(
                    select(func.sum(UsageLog.cost_usd))
                    .where(UsageLog.phone == c.bot_phone_number,
                           UsageLog.created_at >= month_start)
                )).scalar() or 0)
                client_msgs = (await session.execute(
                    select(func.count(UsageLog.id))
                    .where(UsageLog.phone == c.bot_phone_number,
                           UsageLog.created_at >= month_start)
                )).scalar() or 0

            client_cost_mxn = client_cost_usd * USD_TO_MXN
            partner_cost = (getattr(c, "partner_monthly_cost_mxn", 0) or 0) if getattr(c, "is_partner_bot", False) else 0
            margin_mxn = c.monthly_price_mxn - client_cost_mxn - partner_cost

            per_client.append({
                "id": c.id,
                "name": c.name,
                "niche": c.niche or "—",
                "is_partner": getattr(c, "is_partner_bot", False),
                "partner_name": getattr(c, "partner_name", None),
                "revenue_mxn": c.monthly_price_mxn,
                "ai_cost_usd": round(client_cost_usd, 4),
                "ai_cost_mxn": round(client_cost_mxn, 2),
                "partner_cost_mxn": round(partner_cost, 2),
                "margin_mxn": round(margin_mxn, 2),
                "msgs_this_month": client_msgs,
                "margin_pct": round((margin_mxn / c.monthly_price_mxn * 100) if c.monthly_price_mxn else 0, 1),
            })

        per_client.sort(key=lambda x: x["margin_mxn"], reverse=True)

        # ── TOTALS ────────────────────────────────────────────────────────────
        total_cost_mxn = projected_ai_cost_mxn + fixed_cost_mxn + total_partner_cost_mxn
        net_margin_mxn = mrr_mxn - total_cost_mxn
        margin_pct = round((net_margin_mxn / mrr_mxn * 100) if mrr_mxn else 0, 1)

        # 7-day daily revenue chart (revenue confirmed, AI cost actual)
        daily_chart = []
        for i in range(6, -1, -1):
            day = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day + timedelta(days=1)
            day_cost = float((await session.execute(
                select(func.sum(UsageLog.cost_usd))
                .where(UsageLog.created_at >= day, UsageLog.created_at < day_end)
            )).scalar() or 0) * USD_TO_MXN
            daily_chart.append({
                "label": day.strftime("%a %d"),
                "cost_mxn": round(day_cost, 2),
                "revenue_mxn": round(mrr_mxn / days_in_month, 2),  # Daily share of MRR
            })

    return {
        # Summary KPIs
        "mrr_mxn": round(mrr_mxn, 2),
        "projected_ai_cost_mxn": round(projected_ai_cost_mxn, 2),
        "projected_ai_cost_usd": round(projected_ai_cost_usd, 4),
        "fixed_cost_mxn": round(fixed_cost_mxn, 2),
        "fixed_cost_usd": round(fixed_cost_usd, 2),
        "partner_cost_mxn": round(total_partner_cost_mxn, 2),
        "total_cost_mxn": round(total_cost_mxn, 2),
        "net_margin_mxn": round(net_margin_mxn, 2),
        "margin_pct": margin_pct,
        # Month progress
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "days_remaining": days_remaining,
        "month_pct": round(days_elapsed / days_in_month * 100),
        # Actual this month
        "actual_ai_cost_usd": round(n2h_cost_usd, 4),
        "actual_ai_cost_mxn": round(n2h_cost_usd * USD_TO_MXN, 2),
        # Per-client breakdown
        "per_client": per_client,
        "active_clients": len(active_clients),
        "partner_clients": len(partner_clients),
        # Chart
        "daily_chart": daily_chart,
        "usd_to_mxn": USD_TO_MXN,
        "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
    }


def _days_in_month(year: int, month: int) -> int:
    import calendar
    return calendar.monthrange(year, month)[1]
