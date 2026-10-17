"""Пошаговые диалоги: добавление рецепта и правка полей."""

from __future__ import annotations

import logging
from datetime import date
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..db import parse_tags
from ..ui import BTN_ADD_FOUND, BTN_ADD_OWN, MAIN_MENU, MENU_BUTTONS
from . import browse
from .common import current_user, get_db

log = logging.getLogger(__name__)

ADD, EDIT_TEXT, EDIT_PHOTOS = range(3)

MENU_FILTER = filters.Text(MENU_BUTTONS)
TEXT_INPUT = filters.TEXT & ~filters.COMMAND & ~MENU_FILTER

FLOW = {
    "own": ["title", "photos", "ingredients", "steps", "cook_time", "servings", "tags"],
    "found": ["photos", "title", "tags", "notes"],
}
PROMPTS = {
    ("own", "title"): "🍲 Как называется блюдо?",
    ("own", "photos"): "📷 Пришлите фото блюда (можно несколько). Когда закончите — нажмите «Дальше».",
    ("own", "ingredients"): (
        "🧂 Перечислите ингредиенты — каждый с новой строки. Например:\n"
        "картофель — 500 г\nлук — 1 шт\nсоль по вкусу"
    ),
    ("own", "steps"): "👩‍🍳 Опишите приготовление (можно по шагам, каждый с новой строки).",
    ("own", "cook_time"): "⏱ Сколько времени занимает готовка? Например: 40 мин или 1 ч 30 мин.",
    ("own", "servings"): "🍽 На сколько порций? Например: 4 или 4–6.",
    ("found", "photos"): (
        "📷 Пришлите фото или скриншоты рецепта (можно несколько). Когда закончите — нажмите «Дальше»."
    ),
    ("found", "title"): "Как назовём рецепт?",
    ("found", "notes"): "📝 Заметка или ссылка на источник (необязательно).",
}
TAGS_PROMPT = (
    "🏷 Напишите теги через запятую (например: суп, ужин, быстро) "
    "или выберите из существующих. Затем нажмите «Готово»."
)
FIELD_NAMES = {
    "title": "Название",
    "ingredients": "Ингредиенты",
    "steps": "Приготовление",
    "notes": "Заметки",
    "source": "Источник",
    "cook_time": "Время готовки",
    "servings": "Порции",
    "tags": "Теги",
}
SHORT_FIELDS = ("cook_time", "servings")
SHORT_LIMIT = 60
CANCEL_BUTTON = InlineKeyboardButton("✖️ Отмена", callback_data="a:cancel")


# ---------- добавление ----------


def _new_draft(kind: str) -> dict:
    return {
        "kind": kind,
        "step": 0,
        "title": "",
        "ingredients": "",
        "steps": "",
        "notes": "",
        "cook_time": "",
        "servings": "",
        "tags": [],
        "photos": [],
        "last_group": None,
    }


def _field(draft: dict) -> str:
    return FLOW[draft["kind"]][draft["step"]]


def _tag_keyboard(context: ContextTypes.DEFAULT_TYPE, selected: list[str]) -> InlineKeyboardMarkup:
    rows, row = [], []
    for tag_id, name, _ in get_db(context).list_tags()[:12]:
        mark = "✅ " if name in selected else ""
        row.append(InlineKeyboardButton(f"{mark}#{name}", callback_data=f"a:tg:{tag_id}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✅ Готово", callback_data="a:next"), CANCEL_BUTTON])
    return InlineKeyboardMarkup(rows)


async def _ask(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
    draft = context.user_data["draft"]
    field = _field(draft)
    if field == "tags":
        text = TAGS_PROMPT
        if draft["tags"]:
            text += "\n\nВыбрано: " + ", ".join(draft["tags"])
        await message.reply_text(text, reply_markup=_tag_keyboard(context, draft["tags"]))
        return
    required = draft["kind"] == "own" and field == "title"
    buttons = []
    if not required:
        label = "➡️ Дальше" if field == "photos" else "Пропустить"
        buttons.append(InlineKeyboardButton(label, callback_data="a:next"))
    await message.reply_text(
        PROMPTS[(draft["kind"], field)], reply_markup=InlineKeyboardMarkup([[*buttons, CANCEL_BUTTON]])
    )


async def start_own(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["draft"] = _new_draft("own")
    await _ask(update.message, context)
    return ADD


async def start_found(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["draft"] = _new_draft("found")
    await _ask(update.message, context)
    return ADD


async def start_from_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Фото, присланное вне диалога, начинает «найденный рецепт»."""
    draft = _new_draft("found")
    context.user_data["draft"] = draft
    _take_photo(update.message, draft)
    draft["last_group"] = update.message.media_group_id
    await update.message.reply_text(
        "📥 Сохраню как найденный рецепт. Пришлите ещё фото или нажмите «Дальше».\n"
        "Если это фото своего блюда — нажмите «Отмена» и выберите «➕ Своё блюдо».",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("➡️ Дальше", callback_data="a:next"), CANCEL_BUTTON]]
        ),
    )
    return ADD


def _take_photo(message: Message, draft: dict) -> None:
    draft["photos"].append(message.photo[-1].file_id)  # последний размер — самый крупный
    if message.caption:
        draft["notes"] = (draft["notes"] + "\n" + message.caption).strip()


async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data.get("draft")
    if draft is None:
        return await start_from_photo(update, context)
    message = update.message
    _take_photo(message, draft)
    # В альбоме каждое фото приходит отдельным сообщением — отвечаем один раз на альбом.
    group = message.media_group_id
    if group and group == draft["last_group"]:
        return ADD
    draft["last_group"] = group
    if _field(draft) == "photos":
        await message.reply_text(
            "📷 Фото получено. Можно прислать ещё или нажать «Дальше».",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("➡️ Дальше", callback_data="a:next"), CANCEL_BUTTON]]
            ),
        )
    else:
        await message.reply_text("📷 Фото добавлено к рецепту.")
    return ADD


async def image_as_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    await update.message.reply_text("Пришлите картинку как фото (не файлом), чтобы я смог её сохранить.")
    return None


async def add_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data.get("draft")
    if draft is None:  # черновик потерян (например, после ошибки) — считаем текст поиском
        await browse.search_text(update, context)
        return ConversationHandler.END
    field = _field(draft)
    text = update.message.text.strip()
    if field == "photos":
        await update.message.reply_text("Жду фото 📷 Или нажмите «Дальше», чтобы пропустить.")
        return ADD
    if field == "tags":
        for tag in parse_tags(text):
            if tag not in draft["tags"]:
                draft["tags"].append(tag)
        return await _advance(update.message, context)
    if field == "title" and len(text) > 200:
        await update.message.reply_text("Слишком длинное название — уложитесь в 200 символов.")
        return ADD
    if field in SHORT_FIELDS and len(text) > SHORT_LIMIT:
        await update.message.reply_text(f"Слишком длинно — уложитесь в {SHORT_LIMIT} символов.")
        return ADD
    # В заметки уже могли попасть подписи к фото — дописываем, а не затираем.
    draft[field] = (draft["notes"] + "\n" + text).strip() if field == "notes" else text
    return await _advance(update.message, context)


async def add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    draft = context.user_data.get("draft")
    if draft is None:
        await query.answer("Это действие устарело")
        return ConversationHandler.END
    parts = query.data.split(":")
    if parts[1] == "tg":
        name = get_db(context).get_tag(int(parts[2]))
        if name:
            if name in draft["tags"]:
                draft["tags"].remove(name)
            else:
                draft["tags"].append(name)
        await query.answer()
        await query.edit_message_reply_markup(_tag_keyboard(context, draft["tags"]))
        return ADD
    # a:next
    await query.answer()
    await query.edit_message_reply_markup(None)
    return await _advance(query.message, context)


async def _advance(message: Message, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data["draft"]
    draft["step"] += 1
    if draft["step"] < len(FLOW[draft["kind"]]):
        await _ask(message, context)
        return ADD
    return await _save(message, context)


async def _save(message: Message, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data.pop("draft")
    user = get_db(context).get_user(message.chat.id)
    title = draft["title"] or f"Рецепт от {date.today():%d.%m.%Y}"
    recipe_id = get_db(context).add_recipe(
        kind=draft["kind"],
        title=title,
        author_id=user.tg_id,
        author_name=user.name,
        ingredients=draft["ingredients"],
        steps=draft["steps"],
        notes=draft["notes"],
        cook_time=draft["cook_time"],
        servings=draft["servings"],
        tags=draft["tags"],
        photos=draft["photos"],
    )
    try:
        await message.reply_text("✅ Сохранено!", reply_markup=MAIN_MENU)
        await browse.send_recipe(message.chat.id, context, recipe_id, user)
    except TelegramError:
        # Рецепт уже в базе — диалог должен закончиться, даже если Telegram не ответил.
        log.exception("Не удалось показать сохранённый рецепт %s", recipe_id)
    return ConversationHandler.END


# ---------- правка ----------


async def start_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    _, rid, field = query.data.split(":")
    recipe = get_db(context).get_recipe(int(rid))
    if recipe is None:
        await query.answer("Рецепт не найден", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    context.user_data.pop("draft", None)
    context.user_data["edit"] = {"id": recipe.id, "field": field, "photos": []}
    cancel = InlineKeyboardMarkup([[CANCEL_BUTTON]])

    if field.startswith("photos"):
        verb = "добавлю к рецепту" if field == "photos_add" else "заменят все текущие"
        await query.message.reply_text(
            f"📷 Пришлите фото — они {verb}. Затем нажмите «Готово».",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("✅ Готово", callback_data="a:done"), CANCEL_BUTTON]]
            ),
        )
        return EDIT_PHOTOS

    current = ", ".join(recipe.tags) if field == "tags" else getattr(recipe, field)
    text = f"✏️ <b>{FIELD_NAMES[field]}</b>\n"
    if current:
        shown = current if len(current) <= 1500 else current[:1500] + "…"
        text += f"Сейчас:\n<code>{escape(shown)}</code>\n\n"
    text += "Пришлите новое значение."
    if field == "tags":
        text += " Теги — через запятую."
    if field != "title":
        text += " Чтобы очистить, отправьте «-»."
    await query.message.reply_html(text, reply_markup=cancel)
    return EDIT_TEXT


async def edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    edit = context.user_data.get("edit")
    if edit is None:
        return ConversationHandler.END
    db = get_db(context)
    value = update.message.text.strip()
    field = edit["field"]
    if field == "title" and (value == "-" or len(value) > 200):
        await update.message.reply_text("Название не может быть пустым и длиннее 200 символов.")
        return EDIT_TEXT
    if field in SHORT_FIELDS and len(value) > SHORT_LIMIT:
        await update.message.reply_text(f"Слишком длинно — уложитесь в {SHORT_LIMIT} символов.")
        return EDIT_TEXT
    if value == "-":
        value = ""
    if field == "tags":
        db.set_tags(edit["id"], parse_tags(value))
    else:
        db.update_field(edit["id"], field, value)
    context.user_data.pop("edit")
    await update.message.reply_text("✅ Обновлено")
    await browse.send_recipe(update.effective_chat.id, context, edit["id"], current_user(update, context))
    return ConversationHandler.END


async def edit_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    edit = context.user_data.get("edit")
    if edit is None:
        return await start_from_photo(update, context)
    edit["photos"].append(update.message.photo[-1].file_id)
    group = update.message.media_group_id
    if not group or group != edit.get("last_group"):
        edit["last_group"] = group
        await update.message.reply_text(
            "📷 Получено. Ещё фото или «Готово».",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("✅ Готово", callback_data="a:done"), CANCEL_BUTTON]]
            ),
        )
    return EDIT_PHOTOS


async def edit_photos_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    edit = context.user_data.get("edit")
    if edit is None:
        await query.answer("Это действие устарело")
        return ConversationHandler.END
    if not edit["photos"]:
        await query.answer("Сначала пришлите хотя бы одно фото", show_alert=True)
        return EDIT_PHOTOS
    await query.answer()
    await query.edit_message_reply_markup(None)
    db = get_db(context)
    if edit["field"] == "photos_replace":
        db.replace_photos(edit["id"], edit["photos"])
    else:
        db.add_photos(edit["id"], edit["photos"])
    context.user_data.pop("edit")
    await query.message.reply_text("✅ Фото сохранены")
    await browse.send_recipe(update.effective_chat.id, context, edit["id"], current_user(update, context))
    return ConversationHandler.END


async def waiting_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Жду текст ✍️ Или нажмите «Отмена».")
    return EDIT_TEXT


async def waiting_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Жду фото 📷 Или нажмите «Готово» / «Отмена».")
    return EDIT_PHOTOS


# ---------- отмена и прерывание ----------


def _clear(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("draft", None)
    context.user_data.pop("edit", None)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear(context)
    if update.callback_query:
        await update.callback_query.answer("Отменено")
        await update.callback_query.edit_message_reply_markup(None)
    await update.effective_message.reply_text("Отменено.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def interrupt_with_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Нажата кнопка главного меню посреди диалога: бросаем черновик и выполняем команду меню."""
    from .menu import handle_menu

    _clear(context)
    text = update.message.text
    if text == BTN_ADD_OWN:
        return await start_own(update, context)
    if text == BTN_ADD_FOUND:
        return await start_found(update, context)
    await handle_menu(update, context)
    return ConversationHandler.END


def build_conversation() -> ConversationHandler:
    edit_entry = CallbackQueryHandler(start_edit, pattern=r"^ef:\d+:\w+$")
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Text([BTN_ADD_OWN]), start_own),
            MessageHandler(filters.Text([BTN_ADD_FOUND]), start_found),
            CommandHandler("add", start_own),
            CommandHandler("found", start_found),
            MessageHandler(filters.PHOTO, start_from_photo),
            edit_entry,
        ],
        states={
            ADD: [
                MessageHandler(filters.PHOTO, add_photo),
                MessageHandler(filters.Document.IMAGE, image_as_file),
                MessageHandler(TEXT_INPUT, add_text),
                CallbackQueryHandler(add_callback, pattern=r"^a:(next|tg:\d+)$"),
            ],
            EDIT_TEXT: [
                MessageHandler(TEXT_INPUT, edit_text),
                MessageHandler(filters.PHOTO, waiting_text),
            ],
            EDIT_PHOTOS: [
                MessageHandler(filters.PHOTO, edit_photo),
                MessageHandler(filters.Document.IMAGE, image_as_file),
                CallbackQueryHandler(edit_photos_done, pattern=r"^a:done$"),
                MessageHandler(TEXT_INPUT, waiting_photo),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern=r"^a:cancel$"),
            MessageHandler(MENU_FILTER, interrupt_with_menu),
            edit_entry,  # правка другого поля/рецепта прямо из диалога
        ],
        name="flows",
    )
