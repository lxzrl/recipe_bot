import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    token: str
    admin_ids: frozenset[int]
    db_path: Path


def load_config() -> Config:
    load_dotenv(ROOT / ".env")
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не задан BOT_TOKEN. Скопируйте .env.example в .env и впишите токен от @BotFather.")

    raw_ids = os.getenv("ADMIN_IDS", "").replace(";", ",")
    try:
        admin_ids = frozenset(int(x) for x in raw_ids.split(",") if x.strip())
    except ValueError:
        raise SystemExit("ADMIN_IDS должен содержать числовые Telegram ID через запятую.")
    if not admin_ids:
        raise SystemExit("Не задан ADMIN_IDS. Узнать свой ID можно у @userinfobot.")

    db_path = Path(os.getenv("DB_PATH", "data/recipes.db"))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    return Config(token=token, admin_ids=admin_ids, db_path=db_path)
