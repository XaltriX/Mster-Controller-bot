from motor.motor_asyncio import AsyncIOMotorClient
import config

_client = AsyncIOMotorClient(config.MONGO_URI)
db = _client[config.DB_NAME]

bots_col = db["bots"]          # one doc per connected worker bot
users_col = db["users"]        # one doc per (bot_token, user_id) pair
messages_col = db["messages"]  # one doc per incoming message, for stats
settings_col = db["settings"]  # global key/value settings (e.g. reply text)


async def ensure_indexes():
    await bots_col.create_index("token", unique=True)
    await users_col.create_index([("bot_token", 1), ("user_id", 1)], unique=True)
    await messages_col.create_index([("bot_token", 1), ("ts", 1)])
