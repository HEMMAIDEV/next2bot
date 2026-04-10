# agent/migrations.py — Safe schema migration runner
"""
Adds new columns to existing tables without dropping data.
Each migration runs in its OWN isolated transaction so one failure
never blocks the others (critical for PostgreSQL with asyncpg).

PostgreSQL-compatible: uses FALSE/TRUE for boolean defaults (not 0/1).
"""
import logging
import sqlalchemy
from agent.database import engine

logger = logging.getLogger("agentkit")

# Each tuple is (sql, description)
# Every statement is executed in its own transaction — fully isolated.
MIGRATIONS = [
    # Phase 7 — Plans, Alerts, Partner bots, API balance tracking

    # Client table — usage limits
    ("ALTER TABLE clients ADD COLUMN plan_id INTEGER",
     "clients.plan_id"),
    ("ALTER TABLE clients ADD COLUMN msg_limit INTEGER",
     "clients.msg_limit"),
    ("ALTER TABLE clients ADD COLUMN cost_limit_usd REAL DEFAULT 0",
     "clients.cost_limit_usd"),
    ("ALTER TABLE clients ADD COLUMN alert_threshold_pct INTEGER DEFAULT 80",
     "clients.alert_threshold_pct"),

    # Client table — partner bot support (use FALSE not 0 for PostgreSQL booleans)
    ("ALTER TABLE clients ADD COLUMN is_partner_bot BOOLEAN DEFAULT FALSE",
     "clients.is_partner_bot"),
    ("ALTER TABLE clients ADD COLUMN partner_name VARCHAR(200)",
     "clients.partner_name"),
    ("ALTER TABLE clients ADD COLUMN partner_monthly_cost_mxn REAL DEFAULT 0",
     "clients.partner_monthly_cost_mxn"),
    ("ALTER TABLE clients ADD COLUMN partner_api_excluded BOOLEAN DEFAULT FALSE",
     "clients.partner_api_excluded"),

    # ServiceBilling table — balance & alert thresholds
    ("ALTER TABLE service_billing ADD COLUMN alert_threshold_usd REAL DEFAULT 0",
     "service_billing.alert_threshold_usd"),
    ("ALTER TABLE service_billing ADD COLUMN balance_usd REAL",
     "service_billing.balance_usd"),
    ("ALTER TABLE service_billing ADD COLUMN balance_alert_threshold_usd REAL DEFAULT 5",
     "service_billing.balance_alert_threshold_usd"),
]


async def run_migrations() -> None:
    """
    Run each migration in its own isolated transaction.
    Safe to call repeatedly — skips already-applied columns silently.
    """
    applied = 0
    skipped = 0
    failed = 0

    for sql, label in MIGRATIONS:
        # Each statement gets its own begin/commit so a failure here
        # never rolls back or blocks the next statement.
        try:
            async with engine.begin() as conn:
                await conn.execute(sqlalchemy.text(sql))
            applied += 1
            logger.debug(f"Migration applied: {label}")
        except Exception as e:
            err = str(e).lower()
            if (
                "duplicate column" in err          # PostgreSQL
                or "already exists" in err          # SQLite / generic
                or "column" in err and "already" in err
            ):
                skipped += 1  # Already applied — safe to ignore
            else:
                failed += 1
                logger.warning(f"Migration warning [{label}]: {e}")

    logger.info(
        f"Migrations complete — applied: {applied}, skipped (already exist): {skipped}, warnings: {failed}"
    )
