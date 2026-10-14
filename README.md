# Multi-Bot Auto-Reply + Broadcast Platform

Users connect their own bot token to the main bot. Every connected bot auto-replies
with one shared, editable message. The owner can broadcast to every user of every
connected bot. Everything runs on webhooks + raw Bot API calls (no per-bot client in
RAM), so it fits one Heroku web dyno.

## Commands

**Anyone**: `/start`, `/addbot`, or send a `.txt/.json` file with tokens (bulk connect)

**Owner only**
| Command | What it does |
|---|---|
| `/dashboard` | connected/disconnected bots, total + unique users, blocked users, 24h messages, top 5 by messages and by users |
| `/bots` | CSV of every bot: users, blocked users, messages 24h, status |
| `/checkbots` | getMe on every bot now; revoked/deleted tokens are marked disconnected |
| `/setreply` | change the reply text of all bots |
| `/broadcast` -> text -> `/confirm` | message every user of every connected bot |
| `/stopbroadcast` | stop a running broadcast |
| `/lastreport` | bot-wise CSV (delivered / blocked / failed) of the last broadcast |
| `/capacity` | RAM / CPU / bot limit |
| `/cancel` | leave any pending step |

## Config vars (Heroku)

Required: `BOT_TOKEN`, `OWNER_ID`, `MONGO_URI`, `APP_URL`, `WEBHOOK_SECRET`

Optional: `MAX_BOTS_LIMIT` (400), `BROADCAST_DELAY` (0.05s between messages of one bot),
`BROADCAST_CONCURRENCY` (8 bots sending at once), `HEALTHCHECK_HOURS` (6)

## How the broadcast stays reliable

- Progress is stored in MongoDB per bot (cursor = last user reached). If the dyno
  restarts, the broadcast resumes automatically from where it stopped.
- 8 bots send in parallel, each bot sends to its users one by one (Telegram limits are per bot).
- 429 "retry after" is waited out; network blips are retried.
- Users who blocked a bot are marked and skipped from then on.
- A revoked token found mid-broadcast disconnects that bot immediately.
- One crashing bot never stops the others; `/lastreport` shows exactly what happened per bot.

## Revoked tokens

A revoked bot never sends an update, so it can only be found by asking Telegram. A background
check runs every `HEALTHCHECK_HOURS` (and at startup), or run `/checkbots` any time.
Old message-stat rows (> 7 days) are deleted automatically to keep MongoDB small.
