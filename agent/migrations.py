# agent/migrations.py — Safe schema migration runner
"""
Adds new columns to existing tables without dropping data.
Each migration is wrapped in try/except so re-running is always safe.
Works with both SQLite (dev) and PostgreSQL (Railway prod).
"""
import logging
from agent.database import engine

logger = logging.getLogger("agentkit")

# Each migration is a list of ALTER TABLE statements to attempt
MIGRATIONS = [
    # Phase 7 — Plans, Alerts, Partner bots, API balance tracking
    [
        # Client — usage limits & partner support
        "ALTER TABLE clients ADD COLUMN plan_id INTEGER",
        "ALTER TABLE clients ADD COLUMN msg_limit INTEGER",
        "ALTER TABLE clients ADD COLUMN cost_limit_usd REAL DEFAULT 0",
        "ALTER TABLE clients ADD COLUMN is_partner_bot BOOLEAN DEFAULT 0",
        "ALTER TABLE clients ADD COLUMN partner_name VARCHAR(200)",
        "ALTER TABLE clients ADD COLUMN partner_monthly_cost_mxn REAL DEFAULT 0",
        "ALTER TABLE clients ADD COLUMN partner_api_excluded BOOLEAN DEFAULT 0",
        "ALTER TABLE clients ADD COLUMN alert_threshold_pct INTEGER DEFAULT 80",
        # ServiceBilling — balance & alert thresholds
        "ALTER TABLE service_billing ADD COLUMN alert_threshold_usd REAL DEFAULT 0",
        "ALTER TABLE service_billing ADD COLUMN balance_usd REAL",
        "ALTER TABLE service_billing ADD COLUMN balance_alert_threshold_usd REAL DEFAULT 5",
    ]
]


async def run_migrations() -> None:
    """Run all pending migrations safely (idempotent)."""
    async with engine.begin() as conn:
        for batch in MIGRATIONS:
            for sql in batch:
                try:
                    await conn.execute(__import__("sqlalchemy").text(sql))
                    logger.debug(f"Migration OK: {sql[:60]}")
                except Exception as e:
                    err = str(e).lower()
                    # "duplicate column" on PostgreSQL / "already exists" on SQLite
                    if "duplicate column" in err or "already exists" in err:
                        pass  # Already applied — safe to skip
                    else:
                        logger.warning(f"Migration warning [{sql[:40]}]: {e}")
    logger.info("Migrations complete")
