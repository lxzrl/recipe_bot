"""Команды владельца: приглашения, участники, резервная копия."""

from __future__ import annotations

import tempfile
from datetime import datetime
from functools import wraps
from html import escape
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from ..db import backup_database
from .common import current_user, get_db


def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not current_user(update, context).is_admin:
            await update.message.reply_text("Эта команда только для администратора.")
            return
        return await func(update, context)

    return wrapper


@admin_only
async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    code = get_db(context).create_invite(update.effective_user.id)
    link = f"https://t.me/{context.bot.username}?start={code}"
    await update.message.reply_html(
        "🎟 Одноразовое приглашение готово. Перешлите человеку ссылку:\n"
        f"{link}\n\n"
        f"Или пусть откроет бота @{context.bot.username} и отправит код <code>{code}</code>.",
        disable_web_page_preview=True,
    )


@admin_only
async def users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["<b>Участники книги:</b>"]
    for u in get_db(context).list_users():
        role = " 👑" if u.is_admin else ""
        lines.append(f"• {escape(u.name)}{role} — <code>{u.tg_id}</code>")
    await update.message.reply_html("\n".join(lines))


@admin_only
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Использование: /adduser ID Имя\nСвой ID человек увидит, если напишет боту, или у @userinfobot."
        )
        return
    tg_id = int(context.args[0])
    db = get_db(context)
    existing = db.get_user(tg_id)
    if existing:
        await update.message.reply_text(f"{existing.name} уже в списке.")
        return
    name = " ".join(context.args[1:]).strip() or f"Гость {tg_id}"
    db.add_user(tg_id, name)
    await update.message.reply_text(f"✅ {name} добавлен(а). Пусть напишет боту /start.")


@admin_only
async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /remove ID (список — /users)")
        return
    if get_db(context).remove_user(int(context.args[0])):
        await update.message.reply_text("✅ Доступ убран. Добавленные этим человеком рецепты остаются в книге.")
    else:
        await update.message.reply_text("Такого участника нет (или это администратор).")


@admin_only
async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"recipes-{datetime.now():%Y-%m-%d}.db"
        backup_database(get_db(context), path)
        with path.open("rb") as f:
            await update.message.reply_document(
                f, filename=path.name, caption="💾 Резервная копия базы рецептов"
            )
