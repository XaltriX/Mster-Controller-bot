import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

import config
from database.mongo import ensure_indexes
from bot.handlers import router
from worker.webhook_handler import handle_worker_webhook
from worker.api import close_session

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
dp.include_router(router)

MAIN_WEBHOOK_PATH = f"/main_webhook/{config.WEBHOOK_SECRET}"


async def on_startup(app: web.Application):
    await ensure_indexes()
    if config.APP_URL:
        await bot.set_webhook(f"{config.APP_URL}{MAIN_WEBHOOK_PATH}")
    logging.info("Startup complete.")


async def on_cleanup(app: web.Application):
    await close_session()


def build_app() -> web.Application:
    app = web.Application()

    # Main controller bot's own webhook
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=MAIN_WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    # Every connected worker bot posts updates here, routed by its own token
    app.router.add_post("/webhook/{token}", handle_worker_webhook)

    app.router.add_get("/", lambda r: web.Response(text="ok"))  # simple health check

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(build_app(), host="0.0.0.0", port=config.PORT)
