import asyncio
import logging

from database import repo
from worker import api

log = logging.getLogger("healthcheck")
_running = False


def is_running() -> bool:
    return _running


async def check_all() -> dict:
    """getMe on every 'connected' bot. A revoked/deleted token answers 401 -> we mark
    the bot disconnected. Network errors / rate limits are NOT treated as revoked."""
    global _running
    _running = True
    res = {"checked": 0, "ok": 0, "revoked": 0, "transient": 0}
    try:
        bots = await repo.get_connected_bots()
        sem = asyncio.Semaphore(10)

        async def one(b):
            try:
                async with sem:
                    status, _ = await api.check_token(b["token"])
                if status == api.OK:
                    res["ok"] += 1
                elif status == api.REVOKED:
                    await repo.mark_disconnected(b["token"], "token revoked/deleted (health check)")
                    res["revoked"] += 1
                else:
                    res["transient"] += 1
            except Exception:
                log.exception("health check failed for a bot")
                res["transient"] += 1
            res["checked"] += 1

        await asyncio.gather(*(one(b) for b in bots))
    finally:
        _running = False
    return res


async def resync_webhooks() -> dict:
    """Re-sets every connected bot's webhook to the CURRENT config.APP_URL.
    Needed after the Heroku app's URL changes (rename / new app) - a bot's
    webhook is only set at /addbot time, so it keeps pointing at the old,
    now-dead URL until this is run, and silently gets zero updates."""
    global _running
    _running = True
    res = {"checked": 0, "ok": 0, "revoked": 0, "failed": 0}
    try:
        bots = await repo.get_connected_bots()
        sem = asyncio.Semaphore(15)

        async def one(b):
            try:
                async with sem:
                    status, desc = await api.set_webhook_status(b["token"])
                if status == api.OK:
                    res["ok"] += 1
                elif status == api.REVOKED:
                    await repo.mark_disconnected(b["token"], "token revoked/deleted (webhook resync)")
                    res["revoked"] += 1
                else:
                    res["failed"] += 1
                    log.warning("webhook resync failed for @%s: %s", b.get("username"), desc)
            except Exception:
                log.exception("webhook resync failed for a bot")
                res["failed"] += 1
            res["checked"] += 1

        await asyncio.gather(*(one(b) for b in bots))
    finally:
        _running = False
    return res


async def loop(interval_hours: float):
    """Background job: periodic revoked-token detection + old-stats cleanup."""
    await asyncio.sleep(60)  # let the app finish starting
    while True:
        try:
            res = await check_all()
            purged = await repo.purge_old_messages(7)
            log.info("health check %s, purged %s old message rows", res, purged)
        except Exception:
            log.exception("health loop error")
        await asyncio.sleep(interval_hours * 3600)
