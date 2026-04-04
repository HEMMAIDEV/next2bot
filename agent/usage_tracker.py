# agent/usage_tracker.py — Log every API call for cost monitoring
import time
import logging
from datetime import datetime
from agent.database import async_session
from agent.models import UsageLog

logger = logging.getLogger("agentkit")

# gpt-4o-mini pricing per token
COST_PER_INPUT_TOKEN  = 0.00000015
COST_PER_OUTPUT_TOKEN = 0.0000006


async def log_usage(
    provider: str,
    event_type: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int = 0,
    success: bool = True,
    error: str | None = None,
    phone: str | None = None,
):
    cost = (tokens_in * COST_PER_INPUT_TOKEN) + (tokens_out * COST_PER_OUTPUT_TOKEN)
    async with async_session() as session:
        log = UsageLog(
            provider=provider,
            event_type=event_type,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            latency_ms=latency_ms,
            success=success,
            error_message=error,
            phone=phone,
            created_at=datetime.utcnow(),
        )
        session.add(log)
        await session.commit()
