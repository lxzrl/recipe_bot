"""Запуск: python -m bot"""

import logging
import os
import warnings

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.request import BaseRequest
from telegram.warnings import PTBUserWarning

from .config import Config, load_config
from .db import Database
from .handlers import admin, browse, common, flows, menu
from .handlers.common import ADMIN_PLACEHOLDER_NAME
from .ui import BTN_LIST_FOUND, BTN_LIST_OWN, BTN_SEARCH, BTN_TAGS

# ConversationHandler предупреждает про per_message=False с inline-кнопками — нам так и нужно.
warnings.filterwarnings("ignore", message=r".*per_message.*", category=PTBUserWarning)

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("recipe_bot")


async def stale_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("Это действие уже неактуально")


async def image_as_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Пришлите картинку как фото (не файлом), чтобы я смог её сохранить.")


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Главное меню"),
            BotCommand("add", "Добавить своё блюдо"),
            BotCommand("found", "Сохранить найденный рецепт"),
            BotCommand("search", "Поиск: /search курица"),
            BotCommand("cancel", "Отменить действие"),
            BotCommand("help", "Справка"),
        ]
    )
    log.info("Бот @%s запущен", app.bot.username)


def build_app(config: Config, db: Database, request: BaseRequest | None = None) -> Application:
    for admin_id in config.admin_ids:
        existing = db.get_user(admin_id)
        db.add_user(admin_id, existing.name if existing else ADMIN_PLACEHOLDER_NAME, is_admin=True)

    builder = Application.builder().token(config.token).post_init(post_init)
    if request is not None:  # тесты подставляют поддельный Telegram API
        builder = builder.request(request).get_updates_request(request)
    elif proxy := os.getenv("TELEGRAM_PROXY", "").strip():
        builder = builder.proxy(proxy).get_updates_proxy(proxy)
    app = builder.build()
    app.bot_data["db"] = db

    app.add_handler(TypeHandler(Update, common.gate), group=-1)

    app.add_handler(CommandHandler("start", common.start))
    app.add_handler(CommandHandler("help", common.help_command))
    app.add_handler(flows.build_conversation())

    app.add_handler(CommandHandler("search", browse.search_command))
    app.add_handler(CommandHandler("invite", admin.invite))
    app.add_handler(CommandHandler("users", admin.users))
    app.add_handler(CommandHandler("adduser", admin.add_user))
    app.add_handler(CommandHandler("remove", admin.remove_user))
    app.add_handler(CommandHandler("backup", admin.backup))
    app.add_handler(CommandHandler("cancel", flows.cancel))

    app.add_handler(MessageHandler(filters.Text([BTN_LIST_OWN, BTN_LIST_FOUND, BTN_TAGS, BTN_SEARCH]), menu.handle_menu))

    app.add_handler(CallbackQueryHandler(browse.open_recipe, pattern=r"^r:\d+$"))
    app.add_handler(CallbackQueryHandler(browse.back_to_recipe, pattern=r"^rb:\d+$"))
    app.add_handler(CallbackQueryHandler(browse.edit_menu, pattern=r"^e:\d+$"))
    app.add_handler(CallbackQueryHandler(browse.move_recipe, pattern=r"^mv:\d+$"))
    app.add_handler(CallbackQueryHandler(browse.ask_delete, pattern=r"^d:\d+$"))
    app.add_handler(CallbackQueryHandler(browse.confirm_delete, pattern=r"^dy:\d+$"))
    app.add_handler(CallbackQueryHandler(browse.list_page, pattern=r"^l:(own|found):\d+$"))
    app.add_handler(CallbackQueryHandler(browse.tag_page, pattern=r"^t:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(browse.search_page, pattern=r"^s:\d+$"))
    app.add_handler(CallbackQueryHandler(common.noop, pattern=r"^noop$"))
    app.add_handler(CallbackQueryHandler(stale_button))

    app.add_handler(MessageHandler(filters.Document.IMAGE, image_as_file))
    # Любой другой текст — поиск по книге.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, browse.search_text))

    app.add_error_handler(common.on_error)
    return app


def main() -> None:
    config = load_config()
    db = Database(config.db_path)
    app = build_app(config, db)
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        db.close()


if __name__ == "__main__":
    main()
