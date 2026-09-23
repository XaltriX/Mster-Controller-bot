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
        return web.Response(status=200)  # not a text/command update we care about

    chat_id = message["chat"]["id"]

    # Track this user against this bot (needed for broadcast) + stats
    await repo.log_user(token, chat_id)
    await repo.log_message(token)

    reply_text = await repo.get_global_reply()
    ok = await api.send_message(token, chat_id, reply_text)

    if not ok:
        # Sending failed -> token is very likely revoked/blocked, mark disconnected.
        # (A single transient failure won't usually reach here since aiohttp
        # already got a response from Telegram; genuine network blips just retry
        # on the next incoming message.)
        me = await api.get_me(token)
        if me is None:
            await repo.mark_disconnected(token)

    return web.Response(status=200)
