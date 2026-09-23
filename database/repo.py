import time
from database.mongo import bots_col, users_col, messages_col, settings_col

DEFAULT_REPLY = "Hello! This is a placeholder reply. Please join our channel."


# ---------- bots ----------

async def bot_exists(token: str) -> bool:
    return await bots_col.find_one({"token": token}) is not None


async def add_bot(token: str, owner_id: int, username: str, bot_name: str):
    await bots_col.update_one(
        {"token": token},
        {"$set": {
            "token": token,
            "owner_id": owner_id,
            "username": username,
            "bot_name": bot_name,
            "status": "connected",
            "connected_at": time.time(),
        }},
        upsert=True,
    )


async def mark_disconnected(token: str):
    await bots_col.update_one({"token": token}, {"$set": {"status": "disconnected"}})


async def mark_connected(token: str):
    await bots_col.update_one({"token": token}, {"$set": {"status": "connected"}})


async def get_bot(token: str):
    return await bots_col.find_one({"token": token})


async def count_bots(status: str | None = None) -> int:
    query = {"status": status} if status else {}
    return await bots_col.count_documents(query)


async def get_connected_bots() -> list:
    return [b async for b in bots_col.find({"status": "connected"})]


# ---------- global reply text (editable, shared by every worker bot) ----------

async def get_global_reply() -> str:
    doc = await settings_col.find_one({"_id": "global_reply"})
    return doc["text"] if doc else DEFAULT_REPLY


async def set_global_reply(text: str):
    await settings_col.update_one(
        {"_id": "global_reply"}, {"$set": {"text": text}}, upsert=True
    )


# ---------- per-bot users (needed for broadcast) ----------

async def log_user(bot_token: str, user_id: int):
    await users_col.update_one(
        {"bot_token": bot_token, "user_id": user_id},
        {"$setOnInsert": {"first_seen": time.time()}},
        upsert=True,
    )


async def get_bot_users(bot_token: str) -> list:
    return [u["user_id"] async for u in users_col.find({"bot_token": bot_token}, {"user_id": 1})]


# ---------- message stats ----------

async def log_message(bot_token: str):
    await messages_col.insert_one({"bot_token": bot_token, "ts": time.time()})


async def top_bots_last_hours(hours: int = 24, limit: int = 5) -> list:
    since = time.time() - hours * 3600
    pipeline = [
        {"$match": {"ts": {"$gte": since}}},
        {"$group": {"_id": "$bot_token", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    results = [doc async for doc in messages_col.aggregate(pipeline)]
    out = []
    for r in results:
        bot = await get_bot(r["_id"])
        out.append({
            "username": bot["username"] if bot else "unknown",
            "count": r["count"],
        })
    return out
