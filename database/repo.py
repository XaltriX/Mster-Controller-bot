import time
from database.mongo import (
    bots_col, users_col, messages_col, settings_col, broadcasts_col, bcast_bots_col,
)

DEFAULT_REPLY = "Hello! This is a placeholder reply. Please join our channel."
CURSOR_START = -(2 ** 62)  # below any chat id (group/channel ids are negative)
_ACTIVE = {"blocked": {"$ne": True}}

_reply_cache = {"text": None, "at": 0.0}


# ---------------- bots ----------------

async def bot_exists(token: str) -> bool:
    return await bots_col.find_one({"token": token}, {"_id": 1}) is not None


async def add_bot(token: str, owner_id: int, username: str, bot_name: str):
    await bots_col.update_one(
        {"token": token},
        {"$set": {
            "token": token, "owner_id": owner_id, "username": username,
            "bot_name": bot_name, "status": "connected", "connected_at": time.time(),
        }, "$unset": {"disconnected_at": "", "disconnect_reason": ""}},
        upsert=True,
    )


async def mark_disconnected(token: str, reason: str = ""):
    await bots_col.update_one(
        {"token": token},
        {"$set": {"status": "disconnected", "disconnected_at": time.time(),
                  "disconnect_reason": reason}},
    )


async def get_bot(token: str):
    return await bots_col.find_one({"token": token})


async def count_bots(status: str | None = None) -> int:
    return await bots_col.count_documents({"status": status} if status else {})


async def get_connected_bots() -> list:
    return [b async for b in bots_col.find({"status": "connected"})]


async def get_all_bots() -> list:
    return [b async for b in bots_col.find({})]


# ---------------- global reply (shared by every worker bot) ----------------

async def get_global_reply() -> str:
    now = time.time()
    if _reply_cache["text"] is not None and now - _reply_cache["at"] < 10:
        return _reply_cache["text"]
    doc = await settings_col.find_one({"_id": "global_reply"})
    text = doc["text"] if doc else DEFAULT_REPLY
    _reply_cache.update(text=text, at=now)
    return text


async def set_global_reply(text: str):
    await settings_col.update_one({"_id": "global_reply"}, {"$set": {"text": text}}, upsert=True)
    _reply_cache.update(text=text, at=time.time())


# ---------------- users ----------------

async def log_user(bot_token: str, user_id: int):
    now = time.time()
    await users_col.update_one(
        {"bot_token": bot_token, "user_id": user_id},
        {"$set": {"blocked": False, "last_seen": now}, "$setOnInsert": {"first_seen": now}},
        upsert=True,
    )


async def mark_user_blocked(bot_token: str, user_id: int):
    await users_col.update_one(
        {"bot_token": bot_token, "user_id": user_id}, {"$set": {"blocked": True}}
    )


async def next_users(bot_token: str, after: int, limit: int = 200) -> list:
    """Next batch of reachable users of a bot, ordered by user_id, after a cursor."""
    cur = (users_col.find({"bot_token": bot_token, "user_id": {"$gt": after}, **_ACTIVE},
                          {"user_id": 1})
           .sort("user_id", 1).limit(limit))
    return [d["user_id"] async for d in cur]


async def count_users() -> int:
    return await users_col.count_documents(_ACTIVE)


async def count_blocked_users() -> int:
    return await users_col.count_documents({"blocked": True})


async def count_unique_users() -> int:
    rows = [d async for d in users_col.aggregate(
        [{"$match": _ACTIVE}, {"$group": {"_id": "$user_id"}}, {"$count": "n"}])]
    return rows[0]["n"] if rows else 0


async def bot_user_count(token: str) -> int:
    return await users_col.count_documents({"bot_token": token, **_ACTIVE})


async def users_per_bot() -> dict:
    out = {}
    pipeline = [{"$group": {
        "_id": "$bot_token", "total": {"$sum": 1},
        "blocked": {"$sum": {"$cond": [{"$eq": ["$blocked", True]}, 1, 0]}},
    }}]
    async for d in users_col.aggregate(pipeline):
        out[d["_id"]] = {"active": d["total"] - d["blocked"], "blocked": d["blocked"]}
    return out


# ---------------- message stats ----------------

async def log_message(bot_token: str):
    await messages_col.insert_one({"bot_token": bot_token, "ts": time.time()})


async def messages_since(hours: int = 24) -> int:
    return await messages_col.count_documents({"ts": {"$gte": time.time() - hours * 3600}})


async def messages_per_bot(hours: int = 24) -> dict:
    pipeline = [{"$match": {"ts": {"$gte": time.time() - hours * 3600}}},
                {"$group": {"_id": "$bot_token", "n": {"$sum": 1}}}]
    return {d["_id"]: d["n"] async for d in messages_col.aggregate(pipeline)}


async def _usernames(tokens) -> dict:
    cur = bots_col.find({"token": {"$in": list(tokens)}}, {"token": 1, "username": 1})
    return {b["token"]: b.get("username", "") async for b in cur}


async def top_bots_by_messages(hours: int = 24, limit: int = 5) -> list:
    pipeline = [{"$match": {"ts": {"$gte": time.time() - hours * 3600}}},
                {"$group": {"_id": "$bot_token", "n": {"$sum": 1}}},
                {"$sort": {"n": -1}}, {"$limit": limit}]
    rows = [d async for d in messages_col.aggregate(pipeline)]
    names = await _usernames([r["_id"] for r in rows])
    return [{"username": names.get(r["_id"], "unknown"), "messages": r["n"],
             "users": await bot_user_count(r["_id"])} for r in rows]


async def top_bots_by_users(limit: int = 5) -> list:
    pipeline = [{"$match": _ACTIVE}, {"$group": {"_id": "$bot_token", "n": {"$sum": 1}}},
                {"$sort": {"n": -1}}, {"$limit": limit}]
    rows = [d async for d in users_col.aggregate(pipeline)]
    names = await _usernames([r["_id"] for r in rows])
    return [{"username": names.get(r["_id"], "unknown"), "users": r["n"]} for r in rows]


async def purge_old_messages(days: int = 7) -> int:
    r = await messages_col.delete_many({"ts": {"$lt": time.time() - days * 86400}})
    return r.deleted_count


# ---------------- broadcast jobs (persisted so a Heroku restart can resume) ----------------

async def create_job(text: str, chat_id: int, message_id: int):
    now = time.time()
    job_id = (await broadcasts_col.insert_one({
        "text": text, "status": "running", "created_at": now, "heartbeat": now,
        "chat_id": chat_id, "message_id": message_id,
    })).inserted_id
    bots = await get_connected_bots()
    if bots:
        await bcast_bots_col.insert_many([{
            "job_id": job_id, "bot_token": b["token"], "username": b.get("username", ""),
            "status": "pending", "cursor": CURSOR_START, "sent": 0, "blocked": 0, "failed": 0,
        } for b in bots])
    return job_id


async def get_job(job_id):
    return await broadcasts_col.find_one({"_id": job_id})


async def get_running_job():
    return await broadcasts_col.find_one({"status": "running"})


async def get_last_job():
    return await broadcasts_col.find_one({}, sort=[("created_at", -1)])


async def set_job_status(job_id, status: str):
    await broadcasts_col.update_one({"_id": job_id}, {"$set": {"status": status}})


async def heartbeat(job_id):
    await broadcasts_col.update_one({"_id": job_id}, {"$set": {"heartbeat": time.time()}})


async def pending_bot_jobs(job_id) -> list:
    cur = bcast_bots_col.find({"job_id": job_id, "status": {"$in": ["pending", "running"]}})
    return [d async for d in cur]


async def save_bot_progress(job_id, token, cursor, sent, blocked, failed, status):
    await bcast_bots_col.update_one(
        {"job_id": job_id, "bot_token": token},
        {"$set": {"cursor": cursor, "sent": sent, "blocked": blocked,
                  "failed": failed, "status": status}},
    )


async def job_bot_rows(job_id) -> list:
    return [d async for d in bcast_bots_col.find({"job_id": job_id}).sort("username", 1)]


async def job_totals(job_id) -> dict:
    t = {"bots_total": 0, "bots_finished": 0, "revoked": 0, "errors": 0,
         "sent": 0, "blocked": 0, "failed": 0}
    pipeline = [{"$match": {"job_id": job_id}}, {"$group": {
        "_id": "$status", "n": {"$sum": 1}, "sent": {"$sum": "$sent"},
        "blocked": {"$sum": "$blocked"}, "failed": {"$sum": "$failed"}}}]
    async for d in bcast_bots_col.aggregate(pipeline):
        t["bots_total"] += d["n"]
        t["sent"] += d["sent"]
        t["blocked"] += d["blocked"]
        t["failed"] += d["failed"]
        if d["_id"] in ("done", "revoked", "error", "stopped"):
            t["bots_finished"] += d["n"]
        if d["_id"] == "revoked":
            t["revoked"] = d["n"]
        if d["_id"] == "error":
            t["errors"] = d["n"]
    return t
