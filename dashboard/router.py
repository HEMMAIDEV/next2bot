# dashboard/router.py — All dashboard routes
import os
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, desc
from agent.database import async_session
from agent.models import Lead, Message, UsageLog, FunnelEvent
from dashboard.auth import (
    create_session_token, check_credentials, get_current_user, COOKIE_NAME
)

router = APIRouter(prefix="/dashboard")
templates = Jinja2Templates(directory="dashboard/templates")

STATUSES = ["new", "qualified", "follow_up", "demo_booked", "won", "lost"]
STATUS_LABELS = {
    "new": "New", "qualified": "Qualified", "follow_up": "Follow-up",
    "demo_booked": "Demo Booked", "won": "Won", "lost": "Lost"
}
STATUS_COLORS = {
    "new": "blue", "qualified": "purple", "follow_up": "yellow",
    "demo_booked": "green", "won": "emerald", "lost": "red"
}


def _redirect_login():
    return RedirectResponse(url="/dashboard/login", status_code=302)


def _check_auth(request: Request):
    return get_current_user(request)


# ── AUTH ──────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if check_credentials(username, password):
        token = create_session_token(username)
        response = RedirectResponse(url="/dashboard/leads", status_code=302)
        response.set_cookie(COOKIE_NAME, token, max_age=60*60*8, httponly=True)
        return response
    return RedirectResponse(url="/dashboard/login?error=Invalid+credentials", status_code=302)


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/dashboard/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


# ── LEADS BOARD ───────────────────────────────────────────────

@router.get("/leads", response_class=HTMLResponse)
async def leads_board(request: Request, status: str = "", search: str = ""):
    if not _check_auth(request):
        return _redirect_login()

    async with async_session() as session:
        # Count by status
        counts = {}
        for s in STATUSES:
            r = await session.execute(select(func.count()).where(Lead.status == s))
            counts[s] = r.scalar()

        # Filter leads
        q = select(Lead).order_by(desc(Lead.last_seen_at))
        if status:
            q = q.where(Lead.status == status)
        if search:
            q = q.where(
                Lead.phone.ilike(f"%{search}%") |
                Lead.name.ilike(f"%{search}%") |
                Lead.company.ilike(f"%{search}%")
            )
        result = await session.execute(q)
        leads = result.scalars().all()

    return templates.TemplateResponse("leads.html", {
        "request": request,
        "leads": leads,
        "counts": counts,
        "statuses": STATUSES,
        "status_labels": STATUS_LABELS,
        "status_colors": STATUS_COLORS,
        "active_status": status,
        "search": search,
    })


# ── LEAD DETAIL ───────────────────────────────────────────────

@router.get("/leads/{phone}", response_class=HTMLResponse)
async def lead_detail(request: Request, phone: str):
    if not _check_auth(request):
        return _redirect_login()

    phone = phone.replace("-", "@")  # URL-safe phone encoding
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.phone == phone))
        lead = result.scalar_one_or_none()
        if not lead:
            return HTMLResponse("Lead not found", status_code=404)

        msgs_result = await session.execute(
            select(Message).where(Message.phone == phone).order_by(Message.created_at)
        )
        messages = msgs_result.scalars().all()

        events_result = await session.execute(
            select(FunnelEvent).where(FunnelEvent.phone == phone).order_by(FunnelEvent.created_at)
        )
        events = events_result.scalars().all()

    return templates.TemplateResponse("lead_detail.html", {
        "request": request,
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
        msgs_result = await session.execute(
            select(Message).where(Message.phone == phone).order_by(Message.created_at)
        )
        messages = msgs_result.scalars().all()

    if messages:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())
        conversation = "\n".join(f"{m.role.upper()}: {m.content}" for m in messages[-20:])
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"Summarize this WhatsApp sales conversation in 2-3 sentences. Focus on: who they are, what they need, and next step.\n\n{conversation}"
            }],
            max_tokens=200,
        )
        summary = response.choices[0].message.content
        from agent.leads import update_lead_field
        await update_lead_field(phone, ai_summary=summary)

    return RedirectResponse(url=f"/dashboard/leads/{phone.replace('@', '-')}", status_code=302)


# ── MONITOR ───────────────────────────────────────────────────

@router.get("/monitor", response_class=HTMLResponse)
async def monitor(request: Request):
    if not _check_auth(request):
        return _redirect_login()

    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    async with async_session() as session:
        # Chat counts
        def count_q(since): return select(func.count()).where(
            UsageLog.event_type.in_(["chat", "chat_tool_followup"]),
            UsageLog.created_at >= since
        )
        chats_today = (await session.execute(count_q(today))).scalar()
        chats_week  = (await session.execute(count_q(week_ago))).scalar()
        chats_month = (await session.execute(count_q(month_ago))).scalar()

        # Token totals
        tok_result = await session.execute(
            select(func.sum(UsageLog.tokens_in), func.sum(UsageLog.tokens_out))
            .where(UsageLog.created_at >= today)
        )
        tok_row = tok_result.one()
        tokens_in_today  = tok_row[0] or 0
        tokens_out_today = tok_row[1] or 0

        # Cost totals
        def cost_q(since): return select(func.sum(UsageLog.cost_usd)).where(
            UsageLog.created_at >= since
        )
        cost_today = float((await session.execute(cost_q(today))).scalar() or 0)
        cost_week  = float((await session.execute(cost_q(week_ago))).scalar() or 0)
        cost_month = float((await session.execute(cost_q(month_ago))).scalar() or 0)

        # Error count today
        errors_today = (await session.execute(
            select(func.count()).where(UsageLog.success == False, UsageLog.created_at >= today)
        )).scalar()

        # Lead counts
        total_leads = (await session.execute(select(func.count(Lead.id)))).scalar()
        new_leads_today = (await session.execute(
            select(func.count()).where(Lead.created_at >= today)
        )).scalar()

        # Recent errors
        recent_errors = (await session.execute(
            select(UsageLog)
            .where(UsageLog.success == False)
            .order_by(desc(UsageLog.created_at))
            .limit(10)
        )).scalars().all()

        # Avg latency today
        avg_lat = (await session.execute(
            select(func.avg(UsageLog.latency_ms)).where(
                UsageLog.created_at >= today, UsageLog.success == True
            )
        )).scalar()

    return templates.TemplateResponse("monitor.html", {
        "request": request,
        "chats_today": chats_today,
        "chats_week": chats_week,
        "chats_month": chats_month,
        "tokens_in_today": tokens_in_today,
        "tokens_out_today": tokens_out_today,
        "cost_today": round(cost_today, 4),
        "cost_week": round(cost_week, 4),
        "cost_month": round(cost_month, 4),
        "errors_today": errors_today,
        "total_leads": total_leads,
        "new_leads_today": new_leads_today,
        "recent_errors": recent_errors,
        "avg_latency_ms": int(avg_lat or 0),
    })
