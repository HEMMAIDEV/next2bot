# dashboard/router.py — All dashboard routes
import os
import httpx
import time
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, desc, text
from agent.database import async_session
from agent.models import Lead, Message, UsageLog, FunnelEvent, Client, ServiceBilling, LearnedPattern, Alert, PartnerPayment, Plan
from dashboard.auth import (
    create_session_token, check_credentials, get_current_user, COOKIE_NAME
)

router = APIRouter(prefix="/dashboard")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

STATUSES = ["new", "qualified", "follow_up", "demo_booked", "won", "lost"]
STATUS_LABELS = {
    "new": "New", "qualified": "Qualified", "follow_up": "Follow-up",
    "demo_booked": "Demo Booked", "won": "Won", "lost": "Lost"
}
FUNNEL_COLORS = {
    "new": "bg-blue-500", "qualified": "bg-purple-500", "follow_up": "bg-yellow-500",
    "demo_booked": "bg-green-500", "won": "bg-emerald-500", "lost": "bg-red-500"
}


def _redirect_login():
    return RedirectResponse(url="/dashboard/login", status_code=302)


def _check_auth(request: Request):
    return get_current_user(request)


async def _alert_badge() -> int:
    """Returns unread alert count for the sidebar badge."""
    try:
        from agent.alerts import get_unread_count
        return await get_unread_count()
    except Exception:
        return 0


# ── AUTH ──────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": error})


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if check_credentials(username, password):
        token = create_session_token(username)
        response = RedirectResponse(url="/dashboard", status_code=302)
        response.set_cookie(COOKIE_NAME, token, max_age=60*60*8, httponly=True)
        return response
    return RedirectResponse(url="/dashboard/login?error=Invalid+credentials", status_code=302)


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/dashboard/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


# ── HOME / OVERVIEW ───────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not _check_auth(request):
        return _redirect_login()

    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)

    async with async_session() as session:
        total_leads = (await session.execute(select(func.count(Lead.id)))).scalar()
        new_today   = (await session.execute(select(func.count()).where(Lead.created_at >= today))).scalar()
        chats_today = (await session.execute(
            select(func.count()).where(UsageLog.event_type.in_(["chat","chat_tool_followup"]), UsageLog.created_at >= today)
        )).scalar()
        chats_week = (await session.execute(
            select(func.count()).where(UsageLog.event_type.in_(["chat","chat_tool_followup"]), UsageLog.created_at >= week_ago)
        )).scalar()
        cost_today = float((await session.execute(
            select(func.sum(UsageLog.cost_usd)).where(UsageLog.created_at >= today)
        )).scalar() or 0)
        cost_month = float((await session.execute(
            select(func.sum(UsageLog.cost_usd)).where(UsageLog.created_at >= now - timedelta(days=30))
        )).scalar() or 0)
        errors_today = (await session.execute(
            select(func.count()).where(UsageLog.success == False, UsageLog.created_at >= today)
        )).scalar()
        avg_lat = (await session.execute(
            select(func.avg(UsageLog.latency_ms)).where(UsageLog.created_at >= today, UsageLog.success == True)
        )).scalar()

        # Funnel counts
        funnel = []
        for s in STATUSES:
            count = (await session.execute(select(func.count()).where(Lead.status == s))).scalar()
            funnel.append((s, STATUS_LABELS[s], FUNNEL_COLORS[s], count))

        # Recent leads
        result = await session.execute(select(Lead).order_by(desc(Lead.last_seen_at)).limit(8))
        recent_leads = result.scalars().all()

        # Week chart
        week_chart = []
        for i in range(6, -1, -1):
            day = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day + timedelta(days=1)
            count = (await session.execute(
                select(func.count()).where(
                    UsageLog.event_type.in_(["chat","chat_tool_followup"]),
                    UsageLog.created_at >= day,
                    UsageLog.created_at < day_end,
                )
            )).scalar()
            week_chart.append({"label": day.strftime("%a"), "count": count})

    alert_badge = await _alert_badge()
    return templates.TemplateResponse(request=request, name="home.html", context={
        "active_page": "home",
        "total_leads": total_leads,
        "new_today": new_today,
        "chats_today": chats_today,
        "chats_week": chats_week,
        "cost_today": round(cost_today, 4),
        "cost_month": round(cost_month, 4),
        "errors_today": errors_today,
        "avg_latency_ms": int(avg_lat or 0),
        "funnel": funnel,
        "recent_leads": recent_leads,
        "week_chart": week_chart,
        "alert_badge": alert_badge,
    })


# ── LEADS BOARD ───────────────────────────────────────────────

@router.get("/leads", response_class=HTMLResponse)
async def leads_board(request: Request, status: str = "", search: str = ""):
    if not _check_auth(request):
        return _redirect_login()

    async with async_session() as session:
        counts = {}
        for s in STATUSES:
            r = await session.execute(select(func.count()).where(Lead.status == s))
            counts[s] = r.scalar()

        q = select(Lead).order_by(desc(Lead.last_seen_at))
        if status:
            q = q.where(Lead.status == status)
        if search:
            q = q.where(
                Lead.phone.ilike(f"%{search}%") |
                Lead.name.ilike(f"%{search}%") |
                Lead.company.ilike(f"%{search}%")
            )
        leads = (await session.execute(q)).scalars().all()

    return templates.TemplateResponse(request=request, name="leads.html", context={
        "active_page": "leads",
        "leads": leads,
        "counts": counts,
        "statuses": STATUSES,
        "status_labels": STATUS_LABELS,
        "active_status": status,
        "search": search,
    })


# ── LEAD DETAIL ───────────────────────────────────────────────

@router.get("/leads/{phone}", response_class=HTMLResponse)
async def lead_detail(request: Request, phone: str):
    if not _check_auth(request):
        return _redirect_login()

    phone = phone.replace("-", "@")
    async with async_session() as session:
        lead = (await session.execute(select(Lead).where(Lead.phone == phone))).scalar_one_or_none()
        if not lead:
            return HTMLResponse("Lead not found", status_code=404)
        messages = (await session.execute(
            select(Message).where(Message.phone == phone).order_by(Message.created_at)
        )).scalars().all()
        events = (await session.execute(
            select(FunnelEvent).where(FunnelEvent.phone == phone).order_by(FunnelEvent.created_at)
        )).scalars().all()

    return templates.TemplateResponse(request=request, name="lead_detail.html", context={
        "active_page": "leads",
        "lead": lead,
        "messages": messages,
        "events": events,
        "statuses": STATUSES,
        "status_labels": STATUS_LABELS,
    })


@router.post("/leads/{phone}/status")
async def update_status(request: Request, phone: str, status: str = Form(...)):
    if not _check_auth(request):
        return _redirect_login()
    phone = phone.replace("-", "@")
    from agent.leads import update_lead_status
    await update_lead_status(phone, status, triggered_by="owner")
    # Trigger pattern learning when a lead is won or demo_booked
    if status in ("won", "demo_booked"):
        try:
            import asyncio
            from agent.crm import extract_and_store_pattern
            asyncio.create_task(extract_and_store_pattern(phone, status))
        except Exception:
            pass
    return RedirectResponse(url=f"/dashboard/leads/{phone.replace('@', '-')}", status_code=302)


@router.post("/leads/{phone}/notes")
async def update_notes(request: Request, phone: str, notes: str = Form(...)):
    if not _check_auth(request):
        return _redirect_login()
    phone = phone.replace("-", "@")
    from agent.leads import update_lead_field
    await update_lead_field(phone, notes=notes)
    return RedirectResponse(url=f"/dashboard/leads/{phone.replace('@', '-')}", status_code=302)


@router.post("/leads/{phone}/summarize")
async def summarize_lead(request: Request, phone: str):
    if not _check_auth(request):
        return _redirect_login()
    phone = phone.replace("-", "@")
    async with async_session() as session:
        messages = (await session.execute(
            select(Message).where(Message.phone == phone).order_by(Message.created_at)
        )).scalars().all()
    if messages:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())
        conversation = "\n".join(f"{m.role.upper()}: {m.content}" for m in messages[-20:])
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"Summarize this WhatsApp sales conversation in 2-3 sentences. Focus on: who they are, what they need, and next step.\n\n{conversation}"}],
            max_tokens=200,
        )
        from agent.leads import update_lead_field
        await update_lead_field(phone, ai_summary=response.choices[0].message.content)
    return RedirectResponse(url=f"/dashboard/leads/{phone.replace('@', '-')}", status_code=302)


# ── MONITOR ───────────────────────────────────────────────────

@router.get("/monitor", response_class=HTMLResponse)
async def monitor(request: Request):
    if not _check_auth(request):
        return _redirect_login()

    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago  = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    async with async_session() as session:
        def chat_count(since):
            return select(func.count()).where(
                UsageLog.event_type.in_(["chat","chat_tool_followup"]), UsageLog.created_at >= since)

        chats_today = (await session.execute(chat_count(today))).scalar()
        chats_week  = (await session.execute(chat_count(week_ago))).scalar()
        chats_month = (await session.execute(chat_count(month_ago))).scalar()

        tok = (await session.execute(
            select(func.sum(UsageLog.tokens_in), func.sum(UsageLog.tokens_out))
            .where(UsageLog.created_at >= today)
        )).one()
        tokens_in_today  = tok[0] or 0
        tokens_out_today = tok[1] or 0

        tok_month = (await session.execute(
            select(func.sum(UsageLog.tokens_in), func.sum(UsageLog.tokens_out))
            .where(UsageLog.created_at >= month_ago)
        )).one()

        cost_today = float((await session.execute(select(func.sum(UsageLog.cost_usd)).where(UsageLog.created_at >= today))).scalar() or 0)
        cost_week  = float((await session.execute(select(func.sum(UsageLog.cost_usd)).where(UsageLog.created_at >= week_ago))).scalar() or 0)
        cost_month = float((await session.execute(select(func.sum(UsageLog.cost_usd)).where(UsageLog.created_at >= month_ago))).scalar() or 0)

        errors_today = (await session.execute(
            select(func.count()).where(UsageLog.success == False, UsageLog.created_at >= today)
        )).scalar()
        avg_lat = (await session.execute(
            select(func.avg(UsageLog.latency_ms)).where(UsageLog.created_at >= today, UsageLog.success == True)
        )).scalar()

        recent_errors = (await session.execute(
            select(UsageLog).where(UsageLog.success == False).order_by(desc(UsageLog.created_at)).limit(10)
        )).scalars().all()

        # Hourly chart (today)
        hourly_chart = []
        for h in range(24):
            h_start = today + timedelta(hours=h)
            h_end   = h_start + timedelta(hours=1)
            count = (await session.execute(
                select(func.count()).where(
                    UsageLog.event_type.in_(["chat","chat_tool_followup"]),
                    UsageLog.created_at >= h_start, UsageLog.created_at < h_end,
                )
            )).scalar()
            hourly_chart.append({"hour": h, "count": count})

        # Daily cost chart (7 days)
        daily_chart = []
        for i in range(6, -1, -1):
            day = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day + timedelta(days=1)
            cost = float((await session.execute(
                select(func.sum(UsageLog.cost_usd)).where(
                    UsageLog.created_at >= day, UsageLog.created_at < day_end)
            )).scalar() or 0)
            daily_chart.append({"label": day.strftime("%a"), "cost": round(cost, 5)})

        # Cost breakdown by provider
        providers_result = (await session.execute(
            select(UsageLog.provider, func.sum(UsageLog.cost_usd).label("total"))
            .where(UsageLog.created_at >= month_ago)
            .group_by(UsageLog.provider)
        )).all()
        total_cost = sum(float(r.total or 0) for r in providers_result) or 1
        cost_breakdown = [
            {"provider": r.provider, "cost": float(r.total or 0), "pct": round(float(r.total or 0) / total_cost * 100)}
            for r in providers_result
        ]

    return templates.TemplateResponse(request=request, name="monitor.html", context={
        "active_page": "monitor",
        "chats_today": chats_today, "chats_week": chats_week, "chats_month": chats_month,
        "tokens_in_today": tokens_in_today, "tokens_out_today": tokens_out_today,
        "cost_today": round(cost_today, 4), "cost_week": round(cost_week, 4), "cost_month": round(cost_month, 4),
        "errors_today": errors_today, "avg_latency_ms": int(avg_lat or 0),
        "recent_errors": recent_errors,
        "hourly_chart": hourly_chart,
        "daily_chart": daily_chart,
        "cost_breakdown": cost_breakdown,
        "token_chart": {"input": int(tok_month[0] or 0), "output": int(tok_month[1] or 0)},
    })


# ── SERVICES ──────────────────────────────────────────────────

@router.get("/services", response_class=HTMLResponse)
async def services(request: Request):
    if not _check_auth(request):
        return _redirect_login()

    services_data = []

    # 1. Next2Bot / Railway
    services_data.append({
        "name": "Next2Bot",
        "type": "WhatsApp AI Agent",
        "status": "ok",
        "metrics": {
            "Platform": "Railway",
            "Runtime": "Python 3.11",
            "Framework": "FastAPI + Uvicorn",
            "Model": "gpt-4o-mini",
        },
        "note": None,
    })

    # 2. OpenAI
    async with async_session() as session:
        now = datetime.utcnow()
        month_ago = now - timedelta(days=30)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)

        tok_month = (await session.execute(
            select(func.sum(UsageLog.tokens_in + UsageLog.tokens_out)).where(UsageLog.created_at >= month_ago)
        )).scalar() or 0
        cost_month = float((await session.execute(
            select(func.sum(UsageLog.cost_usd)).where(UsageLog.created_at >= month_ago)
        )).scalar() or 0)
        cost_today = float((await session.execute(
            select(func.sum(UsageLog.cost_usd)).where(UsageLog.created_at >= today)
        )).scalar() or 0)
        errors = (await session.execute(
            select(func.count()).where(UsageLog.provider == "openai", UsageLog.success == False, UsageLog.created_at >= today)
        )).scalar()
        avg_lat = (await session.execute(
            select(func.avg(UsageLog.latency_ms)).where(UsageLog.provider == "openai", UsageLog.success == True, UsageLog.created_at >= today)
        )).scalar()

    services_data.append({
        "name": "OpenAI",
        "type": "LLM Provider — gpt-4o-mini",
        "status": "ok" if errors == 0 else "degraded",
        "metrics": {
            "Tokens this month": f"{int(tok_month):,}",
            "Cost this month": f"${cost_month:.4f}",
            "Cost today": f"${cost_today:.4f}",
            "Avg latency": f"{int(avg_lat or 0)}ms",
            "Errors today": str(errors),
            "Pricing": "$0.15/1M in · $0.60/1M out",
        },
        "note": "Balance not available via API — shown from tracked usage logs.",
    })

    # 3. Whapi.cloud
    whapi_token = os.getenv("WHAPI_TOKEN", "")
    whapi_status = "unknown"
    whapi_metrics = {}
    if whapi_token:
        try:
            start = time.time()
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    "https://gate.whapi.cloud/settings",
                    headers={"Authorization": f"Bearer {whapi_token}"}
                )
            latency = int((time.time() - start) * 1000)
            if r.status_code == 200:
                data = r.json()
                whapi_status = "ok"
                whapi_metrics = {
                    "Status": "Connected",
                    "Latency": f"{latency}ms",
                    "Webhook": os.getenv("RAILWAY_PUBLIC_DOMAIN", "Configured"),
                }
            else:
                whapi_status = "degraded"
                whapi_metrics = {"HTTP Status": str(r.status_code), "Latency": f"{latency}ms"}
        except Exception as e:
            whapi_status = "down"
            whapi_metrics = {"Error": str(e)[:60]}
    else:
        whapi_metrics = {"Token": "Not configured"}

    services_data.append({
        "name": "Whapi.cloud",
        "type": "WhatsApp Provider",
        "status": whapi_status,
        "metrics": whapi_metrics,
        "note": "Messages delivered via Whapi REST API.",
    })

    # 4. Google Calendar
    cal_id = os.getenv("GOOGLE_CALENDAR_ID", "")
    cal_creds = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
    cal_status = "ok" if (cal_id and cal_creds) else "degraded"
    services_data.append({
        "name": "Google Calendar",
        "type": "Calendar Integration",
        "status": cal_status,
        "metrics": {
            "Calendar ID": cal_id[:20] + "…" if cal_id else "Not set",
            "Credentials": "Configured" if cal_creds else "Not set",
            "Scope": "Read/Write events",
        },
        "note": "Events created when a lead books a demo via Next2Bot.",
    })

    # 5. PostgreSQL
    db_status = "ok"
    db_metrics = {}
    try:
        start = time.time()
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        latency = int((time.time() - start) * 1000)
        db_metrics = {"Status": "Connected", "Latency": f"{latency}ms", "Engine": "PostgreSQL"}
    except Exception as e:
        db_status = "down"
        db_metrics = {"Error": str(e)[:60]}

    services_data.append({
        "name": "Database",
        "type": "PostgreSQL — Railway",
        "status": db_status,
        "metrics": db_metrics,
        "note": "Stores all leads, messages, and usage logs.",
    })

    return templates.TemplateResponse(request=request, name="services.html", context={
        "active_page": "services",
        "services": services_data,
    })


# ── CLIENTS CONTROL PANEL ─────────────────────────────────────────────────────

PLANS = ["starter", "pro", "enterprise"]
PAYMENT_STATUSES = {"ok": "bg-emerald-500", "pending": "bg-yellow-500", "overdue": "bg-red-500"}


@router.get("/clients", response_class=HTMLResponse)
async def clients_list(request: Request):
    if not _check_auth(request):
        return _redirect_login()

    async with async_session() as session:
        clients = (await session.execute(
            select(Client).order_by(Client.created_at.desc())
        )).scalars().all()

        # Per-client usage this month
        now = datetime.utcnow()
        month_ago = now - timedelta(days=30)
        usage_map = {}
        for c in clients:
            if c.bot_phone_number:
                cost = float((await session.execute(
                    select(func.sum(UsageLog.cost_usd))
                    .where(UsageLog.phone == c.bot_phone_number, UsageLog.created_at >= month_ago)
                )).scalar() or 0)
                msgs = (await session.execute(
                    select(func.count(UsageLog.id))
                    .where(UsageLog.phone == c.bot_phone_number, UsageLog.created_at >= month_ago)
                )).scalar() or 0
                usage_map[c.id] = {"cost": round(cost, 4), "messages": msgs}
            else:
                usage_map[c.id] = {"cost": 0, "messages": 0}

        active_count   = sum(1 for c in clients if c.bot_active)
        overdue_count  = sum(1 for c in clients if c.payment_status == "overdue")
        mrr = sum(c.monthly_price_mxn for c in clients if c.bot_active)

    return templates.TemplateResponse(request=request, name="clients.html", context={
        "active_page": "clients",
        "clients": clients,
        "usage_map": usage_map,
        "plans": PLANS,
        "payment_statuses": PAYMENT_STATUSES,
        "active_count": active_count,
        "overdue_count": overdue_count,
        "mrr": mrr,
    })


@router.get("/clients/new", response_class=HTMLResponse)
async def client_new_form(request: Request):
    if not _check_auth(request):
        return _redirect_login()
    return templates.TemplateResponse(request=request, name="client_form.html", context={
        "active_page": "clients", "client": None, "plans": PLANS, "error": "",
    })


@router.post("/clients/new")
async def client_create(
    request: Request,
    name: str = Form(...),
    owner_name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    niche: str = Form(""),
    plan: str = Form("starter"),
    monthly_price_mxn: float = Form(0.0),
    setup_price_mxn: float = Form(0.0),
    billing_day: int = Form(1),
    bot_phone_number: str = Form(""),
    deployment_url: str = Form(""),
    notes: str = Form(""),
    msg_limit: int = Form(0),
    cost_limit_usd: float = Form(0.0),
    alert_threshold_pct: int = Form(80),
    is_partner_bot: str = Form(""),
    partner_name: str = Form(""),
    partner_monthly_cost_mxn: float = Form(0.0),
    partner_api_excluded: str = Form(""),
):
    if not _check_auth(request):
        return _redirect_login()

    now = datetime.utcnow()
    next_payment = now.replace(day=min(billing_day, 28)) + timedelta(days=30)

    async with async_session() as session:
        client = Client(
            name=name, owner_name=owner_name or None, phone=phone or None,
            email=email or None, niche=niche or None, plan=plan,
            bot_active=True, monthly_price_mxn=monthly_price_mxn,
            setup_price_mxn=setup_price_mxn, billing_day=billing_day,
            next_payment_at=next_payment, payment_status="ok",
            bot_phone_number=bot_phone_number or None,
            deployment_url=deployment_url or None, notes=notes or None,
            msg_limit=msg_limit or None, cost_limit_usd=cost_limit_usd,
            alert_threshold_pct=alert_threshold_pct,
            is_partner_bot=bool(is_partner_bot),
            partner_name=partner_name or None,
            partner_monthly_cost_mxn=partner_monthly_cost_mxn,
            partner_api_excluded=bool(partner_api_excluded),
            created_at=now, updated_at=now,
        )
        session.add(client)
        await session.commit()

    return RedirectResponse(url="/dashboard/clients", status_code=302)


@router.get("/clients/{client_id}", response_class=HTMLResponse)
async def client_detail(request: Request, client_id: int):
    if not _check_auth(request):
        return _redirect_login()

    async with async_session() as session:
        client = (await session.execute(
            select(Client).where(Client.id == client_id)
        )).scalar_one_or_none()
        if not client:
            return HTMLResponse("Client not found", status_code=404)

        now = datetime.utcnow()
        month_ago = now - timedelta(days=30)

        # Usage stats
        cost_month = float((await session.execute(
            select(func.sum(UsageLog.cost_usd))
            .where(UsageLog.phone == client.bot_phone_number, UsageLog.created_at >= month_ago)
        )).scalar() or 0) if client.bot_phone_number else 0

        msgs_month = (await session.execute(
            select(func.count(UsageLog.id))
            .where(UsageLog.phone == client.bot_phone_number, UsageLog.created_at >= month_ago)
        )).scalar() or 0 if client.bot_phone_number else 0

        # Daily usage chart (7 days)
        daily_chart = []
        for i in range(6, -1, -1):
            day = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day + timedelta(days=1)
            cnt = 0
            if client.bot_phone_number:
                cnt = (await session.execute(
                    select(func.count(UsageLog.id))
                    .where(UsageLog.phone == client.bot_phone_number,
                           UsageLog.created_at >= day, UsageLog.created_at < day_end)
                )).scalar() or 0
            daily_chart.append({"label": day.strftime("%a"), "count": cnt})

        # Partner payment history
        partner_payments = []
        if client.is_partner_bot:
            pp_result = await session.execute(
                select(PartnerPayment)
                .where(PartnerPayment.client_id == client.id)
                .order_by(PartnerPayment.paid_at.desc())
                .limit(12)
            )
            partner_payments = pp_result.scalars().all()

        # Usage limits progress
        msg_limit = client.msg_limit or 0
        cost_limit = client.cost_limit_usd or 0
        msg_pct = min(round((msgs_month / msg_limit * 100) if msg_limit else 0), 100)
        cost_pct = min(round((cost_month / cost_limit * 100) if cost_limit else 0), 100)

    alert_badge = await _alert_badge()
    return templates.TemplateResponse(request=request, name="client_detail.html", context={
        "active_page": "clients",
        "client": client,
        "cost_month": round(cost_month, 4),
        "msgs_month": msgs_month,
        "daily_chart": daily_chart,
        "plans": PLANS,
        "payment_statuses": PAYMENT_STATUSES,
        "days_until_payment": (client.next_payment_at - now).days if client.next_payment_at else None,
        "partner_payments": partner_payments,
        "msg_pct": msg_pct,
        "cost_pct": cost_pct,
        "alert_badge": alert_badge,
    })


@router.post("/clients/{client_id}/toggle")
async def client_toggle_bot(request: Request, client_id: int):
    if not _check_auth(request):
        return _redirect_login()
    async with async_session() as session:
        client = (await session.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
        if client:
            client.bot_active = not client.bot_active
            client.updated_at = datetime.utcnow()
            await session.commit()
    return RedirectResponse(url=f"/dashboard/clients/{client_id}", status_code=302)


@router.post("/clients/{client_id}/payment")
async def client_record_payment(request: Request, client_id: int):
    if not _check_auth(request):
        return _redirect_login()
    async with async_session() as session:
        client = (await session.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
        if client:
            now = datetime.utcnow()
            client.last_payment_at = now
            client.next_payment_at = now.replace(day=min(client.billing_day, 28)) + timedelta(days=30)
            client.payment_status = "ok"
            client.updated_at = now
            await session.commit()
    return RedirectResponse(url=f"/dashboard/clients/{client_id}", status_code=302)


@router.post("/clients/{client_id}/edit")
async def client_edit(
    request: Request, client_id: int,
    name: str = Form(...), owner_name: str = Form(""), phone: str = Form(""),
    email: str = Form(""), niche: str = Form(""), plan: str = Form("starter"),
    monthly_price_mxn: float = Form(0.0), billing_day: int = Form(1),
    bot_phone_number: str = Form(""), deployment_url: str = Form(""),
    notes: str = Form(""),
    msg_limit: int = Form(0), cost_limit_usd: float = Form(0.0),
    alert_threshold_pct: int = Form(80),
    is_partner_bot: str = Form(""),
    partner_name: str = Form(""),
    partner_monthly_cost_mxn: float = Form(0.0),
    partner_api_excluded: str = Form(""),
):
    if not _check_auth(request):
        return _redirect_login()
    async with async_session() as session:
        client = (await session.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
        if client:
            client.name = name
            client.owner_name = owner_name or None
            client.phone = phone or None
            client.email = email or None
            client.niche = niche or None
            client.plan = plan
            client.monthly_price_mxn = monthly_price_mxn
            client.billing_day = billing_day
            client.bot_phone_number = bot_phone_number or None
            client.deployment_url = deployment_url or None
            client.notes = notes or None
            client.msg_limit = msg_limit or None
            client.cost_limit_usd = cost_limit_usd
            client.alert_threshold_pct = alert_threshold_pct
            client.is_partner_bot = bool(is_partner_bot)
            client.partner_name = partner_name or None
            client.partner_monthly_cost_mxn = partner_monthly_cost_mxn
            client.partner_api_excluded = bool(partner_api_excluded)
            client.updated_at = datetime.utcnow()
            await session.commit()
    return RedirectResponse(url=f"/dashboard/clients/{client_id}", status_code=302)


@router.post("/clients/{client_id}/partner-payment")
async def client_partner_payment(
    request: Request, client_id: int,
    amount_mxn: float = Form(...),
    notes: str = Form(""),
):
    """Record a payment made to a partner for a client bot."""
    if not _check_auth(request):
        return _redirect_login()
    async with async_session() as session:
        client = (await session.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
        if client and client.is_partner_bot:
            pp = PartnerPayment(
                client_id=client_id,
                partner_name=client.partner_name,
                amount_mxn=amount_mxn,
                notes=notes or None,
                paid_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
            session.add(pp)
            await session.commit()
    return RedirectResponse(url=f"/dashboard/clients/{client_id}", status_code=302)


# ── BILLING & PAYMENTS ────────────────────────────────────────────────────────

@router.get("/billing", response_class=HTMLResponse)
async def billing(request: Request):
    if not _check_auth(request):
        return _redirect_login()

    now = datetime.utcnow()
    month_ago = now - timedelta(days=30)

    async with async_session() as session:
        services = (await session.execute(
            select(ServiceBilling).order_by(ServiceBilling.service_name)
        )).scalars().all()

        # My revenue from clients
        clients = (await session.execute(select(Client))).scalars().all()
        active_clients = [c for c in clients if c.bot_active]
        mrr_mxn = sum(c.monthly_price_mxn for c in active_clients)
        overdue_clients = [c for c in clients if c.payment_status == "overdue"]
        pending_clients = [c for c in clients if c.payment_status == "pending"]

        # My costs
        openai_cost = float((await session.execute(
            select(func.sum(UsageLog.cost_usd)).where(UsageLog.created_at >= month_ago)
        )).scalar() or 0)

        total_fixed_usd = sum(s.monthly_cost_usd for s in services if s.billing_cycle == "monthly")
        total_cost_usd = total_fixed_usd + openai_cost

        # Upcoming payments (next 30 days)
        upcoming = []
        for s in services:
            if s.next_due_at:
                delta = (s.next_due_at - now).days
                if 0 <= delta <= 30:
                    upcoming.append({
                        "name": s.display_name,
                        "due_in_days": delta,
                        "amount_usd": s.monthly_cost_usd,
                        "amount_mxn": s.monthly_cost_mxn,
                        "auto_pay": s.auto_pay,
                    })
        upcoming.sort(key=lambda x: x["due_in_days"])

        # Client payment calendar
        client_payments = []
        for c in clients:
            if c.next_payment_at:
                delta = (c.next_payment_at - now).days
                client_payments.append({
                    "name": c.name,
                    "due_in_days": delta,
                    "amount_mxn": c.monthly_price_mxn,
                    "status": c.payment_status,
                    "id": c.id,
                })
        client_payments.sort(key=lambda x: x["due_in_days"])

        # Learned patterns count
        patterns_count = (await session.execute(
            select(func.count(LearnedPattern.id)).where(LearnedPattern.active == True)
        )).scalar() or 0

    return templates.TemplateResponse(request=request, name="billing.html", context={
        "active_page": "billing",
        "services": services,
        "mrr_mxn": mrr_mxn,
        "active_clients_count": len(active_clients),
        "overdue_clients": overdue_clients,
        "pending_clients": pending_clients,
        "openai_cost_month": round(openai_cost, 4),
        "total_fixed_usd": round(total_fixed_usd, 2),
        "total_cost_usd": round(total_cost_usd, 4),
        "upcoming_payments": upcoming,
        "client_payments": client_payments,
        "patterns_count": patterns_count,
    })


@router.post("/billing/{service_id}/paid")
async def mark_service_paid(request: Request, service_id: int):
    if not _check_auth(request):
        return _redirect_login()
    async with async_session() as session:
        svc = (await session.execute(select(ServiceBilling).where(ServiceBilling.id == service_id))).scalar_one_or_none()
        if svc:
            now = datetime.utcnow()
            svc.last_paid_at = now
            svc.next_due_at = now.replace(day=min(svc.billing_day, 28)) + timedelta(days=30)
            svc.updated_at = now
            await session.commit()
    return RedirectResponse(url="/dashboard/billing", status_code=302)


@router.post("/billing/{service_id}/balance")
async def update_service_balance(
    request: Request, service_id: int,
    balance_usd: float = Form(...),
    balance_alert_threshold_usd: float = Form(5.0),
):
    """Manually update the current balance for a service (e.g. OpenAI prepaid credits)."""
    if not _check_auth(request):
        return _redirect_login()
    async with async_session() as session:
        svc = (await session.execute(select(ServiceBilling).where(ServiceBilling.id == service_id))).scalar_one_or_none()
        if svc:
            svc.balance_usd = balance_usd
            svc.balance_alert_threshold_usd = balance_alert_threshold_usd
            svc.updated_at = datetime.utcnow()
            await session.commit()
    return RedirectResponse(url="/dashboard/billing", status_code=302)


# ── ALERTS ────────────────────────────────────────────────────────────────────

@router.get("/alerts", response_class=HTMLResponse)
async def alerts_page(request: Request):
    if not _check_auth(request):
        return _redirect_login()

    async with async_session() as session:
        alerts = (await session.execute(
            select(Alert)
            .where(Alert.dismissed == False)
            .order_by(Alert.created_at.desc())
            .limit(100)
        )).scalars().all()

    alert_badge = await _alert_badge()
    return templates.TemplateResponse(request=request, name="alerts.html", context={
        "active_page": "alerts",
        "alerts": alerts,
        "alert_badge": alert_badge,
    })


@router.post("/alerts/{alert_id}/read")
async def mark_alert_read(request: Request, alert_id: int):
    if not _check_auth(request):
        return _redirect_login()
    async with async_session() as session:
        alert = (await session.execute(select(Alert).where(Alert.id == alert_id))).scalar_one_or_none()
        if alert:
            alert.read = True
            alert.read_at = datetime.utcnow()
            await session.commit()
    return RedirectResponse(url="/dashboard/alerts", status_code=302)


@router.post("/alerts/{alert_id}/dismiss")
async def dismiss_alert(request: Request, alert_id: int):
    if not _check_auth(request):
        return _redirect_login()
    async with async_session() as session:
        alert = (await session.execute(select(Alert).where(Alert.id == alert_id))).scalar_one_or_none()
        if alert:
            alert.dismissed = True
            alert.read = True
            alert.read_at = datetime.utcnow()
            await session.commit()
    return RedirectResponse(url="/dashboard/alerts", status_code=302)


@router.post("/alerts/read-all")
async def read_all_alerts(request: Request):
    if not _check_auth(request):
        return _redirect_login()
    async with async_session() as session:
        alerts = (await session.execute(
            select(Alert).where(Alert.read == False)
        )).scalars().all()
        now = datetime.utcnow()
        for a in alerts:
            a.read = True
            a.read_at = now
        await session.commit()
    return RedirectResponse(url="/dashboard/alerts", status_code=302)


@router.post("/alerts/run")
async def run_alerts_now(request: Request):
    """Manually trigger alert engine (on-demand)."""
    if not _check_auth(request):
        return _redirect_login()
    try:
        import asyncio
        from agent.alerts import generate_all_alerts
        asyncio.create_task(generate_all_alerts())
    except Exception:
        pass
    return RedirectResponse(url="/dashboard/alerts", status_code=302)


# ── FORECAST ──────────────────────────────────────────────────────────────────

@router.get("/forecast", response_class=HTMLResponse)
async def forecast_page(request: Request):
    if not _check_auth(request):
        return _redirect_login()

    from agent.forecasting import build_forecast
    forecast = await build_forecast()
    alert_badge = await _alert_badge()

    return templates.TemplateResponse(request=request, name="forecast.html", context={
        "active_page": "forecast",
        "forecast": forecast,
        "alert_badge": alert_badge,
    })
