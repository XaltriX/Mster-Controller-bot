import asyncio
import aiohttp
import config

# Result of a send attempt
OK = "ok"
BLOCKED = "blocked"    # user blocked the bot / left / chat gone - this user only
REVOKED = "revoked"    # the bot token itself is dead (revoked or bot deleted)
FLOOD = "flood"        # Telegram rate limit and we gave up waiting
ERROR = "error"        # network problem / Telegram 5xx / anything else

_session: aiohttp.ClientSession | None = None


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=100, ttl_dns_cache=300),
            timeout=aiohttp.ClientTimeout(total=20),
        )
    return _session


async def close_session():
    global _session
    if _session and not _session.closed:
        await _session.close()


async def call(token: str, method: str, payload: dict | None = None) -> dict:
    """Never raises. Returns Telegram's JSON, or a synthetic error dict
    (error_code 0) when the network call itself failed."""
    url = config.TELEGRAM_API.format(token=token, method=method)
    try:
        session = await get_session()
        async with session.post(url, json=payload or {}) as resp:
            try:
                return await resp.json(content_type=None)
            except Exception:
                return {"ok": False, "error_code": resp.status, "description": "bad response"}
    except Exception as e:  # timeouts, connection resets, DNS...  (not CancelledError)
        return {"ok": False, "error_code": 0, "description": f"network: {type(e).__name__}"}


def classify(data: dict) -> tuple[str, int]:
    """-> (status, retry_after_seconds)"""
    if data.get("ok"):
        return OK, 0
    code = data.get("error_code", 0)
    desc = (data.get("description") or "").lower()
    if code == 429:
        return FLOOD, int((data.get("parameters") or {}).get("retry_after", 5))
    if code in (401, 404):
        return REVOKED, 0
    if code == 403 or (code == 400 and ("chat not found" in desc or "user not found" in desc)):
        return BLOCKED, 0
    return ERROR, 0


async def check_token(token: str) -> tuple[str, dict | None]:
    """getMe -> (OK|REVOKED|FLOOD|ERROR, bot_info_or_None)"""
    data = await call(token, "getMe")
    status, _ = classify(data)
    return status, (data.get("result") if status == OK else None)


async def get_me(token: str) -> dict | None:
    _, me = await check_token(token)
    return me


async def set_webhook(token: str) -> bool:
    url = f"{config.APP_URL}/webhook/{token}"
    data = await call(token, "setWebhook", {"url": url})
    return bool(data.get("ok"))


async def send_message(token: str, chat_id: int, text: str,
                       max_retries: int = 3, max_wait: int = 30) -> str:
    """Returns one of OK / BLOCKED / REVOKED / FLOOD / ERROR.
    Waits out Telegram's 429 "retry after" and retries network blips."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "link_preview_options": {"is_disabled": True},
    }
    for attempt in range(max_retries + 1):
        data = await call(token, "sendMessage", payload)
        status, wait = classify(data)
        if status == FLOOD:
            if attempt == max_retries or wait > max_wait:
                return FLOOD
            await asyncio.sleep(wait + 1)
            continue
        if status == ERROR and attempt < max_retries:
            code = data.get("error_code", 0)
            if code == 0 or code >= 500:  # transient only; a real 400 won't fix itself
                await asyncio.sleep(1 + attempt)
                continue
        return status
    return ERROR
