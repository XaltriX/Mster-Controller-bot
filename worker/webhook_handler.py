import asyncio
from aiohttp import web
from database import repo
from worker import api


async def handle_worker_webhook(request: web.Request) -> web.Response:
    token = request.match_info["token"]

    # Unknown/disconnected token -> tell Telegram to stop bothering us
    bot = await repo.get_bot(token)
    if not bot or bot.get("status") != "connected":
        return web.Response(status=403)

    try:
        update = await request.json()
    except Exception:
        return web.Response(status=200)  # ignore malformed payloads

    message = update.get("message") or update.get("channel_post")
    if not message:
        return web.Response(status=200)

    chat_id = message["chat"]["id"]

    # Track the user (needed for broadcast) + stats, concurrently
    await asyncio.gather(repo.log_user(token, chat_id), repo.log_message(token))

    reply_text = await repo.get_global_reply()
    # Short retry budget: this runs inside Heroku's 30s request window
    result = await api.send_message(token, chat_id, reply_text, max_retries=1, max_wait=5)

    if result == api.REVOKED:
        await repo.mark_disconnected(token, "token revoked (found on reply)")

    return web.Response(status=200)
