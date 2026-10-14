import asyncio
import logging
import os
import time

import config
from database import repo
from utils.tasks import spawn
from worker import api

log = logging.getLogger("broadcast")

CONCURRENCY = int(os.getenv("BROADCAST_CONCURRENCY", "8"))  # bots sending at the same time
PROGRESS_EVERY = 5   # seconds between status-message edits / heartbeats
SAVE_EVERY = 25      # users between progress saves (= max duplicates after a crash)

_stop: set = set()


def summary(t: dict) -> str:
    s = (f"🤖 Bots done: {t['bots_finished']}/{t['bots_total']}\n"
         f"✅ Delivered: {t['sent']}\n"
         f"🚫 Blocked / left: {t['blocked']}\n"
         f"⚠️ Failed: {t['failed']}\n"
         f"💀 Revoked bots (auto-disconnected): {t['revoked']}")
    if t["errors"]:
        s += f"\n❗ Bots with internal errors: {t['errors']}"
    return s


async def start(text: str, aiobot, chat_id: int, message_id: int):
    job_id = await repo.create_job(text, chat_id, message_id)
    spawn(run_job(job_id, aiobot))
    return job_id


async def stop(job_id):
    _stop.add(str(job_id))
    await repo.set_job_status(job_id, "cancelled")


async def _send_to_bot(job: dict, bj: dict, sem: asyncio.Semaphore):
    """Sends the job's text to every reachable user of one bot, one by one.
    Never raises (except cancellation) so one bad bot can't kill the broadcast."""
    job_id, token, key = job["_id"], bj["bot_token"], str(job["_id"])
    cursor = bj.get("cursor", repo.CURSOR_START)
    sent, blocked, failed = bj.get("sent", 0), bj.get("blocked", 0), bj.get("failed", 0)
    final = "running"  # stays "running" if we're cancelled -> resumed after restart
    async with sem:
        try:
            unsaved = 0
            while final == "running":
                users = await repo.next_users(token, cursor)
                if not users:
                    final = "done"
                    break
                for uid in users:
                    if key in _stop:
                        final = "stopped"
                        break
                    result = await api.send_message(token, uid, job["text"])
                    if result == api.REVOKED:
                        await repo.mark_disconnected(token, "token revoked (found during broadcast)")
                        final = "revoked"
                        break
                    cursor = uid
                    if result == api.OK:
                        sent += 1
                    elif result == api.BLOCKED:
                        blocked += 1
                        await repo.mark_user_blocked(token, uid)
                    else:
                        failed += 1
                    unsaved += 1
                    if unsaved >= SAVE_EVERY:
                        await repo.save_bot_progress(job_id, token, cursor, sent, blocked, failed, "running")
                        unsaved = 0
                    await asyncio.sleep(config.BROADCAST_DELAY)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("broadcast: bot %s crashed", bj.get("username"))
            final = "error"
        finally:
            await repo.save_bot_progress(job_id, token, cursor, sent, blocked, failed, final)


async def _reporter(job: dict, aiobot):
    """Keeps the heartbeat alive and edits the status message every few seconds."""
    last = ""
    while True:
        await asyncio.sleep(PROGRESS_EVERY)
        try:
            await repo.heartbeat(job["_id"])
            t = await repo.job_totals(job["_id"])
            pct = int(t["bots_finished"] / t["bots_total"] * 100) if t["bots_total"] else 100
            text = f"📢 Broadcasting... {pct}%\n\n{summary(t)}\n\n/stopbroadcast to stop"
            if text != last:
                await aiobot.edit_message_text(text, chat_id=job["chat_id"], message_id=job["message_id"])
                last = text
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # status edits are cosmetic - never let them break the broadcast


async def run_job(job_id, aiobot):
    job = await repo.get_job(job_id)
    if not job or job.get("status") != "running":
        return
    sem = asyncio.Semaphore(CONCURRENCY)
    reporter = spawn(_reporter(job, aiobot))
    try:
        todo = await repo.pending_bot_jobs(job_id)
        await asyncio.gather(*(_send_to_bot(job, bj, sem) for bj in todo))
    except asyncio.CancelledError:   # dyno shutting down: progress is saved, job stays "running"
        reporter.cancel()
        raise
    reporter.cancel()
    await _finish(job, aiobot)


async def _finish(job: dict, aiobot):
    key = str(job["_id"])
    stopped = key in _stop
    _stop.discard(key)
    t = await repo.job_totals(job["_id"])
    await repo.set_job_status(job["_id"], "cancelled" if stopped else "done")
    head = "🛑 Broadcast stopped." if stopped else "✅ Broadcast finished."
    try:
        await aiobot.edit_message_text(f"{head}\n\n{summary(t)}",
                                       chat_id=job["chat_id"], message_id=job["message_id"])
    except Exception:
        pass
    try:  # separate message so the owner gets a notification
        await aiobot.send_message(job["chat_id"], f"{head} Bot-wise CSV: /lastreport")
    except Exception:
        pass


async def resume_if_needed(aiobot):
    """Called on startup: a broadcast left 'running' means the dyno restarted mid-way."""
    job = await repo.get_running_job()
    while job and time.time() - job.get("heartbeat", 0) < 40:
        # another process (old dyno during a deploy) may still be sending - wait for it to go quiet
        await asyncio.sleep(5)
        job = await repo.get_job(job["_id"])
        if not job or job.get("status") != "running":
            return
    if not job:
        return
    try:
        await aiobot.send_message(job["chat_id"], "♻️ Server restarted - resuming the broadcast where it stopped.")
    except Exception:
        pass
    spawn(run_job(job["_id"], aiobot))
