# Multi-Bot Auto-Reply + Broadcast Platform

Users connect their own bot token to your main bot. Every connected bot then
auto-replies with one shared, editable message to anything sent to it
(`/start` or any text). You (owner) can broadcast to every user of every
connected bot, sequentially and flood-safe. No per-bot client objects are
kept in memory — everything runs through raw Bot API calls over one shared
`aiohttp` session, driven by webhooks, so RAM usage doesn't scale with bot
count.

## Commands

**Anyone**
- `/start`, `/addbot` — connect a bot (send its token)
- send a `.txt`/`.json` file — bulk-connects every token found in it
  (works even if the file has usernames/bot names mixed in on the same lines)
- `/setreply` — change the shared reply text (applies to every bot instantly)

**Owner only**
- `/broadcast` → send text → `/confirm` — message every user of every connected bot
- `/capacity` — live RAM/CPU + bot counts vs your configured limit
- `/dashboard` — connected/disconnected counts + top 5 bots by messages (24h)

## Setup

1. `pip install -r requirements.txt`
2. Set environment variables (Heroku Config Vars or local `.env`):

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | ✅ | Your main controller bot's token |
| `OWNER_ID` | ✅ | Your numeric Telegram user ID |
| `MONGO_URI` | ✅ | MongoDB connection string (Atlas free tier works) |
| `APP_URL` | ✅ | Your Heroku app URL, e.g. `https://yourapp.herokuapp.com` (no trailing slash) |
| `WEBHOOK_SECRET` | ✅ | Any random string, keeps your main bot's webhook path private |
| `MAX_BOTS_LIMIT` | ❌ | Default 400 — hard cap on connected bots |
| `BROADCAST_DELAY` | ❌ | Default 0.05s — delay between broadcast sends |

3. Deploy on Heroku as a **web** dyno (not worker — this app is HTTP-driven):
   ```
   git push heroku main
   ```
   The `Procfile` already has `web: python run.py`.

## Why webhooks, not polling

With 1000+ connected bots, keeping a persistent client per bot would need
50–150MB of RAM each. Instead, every worker bot's webhook points at
`APP_URL/webhook/<token>` on this same app. Incoming messages are handled,
replied to, and forgotten — RAM tracks message *traffic*, not bot *count*.
Check `/capacity` regularly; when RAM stays above ~70-80%, that's your signal
to raise `MAX_BOTS_LIMIT` cautiously or move to a bigger dyno.

## Notes

- A bot is auto-marked **disconnected** if a send fails and `getMe` also
  fails (token revoked/deleted from BotFather).
- The dashboard's "top bots" stat comes from a `messages` collection logged
  on every incoming update — cheap to query, accurate per time window.
