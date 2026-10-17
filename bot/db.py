"""Хранилище на SQLite: пользователи, приглашения, рецепты, фото и теги."""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("own", "found")
TEXT_FIELDS = ("title", "ingredients", "steps", "notes", "source")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id    INTEGER PRIMARY KEY,
    name     TEXT    NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    added_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS invites (
    code       TEXT    PRIMARY KEY,
    created_by INTEGER NOT NULL,
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    used_by    INTEGER,
    used_at    TEXT
);
CREATE TABLE IF NOT EXISTS recipes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL CHECK (kind IN ('own', 'found')),
    title       TEXT    NOT NULL,
    ingredients TEXT    NOT NULL DEFAULT '',
    steps       TEXT    NOT NULL DEFAULT '',
    notes       TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT '',
    author_id   INTEGER NOT NULL,
    author_name TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    search_text TEXT    NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS photos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    file_id   TEXT    NOT NULL,
    position  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT    NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS recipe_tags (
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (recipe_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_photos_recipe ON photos(recipe_id);
CREATE INDEX IF NOT EXISTS idx_recipes_kind ON recipes(kind);
"""


def normalize_tag(raw: str) -> str:
    return " ".join(raw.strip().lstrip("#").split()).casefold()[:40]


def parse_tags(text: str) -> list[str]:
    """«Суп, #ужин #на скорую руку» -> ['суп', 'ужин', 'на скорую руку'].

    Теги разделяются запятыми, переносами строк или символом #.
    """
    parts: list[str] = []
    for chunk in text.replace("\n", ",").split(","):
        chunk = chunk.strip()
        if "#" in chunk:
            parts.extend(chunk.split("#"))
        else:
            parts.append(chunk)
    result: list[str] = []
    for p in parts:
        tag = normalize_tag(p)
        if tag and tag not in result:
            result.append(tag)
    return result


@dataclass
class User:
    tg_id: int
    name: str
    is_admin: bool


@dataclass
class Recipe:
    id: int
    kind: str
    title: str
    ingredients: str
    steps: str
    notes: str
    source: str
    author_id: int
    author_name: str
    created_at: str
    tags: list[str] = field(default_factory=list)
    photos: list[str] = field(default_factory=list)


@dataclass
class RecipeBrief:
    id: int
    kind: str
    title: str
    photo_count: int


class Database:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        # Обработчики PTB выполняются в одном потоке event loop, одного соединения достаточно.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------- пользователи ----------

    def get_user(self, tg_id: int) -> User | None:
        row = self.conn.execute(
            "SELECT tg_id, name, is_admin FROM users WHERE tg_id = ?", (tg_id,)
        ).fetchone()
        return User(row["tg_id"], row["name"], bool(row["is_admin"])) if row else None

    def add_user(self, tg_id: int, name: str, is_admin: bool = False) -> None:
        self.conn.execute(
            """INSERT INTO users (tg_id, name, is_admin) VALUES (?, ?, ?)
               ON CONFLICT(tg_id) DO UPDATE SET name = excluded.name,
                   is_admin = MAX(users.is_admin, excluded.is_admin)""",
            (tg_id, name, int(is_admin)),
        )
        self.conn.commit()

    def rename_user(self, tg_id: int, name: str) -> None:
        self.conn.execute("UPDATE users SET name = ? WHERE tg_id = ?", (name, tg_id))
        self.conn.commit()

    def remove_user(self, tg_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM users WHERE tg_id = ? AND is_admin = 0", (tg_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def list_users(self) -> list[User]:
        rows = self.conn.execute(
            "SELECT tg_id, name, is_admin FROM users ORDER BY is_admin DESC, added_at"
        ).fetchall()
        return [User(r["tg_id"], r["name"], bool(r["is_admin"])) for r in rows]

    # ---------- приглашения ----------

    def create_invite(self, created_by: int) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        self.conn.execute(
            "INSERT INTO invites (code, created_by) VALUES (?, ?)", (code, created_by)
        )
        self.conn.commit()
        return code

    def use_invite(self, code: str, tg_id: int, name: str) -> bool:
        cur = self.conn.execute(
            """UPDATE invites SET used_by = ?, used_at = CURRENT_TIMESTAMP
               WHERE code = ? AND used_by IS NULL""",
            (tg_id, code.strip().upper()),
        )
        if cur.rowcount == 0:
            self.conn.rollback()
            return False
        self.conn.execute(
            "INSERT OR IGNORE INTO users (tg_id, name) VALUES (?, ?)", (tg_id, name)
        )
        self.conn.commit()
        return True

    # ---------- рецепты ----------

    def add_recipe(
        self,
        kind: str,
        title: str,
        author_id: int,
        author_name: str,
        ingredients: str = "",
        steps: str = "",
        notes: str = "",
        source: str = "",
        tags: list[str] | None = None,
        photos: list[str] | None = None,
    ) -> int:
        if kind not in KINDS:
            raise ValueError(f"unknown kind: {kind}")
        cur = self.conn.execute(
            """INSERT INTO recipes (kind, title, ingredients, steps, notes, source,
                                    author_id, author_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (kind, title, ingredients, steps, notes, source, author_id, author_name),
        )
        recipe_id = cur.lastrowid
        self._set_tags(recipe_id, tags or [])
        self._add_photos(recipe_id, photos or [])
        self._reindex(recipe_id)
        self.conn.commit()
        return recipe_id

    def get_recipe(self, recipe_id: int) -> Recipe | None:
        row = self.conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not row:
            return None
        return Recipe(
            id=row["id"],
            kind=row["kind"],
            title=row["title"],
            ingredients=row["ingredients"],
            steps=row["steps"],
            notes=row["notes"],
            source=row["source"],
            author_id=row["author_id"],
            author_name=row["author_name"],
            created_at=row["created_at"],
            tags=self._tags_of(recipe_id),
            photos=[
                r["file_id"]
                for r in self.conn.execute(
                    "SELECT file_id FROM photos WHERE recipe_id = ? ORDER BY position, id",
                    (recipe_id,),
                )
            ],
        )

    def update_field(self, recipe_id: int, field_name: str, value: str) -> None:
        if field_name not in TEXT_FIELDS:
            raise ValueError(f"unknown field: {field_name}")
        self.conn.execute(
            f"UPDATE recipes SET {field_name} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (value, recipe_id),
        )
        self._reindex(recipe_id)
        self.conn.commit()

    def set_kind(self, recipe_id: int, kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown kind: {kind}")
        self.conn.execute(
            "UPDATE recipes SET kind = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (kind, recipe_id),
        )
        self.conn.commit()

    def set_tags(self, recipe_id: int, tags: list[str]) -> None:
        self._set_tags(recipe_id, tags)
        self._reindex(recipe_id)
        self.conn.commit()

    def add_photos(self, recipe_id: int, file_ids: list[str]) -> None:
        self._add_photos(recipe_id, file_ids)
        self.conn.commit()

    def replace_photos(self, recipe_id: int, file_ids: list[str]) -> None:
        self.conn.execute("DELETE FROM photos WHERE recipe_id = ?", (recipe_id,))
        self._add_photos(recipe_id, file_ids)
        self.conn.commit()

    def delete_recipe(self, recipe_id: int) -> None:
        self.conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
        self._drop_unused_tags()
        self.conn.commit()

    def list_recipes(
        self, kind: str | None = None, tag_id: int | None = None, offset: int = 0, limit: int = 10
    ) -> tuple[list[RecipeBrief], int]:
        where, params = [], []
        if kind:
            where.append("r.kind = ?")
            params.append(kind)
        if tag_id is not None:
            where.append("r.id IN (SELECT recipe_id FROM recipe_tags WHERE tag_id = ?)")
            params.append(tag_id)
        return self._page(" AND ".join(where) or "1", params, offset, limit)

    def search(self, query: str, offset: int = 0, limit: int = 10) -> tuple[list[RecipeBrief], int]:
        words = [w for w in query.casefold().replace("ё", "е").split() if w]
        if not words:
            return [], 0
        where = " AND ".join("r.search_text LIKE ? ESCAPE '\\'" for _ in words)
        params = ["%" + _escape_like(w) + "%" for w in words]
        return self._page(where, params, offset, limit)

    def list_tags(self) -> list[tuple[int, str, int]]:
        rows = self.conn.execute(
            """SELECT t.id, t.name, COUNT(rt.recipe_id) AS cnt
               FROM tags t JOIN recipe_tags rt ON rt.tag_id = t.id
               GROUP BY t.id ORDER BY cnt DESC, t.name"""
        ).fetchall()
        return [(r["id"], r["name"], r["cnt"]) for r in rows]

    def get_tag(self, tag_id: int) -> str | None:
        row = self.conn.execute("SELECT name FROM tags WHERE id = ?", (tag_id,)).fetchone()
        return row["name"] if row else None

    def stats(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT kind, COUNT(*) AS c FROM recipes GROUP BY kind")
        result = {k: 0 for k in KINDS}
        result.update({r["kind"]: r["c"] for r in rows})
        return result

    # ---------- внутреннее ----------

    def _page(self, where: str, params: list, offset: int, limit: int):
        total = self.conn.execute(
            f"SELECT COUNT(*) FROM recipes r WHERE {where}", params
        ).fetchone()[0]
        rows = self.conn.execute(
            f"""SELECT r.id, r.kind, r.title,
                       (SELECT COUNT(*) FROM photos p WHERE p.recipe_id = r.id) AS pc
                FROM recipes r WHERE {where}
                ORDER BY r.created_at DESC, r.id DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return [RecipeBrief(r["id"], r["kind"], r["title"], r["pc"]) for r in rows], total

    def _tags_of(self, recipe_id: int) -> list[str]:
        return [
            r["name"]
            for r in self.conn.execute(
                """SELECT t.name FROM tags t JOIN recipe_tags rt ON rt.tag_id = t.id
                   WHERE rt.recipe_id = ? ORDER BY t.name""",
                (recipe_id,),
            )
        ]

    def _set_tags(self, recipe_id: int, tags: list[str]) -> None:
        self.conn.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (recipe_id,))
        for raw in tags:
            name = normalize_tag(raw)
            if not name:
                continue
            self.conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
            tag_id = self.conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()[0]
            self.conn.execute(
                "INSERT OR IGNORE INTO recipe_tags (recipe_id, tag_id) VALUES (?, ?)",
                (recipe_id, tag_id),
            )
        self._drop_unused_tags()

    def _drop_unused_tags(self) -> None:
        self.conn.execute(
            "DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM recipe_tags)"
        )

    def _add_photos(self, recipe_id: int, file_ids: list[str]) -> None:
        start = self.conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM photos WHERE recipe_id = ?", (recipe_id,)
        ).fetchone()[0]
        self.conn.executemany(
            "INSERT INTO photos (recipe_id, file_id, position) VALUES (?, ?, ?)",
            [(recipe_id, fid, start + i) for i, fid in enumerate(file_ids)],
        )

    def _reindex(self, recipe_id: int) -> None:
        # SQLite LOWER() не понимает кириллицу, поэтому храним заранее нормализованный текст.
        row = self.conn.execute(
            "SELECT title, ingredients, steps, notes, source FROM recipes WHERE id = ?",
            (recipe_id,),
        ).fetchone()
        if not row:
            return
        text = " ".join([*row, *self._tags_of(recipe_id)]).casefold().replace("ё", "е")
        self.conn.execute("UPDATE recipes SET search_text = ? WHERE id = ?", (text, recipe_id))


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def backup_database(db: Database, target: str | Path) -> None:
    """Консистентная копия базы, даже пока бот работает."""
    dest = sqlite3.connect(target)
    try:
        db.conn.backup(dest)
    finally:
        dest.close()
