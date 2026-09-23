import os

# Main controller bot (the one you talk to as owner/users)
BOT_TOKEN = os.getenv("BOT_TOKEN","8911158533:AAG9qESFojxrlZoysMmmA8Dq7upBqhNWIco")
OWNER_ID = int(os.getenv("OWNER_ID", "5706788169"))

# MongoDB
MONGO_URI = os.getenv("MONGO_URI","mongodb+srv://sahilkhokhar4520_db_user:wdbCPrF6OCmDmWP9@cluster0.5tclbp8.mongodb.net/?appName=Cluster0")
DB_NAME = os.getenv("DB_NAME", "tgbroadcast")

# Your Heroku app's public https URL, e.g. https://yourapp.herokuapp.com
# (no trailing slash)
APP_URL = os.getenv("APP_URL", "").rstrip("/")

# Random secret path segment for the main bot's webhook so nobody else can hit it
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "xk29fj83mZ")

# Hard cap on how many worker bots this instance will accept.
# Bump this up as you monitor RAM/CPU with /capacity.
MAX_BOTS_LIMIT = int(os.getenv("MAX_BOTS_LIMIT", "400"))

# Delay (seconds) between messages while broadcasting, per bot,
# to stay under Telegram's per-bot flood limits.
BROADCAST_DELAY = float(os.getenv("BROADCAST_DELAY", "0.05"))

# Port Heroku assigns
PORT = int(os.getenv("PORT", "8080"))

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
