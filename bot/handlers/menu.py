from telegram import Update
from telegram.ext import ContextTypes

from ..ui import BTN_LIST_FOUND, BTN_LIST_OWN, BTN_SEARCH, BTN_TAGS
from . import browse


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопки главного меню, кроме добавления (оно живёт в диалоге flows)."""
    text = update.message.text
    if text == BTN_LIST_OWN:
        await browse.list_kind(update, context, "own")
    elif text == BTN_LIST_FOUND:
        await browse.list_kind(update, context, "found")
    elif text == BTN_TAGS:
        await browse.show_tags(update, context)
    elif text == BTN_SEARCH:
        await browse.search_prompt(update, context)
