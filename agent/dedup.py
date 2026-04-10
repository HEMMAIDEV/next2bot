# agent/dedup.py — In-process message deduplication
"""
Prevents the bot from processing the same WhatsApp message more than once.

Why this is needed:
  Whapi (and other webhook providers) sometimes fire the same webhook payload
  multiple times from different IPs within a few seconds (retry/load-balancer
  behaviour). Without dedup every delivery triggers a fresh OpenAI call and a
  new WhatsApp reply — the user receives duplicate responses.

How it works:
  We keep an ordered dict of {message_id → arrival_time}.  When a message
  arrives we check if its ID is already in the dict; if so we return True
  (duplicate — skip processing) and log a warning.  Entries older than
  DEDUP_TTL_SECONDS are evicted before each check so memory stays bounded.

Thread / concurrency safety:
  FastAPI runs on a single asyncio event loop (one Python thread) so the plain
  dict is safe without locks.  If you ever switch to multiple workers add a
  shared Redis/Postgres dedup store.
"""

import time
import logging
from collections import OrderedDict

logger = logging.getLogger("agentkit")

# ── Configuration ─────────────────────────────────────────────────────────────

# How long to remember a message ID (seconds).
# Whapi retries typically arrive within 2-10 s; 120 s gives ample margin.
DEDUP_TTL_SECONDS: int = 120

# Safety cap: evict oldest entries if the dict grows beyond this many entries
# (handles pathological bursts without unbounded memory growth).
DEDUP_MAX_SIZE: int = 2_000

# ── Internal state ─────────────────────────────────────────────────────────────

# OrderedDict preserves insertion order so we can evict from the front cheaply.
_seen: OrderedDict[str, float] = OrderedDict()


# ── Public API ─────────────────────────────────────────────────────────────────

def is_duplicate(message_id: str) -> bool:
    """
    Returns True if *message_id* was already processed within the TTL window.
    Returns False (and registers the ID) if this is the first time we see it.

    Side-effects:
      - Evicts expired entries before checking.
      - Evicts the oldest entries if the dict exceeds DEDUP_MAX_SIZE.
      - On first sight: adds the ID to the dict and returns False.
      - On duplicate:  logs a warning and returns True (caller should skip).
    """
    if not message_id:
        # No ID available (provider bug?) — allow through rather than block.
        return False

    now = time.monotonic()

    # ── Evict expired entries (front of the ordered dict) ──────────────────
    cutoff = now - DEDUP_TTL_SECONDS
    while _seen:
        oldest_id, oldest_ts = next(iter(_seen.items()))
        if oldest_ts < cutoff:
            _seen.popitem(last=False)
        else:
            break  # remaining entries are newer — stop

    # ── Enforce max-size cap ───────────────────────────────────────────────
    while len(_seen) >= DEDUP_MAX_SIZE:
        _seen.popitem(last=False)

    # ── Check for duplicate ────────────────────────────────────────────────
    if message_id in _seen:
        logger.warning(
            f"Duplicate webhook ignored — message_id={message_id[:30]} "
            f"(age {now - _seen[message_id]:.1f}s)"
        )
        return True  # caller must skip this message

    # First sight — register and allow through
    _seen[message_id] = now
    return False


def seen_count() -> int:
    """Returns the current number of tracked message IDs (for debugging)."""
    return len(_seen)
