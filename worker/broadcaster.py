import asyncio
from database import repo
from worker import api
import config


async def run_broadcast(text: str, progress_cb=None) -> dict:
    """Sends `text` through every connected bot to every user that bot has ever
    messaged. Goes bot-by-bot, user-by-user, with a small delay - never fires
    everything at once, so no single bot (or the whole app) gets flood-limited."""
    bots = await repo.get_connected_bots()
    total_sent, total_failed = 0, 0

    for i, bot in enumerate(bots, start=1):
        token = bot["token"]
        users = await repo.get_bot_users(token)

        for user_id in users:
            ok = await api.send_message(token, user_id, text)
            if ok:
                total_sent += 1
            else:
                total_failed += 1
            await asyncio.sleep(config.BROADCAST_DELAY)

        if progress_cb:
            await progress_cb(i, len(bots), total_sent, total_failed)

    return {"bots": len(bots), "sent": total_sent, "failed": total_failed}
