import asyncio
import os
import psutil
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

import config
from database import repo
from worker import api
from worker.broadcaster import run_broadcast
from utils.token_extractor import extract_tokens

router = Router()


def owner_only(user_id: int) -> bool:
    return user_id == config.OWNER_ID


# ---------------- FSM states ----------------

class AddBot(StatesGroup):
    waiting_token = State()


class SetReply(StatesGroup):
    waiting_text = State()


class Broadcast(StatesGroup):
    waiting_text = State()
    confirming = State()


# ---------------- /start ----------------

@router.message(Command("start"))
async def cmd_start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add my bot", callback_data="addbot")],
    ])
    await message.answer(
        "👋 Welcome!\n\nConnect your own Telegram bot here and it'll auto-reply "
        "to anyone who messages it with a fixed message you control.\n\n"
        "Tap below, or send /addbot to get started. You can also send a .txt "
        "file with multiple bot tokens to connect several at once.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "addbot")
async def cb_addbot(callback, state: FSMContext):
    await start_addbot(callback.message, state)
    await callback.answer()


# ---------------- Add a single bot ----------------

@router.message(Command("addbot"))
async def start_addbot(message: Message, state: FSMContext):
    connected = await repo.count_bots("connected")
    if connected >= config.MAX_BOTS_LIMIT:
        return await message.answer(
            "⚠️ We're at capacity right now, please try again later."
        )
    await state.set_state(AddBot.waiting_token)
    await message.answer(
        "Send me your bot token (from @BotFather).\n\n"
        "Tip: you can also just send a .txt/.json file containing one or "
        "more tokens - I'll pull them out automatically."
    )


@router.message(AddBot.waiting_token, F.text)
async def receive_token(message: Message, state: FSMContext):
    token = message.text.strip()
    result = await connect_bot(token, message.from_user.id)
    await message.answer(result)
    await state.clear()


async def connect_bot(token: str, owner_id: int) -> str:
    if await repo.bot_exists(token):
        return "⚠️ This bot is already connected."

    me = await api.get_me(token)
    if not me:
        return "❌ That token doesn't look valid. Double check it and try again."

    webhook_ok = await api.set_webhook(token)
    if not webhook_ok:
        return "❌ Couldn't set up the connection for that bot. Try again in a bit."

    await repo.add_bot(token, owner_id, me.get("username", ""), me.get("first_name", ""))
    return f"✅ Connected @{me.get('username')}! It'll now auto-reply to anyone who messages it."


# ---------------- Bulk import via file ----------------

@router.message(F.document)
async def bulk_import(message: Message):
    connected = await repo.count_bots("connected")
    if connected >= config.MAX_BOTS_LIMIT:
        return await message.answer("⚠️ We're at capacity right now, please try again later.")

    file = await message.bot.get_file(message.document.file_id)
    raw = await message.bot.download_file(file.file_path)
    text = raw.read().decode("utf-8", errors="ignore")

    tokens = extract_tokens(text)
    if not tokens:
        return await message.answer("❌ Couldn't find any bot tokens in that file.")

    status = await message.answer(f"🔍 Found {len(tokens)} token(s). Connecting...")
    connected_n = already_n = invalid_n = 0

    for token in tokens:
        if await repo.count_bots("connected") >= config.MAX_BOTS_LIMIT:
            break
        result = await connect_bot(token, message.from_user.id)
        if result.startswith("✅"):
            connected_n += 1
        elif result.startswith("⚠️"):
            already_n += 1
        else:
            invalid_n += 1
        await asyncio.sleep(0.5)  # stay well under Telegram's rate limits

    await status.edit_text(
        f"Done.\n✅ Connected: {connected_n}\n⚠️ Already connected: {already_n}\n❌ Invalid: {invalid_n}"
    )


# ---------------- Edit the global reply text ----------------

@router.message(Command("setreply"))
async def cmd_setreply(message: Message, state: FSMContext):
    current = await repo.get_global_reply()
    await state.set_state(SetReply.waiting_text)
    await message.answer(
        f"Current reply text:\n\n{current}\n\nSend the new text you want every "
        "connected bot to reply with."
    )


@router.message(SetReply.waiting_text, F.text)
async def receive_reply_text(message: Message, state: FSMContext):
    await repo.set_global_reply(message.text)
    await state.clear()
    await message.answer("✅ Reply text updated - every connected bot uses it immediately.")


# ---------------- Broadcast (owner only) ----------------

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    if not owner_only(message.from_user.id):
        return await message.answer("This command is owner-only.")
    await state.set_state(Broadcast.waiting_text)
    await message.answer("Send the message you want to broadcast to every user of every connected bot.")


@router.message(Broadcast.waiting_text, F.text)
async def receive_broadcast_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(Broadcast.confirming)
    bots = await repo.count_bots("connected")
    await message.answer(
        f"This will go out through {bots} connected bot(s), one bot and one user "
        f"at a time (flood-safe). Send /confirm to proceed or /cancel to stop."
    )


@router.message(Broadcast.confirming, Command("cancel"))
async def cancel_broadcast(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Cancelled.")


@router.message(Broadcast.confirming, Command("confirm"))
async def confirm_broadcast(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    status = await message.answer("📢 Broadcasting... 0% done.")

    async def progress(done_bots, total_bots, sent, failed):
        pct = int(done_bots / total_bots * 100) if total_bots else 100
        await status.edit_text(f"📢 Broadcasting... {pct}% done. Sent: {sent}, failed: {failed}.")

    result = await run_broadcast(data["text"], progress_cb=progress)
    await status.edit_text(
        f"✅ Broadcast finished.\nBots used: {result['bots']}\n"
        f"Delivered: {result['sent']}\nFailed: {result['failed']}"
    )


# ---------------- Capacity (owner only) ----------------

@router.message(Command("capacity"))
async def cmd_capacity(message: Message):
    if not owner_only(message.from_user.id):
        return await message.answer("This command is owner-only.")
    ram = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=0.5)
    connected = await repo.count_bots("connected")
    disconnected = await repo.count_bots("disconnected")

    await message.answer(
        "📊 **System Capacity**\n\n"
        f"RAM: {ram.percent}% used ({ram.used // (1024*1024)}MB / {ram.total // (1024*1024)}MB)\n"
        f"CPU: {cpu}%\n"
        f"Connected bots: {connected} / {config.MAX_BOTS_LIMIT} limit\n"
        f"Disconnected bots: {disconnected}\n"
        f"Dyno: {os.getenv('DYNO', 'local')}"
    )


# ---------------- Dashboard (owner only) ----------------

@router.message(Command("dashboard"))
async def cmd_dashboard(message: Message):
    if not owner_only(message.from_user.id):
        return await message.answer("This command is owner-only.")
    connected = await repo.count_bots("connected")
    disconnected = await repo.count_bots("disconnected")
    top = await repo.top_bots_last_hours(hours=24, limit=5)

    lines = [
        "📈 **Dashboard**",
        "",
        f"🟢 Connected: {connected}",
        f"🔴 Disconnected: {disconnected}",
        "",
        "**Top 5 bots (last 24h)**",
    ]
    if top:
        for i, b in enumerate(top, start=1):
            lines.append(f"{i}. @{b['username']} — {b['count']} messages")
    else:
        lines.append("No messages yet.")

    await message.answer("\n".join(lines))
