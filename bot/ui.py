"""Тексты, клавиатуры и форматирование карточек рецептов."""

from __future__ import annotations

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from .db import Recipe, RecipeBrief

PAGE_SIZE = 8
MAX_MESSAGE = 4000

BTN_ADD_OWN = "➕ Своё блюдо"
BTN_ADD_FOUND = "📥 Найденный рецепт"
BTN_LIST_OWN = "🍲 Свои блюда"
BTN_LIST_FOUND = "📚 Найденные"
BTN_SEARCH = "🔍 Поиск"
BTN_TAGS = "🏷 Теги"
MENU_BUTTONS = (BTN_ADD_OWN, BTN_ADD_FOUND, BTN_LIST_OWN, BTN_LIST_FOUND, BTN_SEARCH, BTN_TAGS)

KIND_ICON = {"own": "🍲", "found": "📚"}
KIND_NAME = {"own": "Своё блюдо", "found": "Найденный рецепт"}

MAIN_MENU = ReplyKeyboardMarkup(
    [[BTN_ADD_OWN, BTN_ADD_FOUND], [BTN_LIST_OWN, BTN_LIST_FOUND], [BTN_SEARCH, BTN_TAGS]],
    resize_keyboard=True,
)

HELP_TEXT = (
    "<b>Как пользоваться</b>\n\n"
    f"{BTN_ADD_OWN} — добавить своё блюдо: название, фото, ингредиенты, приготовление, "
    "время, порции, теги.\n"
    f"{BTN_ADD_FOUND} — сохранить найденный рецепт: фото или скриншоты, название, теги, заметка.\n"
    f"{BTN_LIST_OWN} / {BTN_LIST_FOUND} — просмотр разделов.\n"
    f"{BTN_TAGS} — все теги, по нажатию — рецепты с этим тегом.\n"
    "🔍 Чтобы найти рецепт, просто напишите боту слово: название, ингредиент или тег. "
    "Несколько слов — ищутся рецепты, где есть все слова.\n\n"
    "Можно сразу отправить фото без команды — бот предложит сохранить его как рецепт.\n\n"
    "/cancel — отменить текущее действие\n"
    "/help — эта справка"
)

ADMIN_HELP = (
    "\n\n<b>Для администратора</b>\n"
    "/invite — создать одноразовое приглашение\n"
    "/users — список участников\n"
    "/adduser ID Имя — добавить человека по Telegram ID\n"
    "/remove ID — убрать доступ\n"
    "/backup — прислать файл базы данных"
)


def recipe_card(recipe: Recipe) -> list[str]:
    """Карточка рецепта в HTML, разбитая на сообщения не длиннее лимита Telegram."""
    date = recipe.created_at[:10]
    if len(date) == 10:
        date = f"{date[8:10]}.{date[5:7]}.{date[0:4]}"
    lines = [
        f"{KIND_ICON[recipe.kind]} <b>{escape(recipe.title)}</b>",
        f"<i>{KIND_NAME[recipe.kind]} · {escape(recipe.author_name)} · {date}</i>",
    ]
    if recipe.tags:
        lines.append("🏷 " + " ".join("#" + escape(t.replace(" ", "_")) for t in recipe.tags))
    facts = []
    if recipe.cook_time.strip():
        facts.append(f"⏱ {escape(recipe.cook_time.strip())}")
    if recipe.servings.strip():
        facts.append(f"🍽 порций: {escape(recipe.servings.strip())}")
    if facts:
        lines.append(" · ".join(facts))
    if recipe.photos:
        lines.append(f"📷 фото: {len(recipe.photos)}")

    if recipe.ingredients.strip():
        lines += ["", "<b>Ингредиенты:</b>"]
        for item in recipe.ingredients.strip().splitlines():
            item = item.strip()
            if not item:
                continue
            if item[0] not in "•-–—*":
                item = "• " + item
            lines += _escaped_pieces(item)
    if recipe.steps.strip():
        lines += ["", "<b>Приготовление:</b>"]
        for step in recipe.steps.strip().splitlines():
            lines += _escaped_pieces(step)
    if recipe.notes.strip():
        lines += ["", "<b>Заметки:</b>"]
        for note in recipe.notes.strip().splitlines():
            lines += _escaped_pieces(note)
    if recipe.source.strip():
        lines += ["", "<b>Источник:</b>"] + _escaped_pieces(recipe.source.strip())

    chunks, current = [], ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > MAX_MESSAGE and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    chunks.append(current)
    return chunks


def _escaped_pieces(text: str, size: int = 3000) -> list[str]:
    # Режем до экранирования, чтобы не разорвать HTML-сущность вроде &amp;
    return [escape(text[i : i + size]) for i in range(0, max(len(text), 1), size)]


def recipe_keyboard(recipe: Recipe, can_delete: bool) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton("✏️ Изменить", callback_data=f"e:{recipe.id}")]
    if can_delete:
        row.append(InlineKeyboardButton("🗑 Удалить", callback_data=f"d:{recipe.id}"))
    return InlineKeyboardMarkup([row])


def edit_keyboard(recipe: Recipe) -> InlineKeyboardMarkup:
    rid = recipe.id
    other = "found" if recipe.kind == "own" else "own"
    move_label = f"↔️ Перенести в «{KIND_NAME[other]}»"

    def b(text: str, field: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(text, callback_data=f"ef:{rid}:{field}")

    return InlineKeyboardMarkup(
        [
            [b("Название", "title"), b("Теги", "tags")],
            [b("Ингредиенты", "ingredients"), b("Приготовление", "steps")],
            [b("⏱ Время", "cook_time"), b("🍽 Порции", "servings")],
            [b("Заметки", "notes"), b("Источник", "source")],
            [b("📷 Добавить фото", "photos_add"), b("📷 Заменить фото", "photos_replace")],
            [InlineKeyboardButton(move_label, callback_data=f"mv:{rid}")],
            [InlineKeyboardButton("⬅️ Назад к рецепту", callback_data=f"r:{rid}")],
        ]
    )


def list_keyboard(
    items: list[RecipeBrief], total: int, page: int, page_cb: str
) -> InlineKeyboardMarkup:
    """page_cb — префикс callback_data, к которому дописывается номер страницы."""
    rows = []
    for item in items:
        photo = " 📷" if item.photo_count else ""
        title = item.title if len(item.title) <= 50 else item.title[:49] + "…"
        rows.append(
            [InlineKeyboardButton(f"{KIND_ICON[item.kind]} {title}{photo}", callback_data=f"r:{item.id}")]
        )
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"{page_cb}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"{page_cb}:{page + 1}"))
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


def tags_keyboard(tags: list[tuple[int, str, int]]) -> InlineKeyboardMarkup:
    rows, row = [], []
    for tag_id, name, count in tags[:90]:
        row.append(InlineKeyboardButton(f"#{name} ({count})", callback_data=f"t:{tag_id}:0"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)
