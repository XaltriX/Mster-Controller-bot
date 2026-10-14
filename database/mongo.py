from motor.motor_asyncio import AsyncIOMotorClient
import config

_client = AsyncIOMotorClient(config.MONGO_URI)
db = _client[config.DB_NAME]

bots_col = db["bots"]                  # one doc per connected worker bot
users_col = db["users"]                # one doc per (bot_token, user_id)
messages_col = db["messages"]          # one doc per incoming message (stats)
settings_col = db["settings"]          # global key/value settings (reply text)
broadcasts_col = db["broadcasts"]      # one doc per broadcast job
bcast_bots_col = db["broadcast_bots"]  # per-bot progress inside a broadcast job


async def ensure_indexes():
    await bots_col.create_index("token", unique=True)
    await bots_col.create_index("status")
    await users_col.create_index([("bot_token", 1), ("user_id", 1)], unique=True)
    await messages_col.create_index([("bot_token", 1), ("ts", 1)])
    await messages_col.create_index("ts")
    await bcast_bots_col.create_index([("job_id", 1), ("bot_token", 1)], unique=True)
