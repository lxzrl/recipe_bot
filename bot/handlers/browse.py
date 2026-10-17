"""Просмотр рецептов, списки, теги, поиск, удаление."""

from __future__ import annotations

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import ContextTypes

from ..db import User
from ..ui import (
    KIND_NAME,
    PAGE_SIZE,
    edit_keyboard,
    list_keyboard,
    recipe_card,
    recipe_keyboard,
    tags_keyboard,
)
from .common import current_user, get_db

LIST_TITLES = {"own": "🍲 Свои блюда", "found": "📚 Найденные рецепты"}


def can_delete(user: User, author_id: int) -> bool:
    return user.is_admin or user.tg_id == author_id


async def send_recipe(chat_id: int, context: ContextTypes.DEFAULT_TYPE, recipe_id: int, viewer: User) -> None:
    recipe = get_db(context).get_recipe(recipe_id)
    if recipe is None:
        await context.bot.send_message(chat_id, "Рецепт не найден — возможно, его удалили.")
        return
    for i in range(0, len(recipe.photos), 10):  # в одном альбоме Telegram не больше 10 фото
        group = recipe.photos[i : i + 10]
        if len(group) == 1:
            await context.bot.send_photo(chat_id, group[0])
        else:
            await context.bot.send_media_group(chat_id, [InputMediaPhoto(fid) for fid in group])
    chunks = recipe_card(recipe)
    for chunk in chunks[:-1]:
        await context.bot.send_message(chat_id, chunk, parse_mode="HTML")
    await context.bot.send_message(
        chat_id,
        chunks[-1],
        parse_mode="HTML",
        reply_markup=recipe_keyboard(recipe, can_delete(viewer, recipe.author_id)),
        disable_web_page_preview=True,
    )


async def open_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    recipe_id = int(query.data.split(":")[1])
    await send_recipe(update.effective_chat.id, context, recipe_id, current_user(update, context))


async def back_to_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Возвращает обычные кнопки под карточкой (после меню правки или отмены удаления)."""
    query = update.callback_query
    await query.answer()
    recipe = get_db(context).get_recipe(int(query.data.split(":")[1]))
    if recipe is None:
        await query.edit_message_reply_markup(None)
        return
    user = current_user(update, context)
    await query.edit_message_reply_markup(recipe_keyboard(recipe, can_delete(user, recipe.author_id)))


async def edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    recipe = get_db(context).get_recipe(int(query.data.split(":")[1]))
    if recipe is None:
        await query.answer("Рецепт не найден", show_alert=True)
        return
    await query.answer("Что изменить?")
    await query.edit_message_reply_markup(edit_keyboard(recipe))


async def move_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    db = get_db(context)
    recipe = db.get_recipe(int(query.data.split(":")[1]))
    if recipe is None:
        await query.answer("Рецепт не найден", show_alert=True)
        return
    new_kind = "found" if recipe.kind == "own" else "own"
    db.set_kind(recipe.id, new_kind)
    recipe.kind = new_kind
    await query.answer(f"Перенесено в «{KIND_NAME[new_kind]}»")
    keyboard = recipe_keyboard(recipe, can_delete(current_user(update, context), recipe.author_id))
    chunks = recipe_card(recipe)
    if len(chunks) == 1:
        await query.edit_message_text(
            chunks[0], parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True
        )
    else:
        await query.edit_message_reply_markup(keyboard)


async def ask_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    rid = int(query.data.split(":")[1])
    recipe = get_db(context).get_recipe(rid)
    if recipe is None or not can_delete(current_user(update, context), recipe.author_id):
        await query.answer("Удалить может только автор или администратор", show_alert=True)
        return
    await query.answer()
    await query.edit_message_reply_markup(
        InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Да, удалить", callback_data=f"dy:{rid}"),
                    InlineKeyboardButton("Отмена", callback_data=f"rb:{rid}"),
                ]
            ]
        )
    )


async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    db = get_db(context)
    recipe = db.get_recipe(int(query.data.split(":")[1]))
    if recipe is None:
        await query.answer("Уже удалён")
        await query.edit_message_reply_markup(None)
        return
    if not can_delete(current_user(update, context), recipe.author_id):
        await query.answer("Удалить может только автор или администратор", show_alert=True)
        return
    db.delete_recipe(recipe.id)
    await query.answer("Удалено")
    await query.edit_message_text(f"🗑 Рецепт «{escape(recipe.title)}» удалён.", parse_mode="HTML")


# ---------- списки ----------


async def _show_page(update: Update, text: str, items, total: int, page: int, page_cb: str) -> None:
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    header = f"{text}\nВсего: {total}" + (f" · стр. {page + 1}/{pages}" if pages > 1 else "")
    keyboard = list_keyboard(items, total, page, page_cb)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(header, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.effective_message.reply_html(header, reply_markup=keyboard)


async def list_kind(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str, page: int = 0) -> None:
    items, total = get_db(context).list_recipes(kind=kind, offset=page * PAGE_SIZE, limit=PAGE_SIZE)
    if total == 0:
        if update.callback_query:
            await update.callback_query.answer()
        empty = (
            "Здесь пока пусто. Нажмите «➕ Своё блюдо», чтобы добавить первое."
            if kind == "own"
            else "Здесь пока пусто. Нажмите «📥 Найденный рецепт» или просто пришлите скриншот."
        )
        await update.effective_message.reply_text(empty)
        return
    await _show_page(update, f"<b>{LIST_TITLES[kind]}</b>", items, total, page, f"l:{kind}")


async def list_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, kind, page = update.callback_query.data.split(":")
    await list_kind(update, context, kind, int(page))


async def show_tags(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tags = get_db(context).list_tags()
    if not tags:
        await update.effective_message.reply_text(
            "Тегов пока нет. Их можно добавить при создании рецепта или через «✏️ Изменить»."
        )
        return
    await update.effective_message.reply_text("🏷 Выберите тег:", reply_markup=tags_keyboard(tags))


async def tag_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, tag_id, page = update.callback_query.data.split(":")
    db = get_db(context)
    name = db.get_tag(int(tag_id))
    if name is None:
        await update.callback_query.answer("Такого тега больше нет", show_alert=True)
        return
    page = int(page)
    items, total = db.list_recipes(tag_id=int(tag_id), offset=page * PAGE_SIZE, limit=PAGE_SIZE)
    await _show_page(update, f"<b>#{escape(name)}</b>", items, total, page, f"t:{tag_id}")


# ---------- поиск ----------


async def search_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Просто напишите, что ищете: название, ингредиент или тег.\nНапример: «курица сыр» или «десерт»."
    )


async def search_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_search(update, context, update.message.text)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await search_prompt(update, context)
        return
    await run_search(update, context, " ".join(context.args))


async def run_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str, page: int = 0) -> None:
    query = query.strip()[:100]
    context.user_data["query"] = query
    items, total = get_db(context).search(query, offset=page * PAGE_SIZE, limit=PAGE_SIZE)
    if total == 0:
        await update.effective_message.reply_html(f"По запросу «{escape(query)}» ничего не найдено 🤷")
        return
    await _show_page(update, f"🔍 <b>{escape(query)}</b>", items, total, page, "s")


async def search_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = context.user_data.get("query")
    if not query:
        await update.callback_query.answer("Повторите поиск", show_alert=True)
        return
    await run_search(update, context, query, int(update.callback_query.data.split(":")[1]))
