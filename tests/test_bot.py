"""Сквозные сценарии: настоящие обработчики PTB, поддельный Telegram API."""

import asyncio
import itertools
import json
from pathlib import Path

import pytest
from telegram import Update
from telegram.request import BaseRequest

from bot.__main__ import build_app
from bot.config import Config
from bot.db import Database

ADMIN, GUEST, STRANGER = 100, 200, 300


class FakeTelegram(BaseRequest):
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self._ids = itertools.count(5000)

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    @property
    def read_timeout(self):
        return 5

    async def do_request(self, url, method, request_data=None, **kwargs):
        name = url.rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data else {}
        self.calls.append((name, params))
        chat = {"id": params.get("chat_id", 0), "type": "private"}
        message = {"message_id": next(self._ids), "date": 0, "chat": chat, "text": params.get("text", "")}
        if name == "getMe":
            result = {"id": 1, "is_bot": True, "first_name": "Bot", "username": "recipe_test_bot"}
        elif name == "sendMediaGroup":
            result = [dict(message, message_id=next(self._ids)) for _ in params["media"]]
        elif name.startswith(("send", "edit")):
            result = message
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()

    def texts(self) -> list[str]:
        return [p.get("text", "") for n, p in self.calls if n in ("sendMessage", "editMessageText")]

    def last_text(self) -> str:
        return self.texts()[-1]

    def clear(self):
        self.calls.clear()


class Harness:
    def __init__(self, tmp_path: Path):
        self.db = Database(tmp_path / "test.db")
        self.tg = FakeTelegram()
        self.app = build_app(Config("1:TEST", frozenset({ADMIN}), tmp_path / "test.db"), self.db, self.tg)
        self._ids = itertools.count(1)
        self.loop = asyncio.new_event_loop()
        self.loop.run_until_complete(self.app.initialize())

    def close(self):
        self.loop.run_until_complete(self.app.shutdown())
        self.loop.close()

    def _send(self, data: dict):
        update = Update.de_json({"update_id": next(self._ids), **data}, self.app.bot)
        self.loop.run_until_complete(self.app.process_update(update))

    def _message(self, user: int, **fields) -> dict:
        return {
            "message_id": next(self._ids),
            "date": 0,
            "chat": {"id": user, "type": "private"},
            "from": {"id": user, "is_bot": False, "first_name": f"User{user}"},
            **fields,
        }

    def text(self, user: int, text: str):
        entities = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else []
        self._send({"message": self._message(user, text=text, entities=entities)})

    def photo(self, user: int, file_id: str, caption: str | None = None, group: str | None = None):
        fields = {"photo": [{"file_id": file_id + "_s", "file_unique_id": file_id + "s", "width": 90, "height": 90},
                            {"file_id": file_id, "file_unique_id": file_id, "width": 800, "height": 800}]}
        if caption:
            fields["caption"] = caption
        if group:
            fields["media_group_id"] = group
        self._send({"message": self._message(user, **fields)})

    def button(self, user: int, data: str):
        self._send({"callback_query": {
            "id": str(next(self._ids)),
            "from": {"id": user, "is_bot": False, "first_name": f"User{user}"},
            "chat_instance": "x",
            "data": data,
            "message": self._message(user, text="card"),
        }})


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.close()


def test_invite_flow(h):
    h.text(STRANGER, "привет")
    assert "закрытая книга" in h.tg.last_text()
    assert h.db.get_user(STRANGER) is None

    h.text(ADMIN, "/invite")
    code = h.tg.last_text().split("start=")[1].split()[0]
    h.text(STRANGER, "AAAAAAAA")  # неверный код
    assert h.db.get_user(STRANGER) is None
    h.text(STRANGER, f"/start {code}")
    assert h.db.get_user(STRANGER).name == f"User{STRANGER}"
    assert any(f"User{STRANGER} присоединился" in t for t in h.tg.texts())

    h.text(GUEST, "/invite")
    assert "закрытая" in h.tg.last_text()


def test_admin_renamed_on_start(h):
    h.text(ADMIN, "/start")
    assert h.db.get_user(ADMIN).name == f"User{ADMIN}"


def test_add_own_recipe(h):
    h.text(ADMIN, "➕ Своё блюдо")
    h.text(ADMIN, "Борщ")
    h.photo(ADMIN, "ph1", group="g1")
    h.photo(ADMIN, "ph2", group="g1")
    h.button(ADMIN, "a:next")
    h.text(ADMIN, "свёкла 2 шт\nкапуста")
    h.text(ADMIN, "Варить час")
    h.text(ADMIN, "1 ч 30 мин")
    h.button(ADMIN, "a:next")  # порции пропущены
    h.text(ADMIN, "суп, обед")
    assert "Сохранено" in "".join(h.tg.texts())

    [brief] = h.db.list_recipes(kind="own")[0]
    r = h.db.get_recipe(brief.id)
    assert (r.title, r.ingredients, r.steps, r.cook_time, r.servings, r.tags, r.photos) == (
        "Борщ", "свёкла 2 шт\nкапуста", "Варить час", "1 ч 30 мин", "", ["обед", "суп"], ["ph1", "ph2"]
    )
    assert any("⏱ 1 ч 30 мин" in t and "порций" not in t for t in h.tg.texts())
    assert any(n == "sendMediaGroup" for n, _ in h.tg.calls)

    h.tg.clear()
    h.text(ADMIN, "свекла")  # обычный текст — поиск
    assert "Всего: 1" in h.tg.last_text()


def test_photo_starts_found_recipe_with_skips_and_tag_buttons(h):
    h.db.add_recipe("own", "x", ADMIN, "A", tags=["десерт"])
    tag_id = h.db.list_tags()[0][0]

    h.photo(ADMIN, "scr1", caption="рецепт из инсты")
    h.button(ADMIN, "a:next")      # фото готовы
    h.button(ADMIN, "a:next")      # название пропущено
    h.button(ADMIN, f"a:tg:{tag_id}")
    h.button(ADMIN, "a:next")      # теги готовы
    h.text(ADMIN, "https://example.com/cake")

    [brief] = h.db.list_recipes(kind="found")[0]
    r = h.db.get_recipe(brief.id)
    assert r.title.startswith("Рецепт от")
    assert r.photos == ["scr1"] and r.tags == ["десерт"]
    assert r.notes == "рецепт из инсты\nhttps://example.com/cake"


def test_menu_button_interrupts_flow(h):
    h.text(ADMIN, "➕ Своё блюдо")
    h.text(ADMIN, "🍲 Свои блюда")
    assert "пусто" in h.tg.last_text()
    h.text(ADMIN, "просто текст")  # уже не название, а поиск
    assert h.db.stats() == {"own": 0, "found": 0}
    assert "ничего не найдено" in h.tg.last_text()


def test_cancel(h):
    h.text(ADMIN, "📥 Найденный рецепт")
    h.button(ADMIN, "a:cancel")
    h.text(ADMIN, "Торт")
    assert h.db.stats()["found"] == 0


def test_edit_fields_and_photos(h):
    rid = h.db.add_recipe("own", "Борщ", ADMIN, "A", tags=["суп"], photos=["old"])
    h.button(ADMIN, f"ef:{rid}:title")
    h.text(ADMIN, "Украинский борщ")
    h.button(ADMIN, f"ef:{rid}:tags")
    h.text(ADMIN, "суп, #свекла")
    h.button(ADMIN, f"ef:{rid}:notes")
    h.text(ADMIN, "-")
    h.button(ADMIN, f"ef:{rid}:servings")
    h.text(ADMIN, "x" * 61)  # слишком длинно — остаёмся в правке
    h.text(ADMIN, "4–6")
    h.button(ADMIN, f"ef:{rid}:photos_replace")
    h.photo(ADMIN, "new1")
    h.button(ADMIN, "a:done")
    r = h.db.get_recipe(rid)
    assert (r.title, r.tags, r.notes, r.servings, r.photos) == (
        "Украинский борщ", ["свекла", "суп"], "", "4–6", ["new1"]
    )
    assert any("🍽 порций: 4–6" in t for t in h.tg.texts())

    h.button(ADMIN, f"mv:{rid}")
    assert h.db.get_recipe(rid).kind == "found"


def test_only_author_or_admin_can_delete(h):
    h.db.add_user(GUEST, "Гость")
    h.db.add_user(STRANGER, "Ещё гость")
    rid = h.db.add_recipe("own", "Борщ", GUEST, "Гость")
    h.button(STRANGER, f"dy:{rid}")
    assert h.db.get_recipe(rid) is not None
    h.button(GUEST, f"d:{rid}")
    h.button(GUEST, f"dy:{rid}")
    assert h.db.get_recipe(rid) is None


def test_lists_tags_and_paging(h):
    for i in range(10):
        h.db.add_recipe("own", f"Блюдо {i}", ADMIN, "A", tags=["ужин"])
    h.text(ADMIN, "🍲 Свои блюда")
    assert "стр. 1/2" in h.tg.last_text()
    h.button(ADMIN, "l:own:1")
    assert "стр. 2/2" in h.tg.last_text()
    h.text(ADMIN, "🏷 Теги")
    tag_id = h.db.list_tags()[0][0]
    h.button(ADMIN, f"t:{tag_id}:0")
    assert "#ужин" in h.tg.last_text()
    h.text(ADMIN, "блюдо")
    h.button(ADMIN, "s:1")
    assert "стр. 2/2" in h.tg.last_text()


def test_backup(h):
    h.text(ADMIN, "/backup")
    assert any(n == "sendDocument" for n, _ in h.tg.calls)
