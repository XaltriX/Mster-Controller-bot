import asyncio
import csv
import html
import io
import os
from datetime import datetime

import psutil
from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

import config
from database import repo
from utils.token_extractor import extract_tokens
from utils.tasks import spawn
from worker import api, broadcaster, healthcheck

router = Router()

OWNER = F.from_user.id == config.OWNER_ID  # owner-only commands are silently ignored for others


# ---------------- FSM states ----------------

class AddBot(StatesGroup):
    waiting_token = State()


class SetReply(StatesGroup):
    waiting_text = State()


class Broadcast(StatesGroup):
    waiting_text = State()
    confirming = State()


# ---------------- /cancel (works in every state; registered first on purpose) ----------------

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Cancelled.")


# ---------------- /start ----------------

@router.message(Command("start"))
async def cmd_start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add my bot", callback_data="addbot")],
    ])
    await message.answer(
        "👋 Welcome!\n\nConnect your own Telegram bot here and it'll auto-reply "
        "to anyone who messages it with a fixed message.\n\n"
        "Tap below, or send /addbot to get started. You can also send a .txt "
        "file with multiple bot tokens to connect several at once.",
        reply_markup=kb,
    )


@router.message(Command("help"), OWNER)
async def cmd_help(message: Message):
    await message.answer(
        "Owner commands:\n"
        "/dashboard - bots, users, top bots\n"
        "/listbot - all bots (connected + disconnected) with @username and users\n"
        "/bots - CSV of every bot (users, messages, status)\n"
        "/checkbots - find revoked/deleted tokens now\n"
        "/setreply - change the reply text of all bots\n"
        "/broadcast - message every user of every bot\n"
        "/stopbroadcast - stop a running broadcast\n"
        "/lastreport - bot-wise CSV of the last broadcast\n"
        "/capacity - RAM / CPU / bot limit\n"
        "/cancel - leave any pending step"
    )


@router.callback_query(F.data == "addbot")
async def cb_addbot(callback: CallbackQuery, state: FSMContext):
    await start_addbot(callback.message, state)
    await callback.answer()


# ---------------- Add a single bot ----------------

@router.message(Command("addbot"))
async def start_addbot(message: Message, state: FSMContext):
    if await repo.count_bots("connected") >= config.MAX_BOTS_LIMIT:
        return await message.answer("⚠️ We're at capacity right now, please try again later.")
    await state.set_state(AddBot.waiting_token)
    await message.answer(
        "Send me your bot token (from @BotFather).\n\n"
        "Tip: you can also send a .txt/.json file containing one or more tokens - "
        "I'll pull them out automatically."
    )


@router.message(AddBot.waiting_token, F.text, ~F.text.startswith("/"))
async def receive_token(message: Message, state: FSMContext):
    result = await connect_bot(message.text.strip(), message.from_user.id)
    await message.answer(result)
    await state.clear()


async def connect_bot(token: str, owner_id: int) -> str:
    existing = await repo.get_bot(token)
    if existing and existing.get("status") == "connected":
        return "⚠️ This bot is already connected."

    me = await api.get_me(token)
    if not me:
        return "❌ That token doesn't look valid. Double check it and try again."

    if not await api.set_webhook(token):
        return "❌ Couldn't set up the connection for that bot. Try again in a bit."

    await repo.add_bot(token, owner_id, me.get("username", ""), me.get("first_name", ""))
    return f"✅ Connected @{me.get('username')}! It'll now auto-reply to anyone who messages it."


# ---------------- Bulk import via file ----------------

@router.message(F.document)
async def bulk_import(message: Message):
    if await repo.count_bots("connected") >= config.MAX_BOTS_LIMIT:
        return await message.answer("⚠️ We're at capacity right now, please try again later.")

    file = await message.bot.get_file(message.document.file_id)
    raw = await message.bot.download_file(file.file_path)
    tokens = extract_tokens(raw.read().decode("utf-8", errors="ignore"))
    if not tokens:
        return await message.answer("❌ Couldn't find any bot tokens in that file.")

    status = await message.answer(f"🔍 Found {len(tokens)} token(s). Connecting...")
    # Background task: Heroku kills any single request after ~30s
    spawn(_bulk_import_bg(tokens, message.from_user.id, status))


async def _bulk_import_bg(tokens: list[str], owner_id: int, status: Message):
    connected_n = already_n = invalid_n = 0
    for token in tokens:
        if await repo.count_bots("connected") >= config.MAX_BOTS_LIMIT:
            break
        result = await connect_bot(token, owner_id)
        if result.startswith("✅"):
            connected_n += 1
        elif result.startswith("⚠️"):
            already_n += 1
        else:
            invalid_n += 1
        await asyncio.sleep(0.5)
    try:
        await status.edit_text(
            f"Done.\n✅ Connected: {connected_n}\n⚠️ Already connected: {already_n}\n❌ Invalid: {invalid_n}"
        )
    except Exception:
        pass


# ---------------- Edit the global reply text (OWNER ONLY - it changes every bot) ----------------

@router.message(Command("setreply"), OWNER)
async def cmd_setreply(message: Message, state: FSMContext):
    current = await repo.get_global_reply()
    await state.set_state(SetReply.waiting_text)
    await message.answer(
        f"Current reply text:\n\n{current}\n\nSend the new text for every connected bot "
        "(or /cancel)."
    )


@router.message(SetReply.waiting_text, F.text, ~F.text.startswith("/"))
async def receive_reply_text(message: Message, state: FSMContext):
    await repo.set_global_reply(message.text)
    await state.clear()
    await message.answer("✅ Reply text updated - every connected bot uses it now.")


# ---------------- Broadcast (owner only) ----------------

@router.message(Command("broadcast"), OWNER)
async def cmd_broadcast(message: Message, state: FSMContext):
    if await repo.get_running_job():
        return await message.answer("A broadcast is already running. /stopbroadcast to stop it first.")
    await state.set_state(Broadcast.waiting_text)
    await message.answer("Send the message to broadcast to every user of every connected bot.\n(/cancel to abort)")


@router.message(Broadcast.waiting_text, F.text, ~F.text.startswith("/"))
async def receive_broadcast_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(Broadcast.confirming)
    bots, users = await repo.count_bots("connected"), await repo.count_users()
    await message.answer(
        f"This goes out through {bots} connected bots to about {users} users "
        "(users who blocked a bot are skipped).\n\n/confirm to start, /cancel to abort."
    )


@router.message(Broadcast.confirming, Command("confirm"), OWNER)
async def confirm_broadcast(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    if await repo.get_running_job():
        return await message.answer("A broadcast is already running.")
    status = await message.answer("📢 Starting broadcast...")
    await broadcaster.start(data["text"], message.bot, status.chat.id, status.message_id)


@router.message(Command("stopbroadcast"), OWNER)
async def cmd_stopbroadcast(message: Message):
    job = await repo.get_running_job()
    if not job:
        return await message.answer("No broadcast is running.")
    await broadcaster.stop(job["_id"])
    await message.answer("🛑 Stopping... the final report arrives in a few seconds.")


@router.message(Command("lastreport"), OWNER)
async def cmd_lastreport(message: Message):
    job = await repo.get_last_job()
    if not job:
        return await message.answer("No broadcast has been run yet.")
    rows = await repo.job_bot_rows(job["_id"])
    totals = await repo.job_totals(job["_id"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["bot", "status", "delivered", "blocked_or_left", "failed"])
    for r in rows:
        w.writerow([f"@{r.get('username', '')}", r["status"], r["sent"], r["blocked"], r["failed"]])
    await message.answer_document(
        BufferedInputFile(buf.getvalue().encode("utf-8-sig"), filename="broadcast_report.csv"),
        caption=f"Last broadcast ({job['status']})\n\n{broadcaster.summary(totals)}",
    )


# ---------------- Bot health ----------------

@router.message(Command("checkbots"), OWNER)
async def cmd_checkbots(message: Message):
    if healthcheck.is_running():
        return await message.answer("A check is already running, wait for it to finish.")
    status = await message.answer("🔎 Checking every connected bot... takes a minute or two.")
    spawn(_checkbots_bg(status))


async def _checkbots_bg(status: Message):
    res = await healthcheck.check_all()
    try:
        await status.edit_text(
            "✅ Check finished.\n"
            f"Checked: {res['checked']}\n"
            f"🟢 Alive: {res['ok']}\n"
            f"🔴 Revoked/deleted (now marked disconnected): {res['revoked']}\n"
            f"⏳ Couldn't verify (temporary errors, left as is): {res['transient']}"
        )
    except Exception:
        pass


# ---------------- Capacity & dashboard (owner only) ----------------

@router.message(Command("capacity"), OWNER)
async def cmd_capacity(message: Message):
    ram = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=0.5)
    connected, disconnected = await asyncio.gather(
        repo.count_bots("connected"), repo.count_bots("disconnected"))
    await message.answer(
        "📊 <b>System Capacity</b>\n\n"
        f"RAM: {ram.percent}% ({ram.used // (1024 * 1024)}MB / {ram.total // (1024 * 1024)}MB)\n"
        f"CPU: {cpu}%\n"
        f"Connected bots: {connected} / {config.MAX_BOTS_LIMIT} limit\n"
        f"Disconnected bots: {disconnected}\n"
        f"Dyno: {os.getenv('DYNO', 'local')}",
        parse_mode="HTML",
    )


@router.message(Command("dashboard"), OWNER)
async def cmd_dashboard(message: Message):
    (connected, disconnected, users, blocked, unique, msgs24,
     top_msgs, top_users) = await asyncio.gather(
        repo.count_bots("connected"), repo.count_bots("disconnected"),
        repo.count_users(), repo.count_blocked_users(), repo.count_unique_users(),
        repo.messages_since(24), repo.top_bots_by_messages(24, 5), repo.top_bots_by_users(5),
    )
    lines = [
        "📈 <b>Dashboard</b>", "",
        f"🟢 Connected bots: {connected}",
        f"🔴 Disconnected bots: {disconnected}", "",
        f"👥 Total users (all bots): {users}",
        f"🧍 Unique people: {unique}",
        f"🚫 Blocked / left: {blocked}",
        f"💬 Messages (24h): {msgs24}", "",
        "<b>Top 5 bots by messages (24h)</b>",
    ]
    if top_msgs:
        for i, b in enumerate(top_msgs, 1):
            lines.append(f"{i}. @{html.escape(b['username'])} — {b['messages']} msgs · {b['users']} users")
    else:
        lines.append("No messages yet.")
    lines += ["", "<b>Top 5 bots by users</b>"]
    if top_users:
        for i, b in enumerate(top_users, 1):
            lines.append(f"{i}. @{html.escape(b['username'])} — {b['users']} users")
    else:
        lines.append("No users yet.")
    lines += ["", "Full list: /bots"]
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("bots"), OWNER)
async def cmd_bots(message: Message):
    bots, per_bot, msgs = await asyncio.gather(
        repo.get_all_bots(), repo.users_per_bot(), repo.messages_per_bot(24))
    rows = []
    for b in bots:
        u = per_bot.get(b["token"], {"active": 0, "blocked": 0})
        added = datetime.fromtimestamp(b["connected_at"]).strftime("%Y-%m-%d") if b.get("connected_at") else ""
        rows.append([f"@{b.get('username', '')}", b.get("status", ""), u["active"], u["blocked"],
                     msgs.get(b["token"], 0), b.get("owner_id", ""), added])
    rows.sort(key=lambda r: r[2], reverse=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["bot", "status", "users", "blocked_users", "messages_24h", "added_by", "added_on"])
    w.writerows(rows)
    await message.answer_document(
        BufferedInputFile(buf.getvalue().encode("utf-8-sig"), filename="bots_report.csv"),
        caption=f"{len(rows)} bots, sorted by users.",
    )


@router.message(Command("listbot"), OWNER)
async def cmd_listbot(message: Message, command: CommandObject):
    """All bots with @username + users. `/listbot connected` or `/listbot disconnected` to filter."""
    arg = (command.args or "").strip().lower()
    bots, per_bot = await asyncio.gather(repo.get_all_bots(), repo.users_per_bot())

    def users(b):
        return per_bot.get(b["token"], {"active": 0})["active"]

    sections, caption = [], []
    for status, icon in (("connected", "🟢"), ("disconnected", "🔴")):
        if arg in ("connected", "disconnected") and arg != status:
            continue
        group = sorted((b for b in bots if b.get("status") == status), key=users, reverse=True)
        total = sum(users(b) for b in group)
        lines = [f"{status.upper()} BOTS: {len(group)}  |  TOTAL USERS: {total}", "-" * 44]
        for i, b in enumerate(group, 1):
            why = f"  ({b['disconnect_reason']})" if status == "disconnected" and b.get("disconnect_reason") else ""
            lines.append(f"{i}. @{b.get('username') or '?'} — {users(b)} users{why}")
        sections.append("\n".join(lines))
        caption.append(f"{icon} {status.capitalize()}: {len(group)} bots · {total} users")

    await message.answer_document(
        BufferedInputFile("\n\n".join(sections).encode("utf-8"), filename="listbot.txt"),
        caption="\n".join(caption),
    )
