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
from agent.models import Lead, Message, UsageLog, FunnelEvent
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
