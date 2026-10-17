"""Доступ, /start, /help и общие помощники."""

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram import User as TgUser
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, ContextTypes

from ..db import Database, User
from ..ui import ADMIN_HELP, HELP_TEXT, MAIN_MENU

log = logging.getLogger(__name__)

ADMIN_PLACEHOLDER_NAME = "Админ"
INVITE_RE = re.compile(r"[A-Za-z0-9]{8}")


def get_db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.bot_data["db"]


def current_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> User:
    # gate() уже убедился, что пользователь зарегистрирован.
    return get_db(context).get_user(update.effective_user.id)


def display_name(user: TgUser) -> str:
    return user.full_name or user.username or str(user.id)


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пропускает дальше только участников книги; новичков регистрирует по коду приглашения."""
    tg_user, chat = update.effective_user, update.effective_chat
    if tg_user is None or chat is None or chat.type != ChatType.PRIVATE:
        raise ApplicationHandlerStop

    db = get_db(context)
    if db.get_user(tg_user.id):
        return

    msg = update.effective_message
    text = (msg.text or "").strip() if msg else ""
    code = text[len("/start"):].strip() if text.startswith("/start") else text
    if INVITE_RE.fullmatch(code) and db.use_invite(code, tg_user.id, display_name(tg_user)):
        log.info("Новый участник %s (%s)", display_name(tg_user), tg_user.id)
        await msg.reply_html(
            f"Добро пожаловать в книгу рецептов, {display_name(tg_user)}! 🍽\n\n" + HELP_TEXT,
            reply_markup=MAIN_MENU,
        )
        for admin in db.list_users():
            if admin.is_admin:
                try:
                    await context.bot.send_message(
                        admin.tg_id, f"👋 {display_name(tg_user)} присоединился(-ась) к книге рецептов."
                    )
                except Exception:  # админ мог ещё не запускать бота
                    log.warning("Не удалось уведомить админа %s", admin.tg_id)
        raise ApplicationHandlerStop

    if update.callback_query:
        await update.callback_query.answer("Нет доступа", show_alert=True)
    elif msg:
        await msg.reply_text(
            "Это закрытая книга рецептов 🔒\n"
            "Попросите у владельца код приглашения и отправьте его сюда.\n\n"
            f"Ваш Telegram ID: {tg_user.id}"
        )
    raise ApplicationHandlerStop


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_db(context)
    user = current_user(update, context)
    if user.name == ADMIN_PLACEHOLDER_NAME:
        db.rename_user(user.tg_id, display_name(update.effective_user))
    stats = db.stats()
    await update.message.reply_html(
        f"Привет! В книге сейчас {stats['own']} своих блюд и {stats['found']} найденных рецептов.\n\n"
        + HELP_TEXT
        + (ADMIN_HELP if user.is_admin else ""),
        reply_markup=MAIN_MENU,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = current_user(update, context)
    await update.message.reply_html(
        HELP_TEXT + (ADMIN_HELP if user.is_admin else ""), reply_markup=MAIN_MENU
    )


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Ошибка при обработке обновления", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("Что-то пошло не так 😕 Попробуйте ещё раз.")
        except Exception:
            pass
