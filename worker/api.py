import aiohttp
import config

_session: aiohttp.ClientSession | None = None


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def close_session():
    global _session
    if _session and not _session.closed:
        await _session.close()


async def call(token: str, method: str, payload: dict | None = None) -> dict:
    url = config.TELEGRAM_API.format(token=token, method=method)
    session = await get_session()
    async with session.post(url, json=payload or {}, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        return await resp.json()


async def get_me(token: str) -> dict | None:
    data = await call(token, "getMe")
    return data.get("result") if data.get("ok") else None


async def set_webhook(token: str) -> bool:
    url = f"{config.APP_URL}/webhook/{token}"
    data = await call(token, "setWebhook", {"url": url})
    return bool(data.get("ok"))


async def send_message(token: str, chat_id: int, text: str) -> bool:
    data = await call(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "link_preview_options": {"is_disabled": True},
    })
    return bool(data.get("ok"))
