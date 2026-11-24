import asyncio
import secrets
import hmac
import hashlib
import html
import json
import logging
import os
import re
import sqlite3
import struct

from datetime import datetime, timedelta, timezone, time
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from dotenv import load_dotenv


# =====================================================
# CONFIGURATION
# =====================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

VERIFY_DB_PATH = Path("/var/lib/cloverteam/telegram_verifications.db")
VERIFY_SECRET = os.getenv("TELEGRAM_VERIFY_SECRET", "")

if len(VERIFY_SECRET) < 32:
    raise RuntimeError("TELEGRAM_VERIFY_SECRET отсутствует или слишком короткий")


RCON_HOST = os.getenv("RCON_HOST", "")
RCON_PORT = int(os.getenv("RCON_PORT", "25640"))
RCON_PASSWORD = os.getenv("RCON_PASSWORD", "")

DB_PATH = BASE_DIR / os.getenv(
    "DATABASE_PATH",
    "../Adm-bot/cloverteam.sqlite3"
)

ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
ADMIN_THREAD_ID = int(os.getenv("ADMIN_THREAD_ID", "0"))
MAIN_CHAT_ID = int(os.getenv("MAIN_CHAT_ID", "0"))

APPLICATION_THREAD_ID = int(
    os.getenv("APPLICATION_THREAD_ID", "0")
)

REST_THREAD_ID = int(
    os.getenv("REST_THREAD_ID", "0")
)

MOSCOW_TZ = ZoneInfo("Europe/Moscow")

REST_MAX_DAYS = 3650


# =====================================================
# LOGGING
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

log = logging.getLogger("cloverteam")


# =====================================================
# CONSTANTS
# =====================================================

RANKS = [
    "legacy",
    "player",
    "rest",
    "helper",
    "moder",
    "eventor",
    "tech_admin",
    "admin",
    "st_admin",
    "owner",
]

STAFF_RANKS = set(RANKS[3:])
JAVA_RANKS = {"owner", "st_admin"}

REST_ELIGIBLE_RANKS = {
    "player",
    "helper",
    "moder",
    "eventor",
    "tech_admin",
    "admin",
    "st_admin",
    "owner",
}

MC_NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")

APPLICATION_TEXT = (
    "🍀 Анкета для подачи заявки в CloverTeam 🍀\n\n"
    "Приветствуем! Чтобы попасть в CloverTeam, заполни "
    "анкету одним сообщением в ответ на это сообщение.\n\n"

    "⚠️ На нашем проекте есть лор и сюжетные ивенты. "
    "Если тебе неинтересен сюжетный игровой процесс, проект может тебе не подойти. \n\n"

    "1. Как к тебе можно обращаться?\n"
    "2. Дата рождения (дд.мм.гггг).\n"
    "3. Твой Minecraft-ник.\n"
    "4. Какое издание используешь: Java или Bedrock?\n"
    "5. Пол\n"
    "6. Telegram username (если есть).\n"
    "7. Оцени свою предполагаемую активность на сервере и в чате от 1 до 10.\n"
    "8. Играл(а) ли ты на похожих проектах? На каких?\n"
    "9. Как относишься к лору и сюжетным событиям?\n"
    "10. Почему хочешь присоединиться именно к CloverTeam?\n"
    "11. Есть ли у тебя RP-персонаж? (необязательно)\n"

)


REST_SELF_TEXT = (
    "Чтобы подать рест, заполните заявку одним сообщением "
    "в ответ на это сообщение.\n\n"
    "1. Причина, по которой вы хотите взять рест.\n"
    "2. Сколько дней вам потребуется? Указывать цифрами, "
    "минимум 1 день.\n"
    "3. Ваш Telegram username.\n"
    "4. Ваш Minecraft-ник."
)


REST_FRIEND_TEXT = (
    "Чтобы подать рест для друга, заполните заявку одним "
    "сообщением в ответ на это сообщение.\n\n"
    "1. Причина, по которой ваш друг хочет взять рест.\n"
    "2. Сколько дней потребуется другу? Минимум 1 день.\n"
    "3. Telegram username вашего друга.\n"
    "4. Minecraft-ник вашего друга.\n\n"
    "После заполнения формы бот попросит прислать "
    "скриншот доказательства и разрешения друга."
)


REST_EXTEND_TEXT = (
    "Для продления реста заполните заявку одним сообщением "
    "в ответ на это сообщение.\n\n"
    "1. До какого числа продлить рест? (ДД.ММ.ГГГГ)\n"
    "2. По какой причине вы хотите продлить рест?"
)


# =====================================================
# TEXT HELPERS
# =====================================================

def extract_command(message: Message) -> str:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def safe_html(value) -> str:
    return html.escape(str(value))


def format_code(value: str) -> str:
    return f"<pre><code>{safe_html(value)}</code></pre>"


def normalize_username(value: str) -> str:
    return value.strip().lstrip("@").lower()


def moscow_now() -> datetime:
    return datetime.now(MOSCOW_TZ)


def moscow_iso(value: datetime) -> str:
    return value.astimezone(MOSCOW_TZ).isoformat()


def parse_rest_lines(text: str, expected: int):
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    cleaned = []

    for line in lines:
        line = re.sub(r"^\d+[\.\)]\s*", "", line)
        cleaned.append(line.strip())

    if len(cleaned) != expected:
        return None

    if any(not item for item in cleaned):
        return None

    return cleaned


def parse_date(value: str):
    try:
        return datetime.strptime(
            value.strip(),
            "%d.%m.%Y"
        ).date()
    except ValueError:
        return None


# =====================================================
# ADMIN HELPERS (users.rank is the single source of truth)
# =====================================================

def get_admin(telegram_id: int):
    """Compatibility helper: returns an admin row from users, never a JSON file."""
    user = get_user(telegram_id)
    if not user or user["rank"] not in STAFF_RANKS:
        return None
    return {
        "rank": user["rank"],
        "minecraft": user["minecraft_nickname"] or "",
        "telegram": user["telegram_username"] or "",
    }


# =====================================================
# SQLITE
# =====================================================

def db_connect():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=15
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with db_connect() as conn:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                telegram_username TEXT,
                minecraft_nickname TEXT UNIQUE COLLATE NOCASE,
                rank TEXT NOT NULL DEFAULT 'player'
            )
        """)

        # Миграция старой схемы: у legacy пока нет Minecraft-ника,
        # поэтому minecraft_nickname должен допускать NULL.
        user_columns = {
            row["name"]: row
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }

        if user_columns.get("minecraft_nickname") and user_columns["minecraft_nickname"]["notnull"]:
            conn.execute("""
                CREATE TABLE users_new (
                    telegram_id INTEGER PRIMARY KEY,
                    telegram_username TEXT,
                    minecraft_nickname TEXT UNIQUE COLLATE NOCASE,
                    rank TEXT NOT NULL DEFAULT 'player'
                )
            """)
            conn.execute("""
                INSERT INTO users_new
                    (telegram_id, telegram_username, minecraft_nickname, rank)
                SELECT telegram_id, telegram_username, minecraft_nickname, rank
                FROM users
            """)
            conn.execute("DROP TABLE users")
            conn.execute("ALTER TABLE users_new RENAME TO users")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                telegram_username TEXT,
                prompt_message_id INTEGER,
                admin_message_id INTEGER,
                application_text TEXT,
                status TEXT NOT NULL DEFAULT 'awaiting_reply',
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS bans (
                telegram_id INTEGER PRIMARY KEY,
                reason TEXT,
                created_at TEXT NOT NULL
            )
        """)

        # Постоянная блокировка подачи анкет по Telegram ID.
        # Отдельная таблица гарантирует, что /unreg не снимет этот запрет.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ank_bans (
                telegram_id INTEGER PRIMARY KEY,
                telegram_username TEXT,
                admin_id INTEGER NOT NULL,
                application_id INTEGER,
                created_at TEXT NOT NULL
            )
        """)

        # Сводка активных варнов по Telegram ID. История хранится отдельно.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS warns (
                telegram_id INTEGER PRIMARY KEY,
                warn_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS warn_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                admin_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                days INTEGER NOT NULL,
                issued_at TEXT NOT NULL,
                expires_at TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                server_notified INTEGER NOT NULL DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_warn_history_active
            ON warn_history(telegram_id, status, expires_at)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS rest_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                applicant_id INTEGER NOT NULL,
                target_id INTEGER,
                request_type TEXT NOT NULL,
                reason TEXT NOT NULL,
                days INTEGER,
                target_username TEXT,
                target_nickname TEXT,
                requested_until TEXT,
                proof_file_id TEXT,
                admin_message_id INTEGER,
                reject_prompt_message_id INTEGER,
                reject_admin_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS rest_periods (
                telegram_id INTEGER PRIMARY KEY,
                original_rank TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ends_at TEXT NOT NULL,
                request_id INTEGER
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS rest_sessions (
                telegram_id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL,
                step TEXT NOT NULL,
                prompt_message_id INTEGER,
                data_json TEXT NOT NULL DEFAULT '{}'
            )
        """)

        # Безопасные миграции старой базы.

        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(rest_requests)"
            ).fetchall()
        }

        if "reject_prompt_message_id" not in columns:
            conn.execute("""
                ALTER TABLE rest_requests
                ADD COLUMN reject_prompt_message_id INTEGER
            """)

        if "reject_admin_id" not in columns:
            conn.execute("""
                ALTER TABLE rest_requests
                ADD COLUMN reject_admin_id INTEGER
            """)

        period_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(rest_periods)"
            ).fetchall()
        }

        if "original_rank" not in period_columns:
            conn.execute("""
                ALTER TABLE rest_periods
                ADD COLUMN original_rank TEXT NOT NULL DEFAULT 'player'
            """)

        conn.commit()


# =====================================================
# DATABASE HELPERS
# =====================================================

def get_user(telegram_id: int):
    with db_connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()


def ensure_legacy_user(telegram_id: int, telegram_username: str = "") -> bool:
    """Создаёт legacy-запись для замеченного участника основного чата."""
    existing_user = get_user(telegram_id)
    if existing_user and existing_user["rank"] in STAFF_RANKS:
        return False

    with db_connect() as conn:
        existing = conn.execute(
            "SELECT rank FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()

        if existing:
            if existing["rank"] == "legacy":
                conn.execute(
                    "UPDATE users SET telegram_username = ? WHERE telegram_id = ?",
                    (telegram_username or "", telegram_id)
                )
                conn.commit()
            return False

        conn.execute(
            """
            INSERT INTO users
                (telegram_id, telegram_username, minecraft_nickname, rank)
            VALUES (?, ?, NULL, 'legacy')
            """,
            (telegram_id, telegram_username or "")
        )
        conn.commit()

    log.info("Added legacy user: telegram_id=%s username=%s", telegram_id, telegram_username or "")
    return True


def get_user_by_nickname(nickname: str):
    with db_connect() as conn:
        return conn.execute(
            """
            SELECT *
            FROM users
            WHERE minecraft_nickname = ? COLLATE NOCASE
            """,
            (nickname,)
        ).fetchone()


def get_rest_period(telegram_id: int):
    with db_connect() as conn:
        return conn.execute(
            """
            SELECT *
            FROM rest_periods
            WHERE telegram_id = ?
            """,
            (telegram_id,)
        ).fetchone()


def get_account(telegram_id: int):
    """Returns account details from users; active rest temporarily overrides rank."""
    user = get_user(telegram_id)
    if not user:
        return None
    base_rank = user["rank"]
    nickname = user["minecraft_nickname"] or ""
    telegram = user["telegram_username"] or ""
    if base_rank not in RANKS:
        return None
    if base_rank != "legacy" and (not nickname or not MC_NAME_RE.fullmatch(nickname)):
        return None
    period = get_rest_period(telegram_id)
    if period:
        return {
            "rank": "rest", "minecraft": nickname, "telegram": telegram,
            "original_rank": period["original_rank"], "ends_at": period["ends_at"],
        }
    return {"rank": base_rank, "minecraft": nickname, "telegram": telegram}


def is_senior_admin(telegram_id: int) -> bool:
    if get_rest_period(telegram_id):
        return False
    user = get_user(telegram_id)
    return bool(user and user["rank"] in {"owner", "st_admin"})


def is_banned(telegram_id: int) -> bool:
    """Общая проверка блокировок, включая запрет подачи анкет."""
    with db_connect() as conn:
        result = conn.execute(
            """
            SELECT 1 FROM bans WHERE telegram_id = ?
            UNION ALL
            SELECT 1 FROM ank_bans WHERE telegram_id = ?
            UNION ALL
            SELECT 1 FROM users WHERE telegram_id = ? AND rank = 'warnbanned'
            LIMIT 1
            """,
            (telegram_id, telegram_id, telegram_id)
        ).fetchone()

    return result is not None


WARN_RANK_LEVEL = {
    "player": 0,
    "helper": 1,
    "moder": 2,
    "admin": 3,
    "tech_admin": 3,
    "st_admin": 4,
    "owner": 5,
}


def get_warn_target(identifier: str):
    """Find a registered account by Telegram ID, Minecraft nick or Telegram username."""
    value = identifier.strip()
    if not value:
        return None
    with db_connect() as conn:
        row = None
        if value.isdigit():
            row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (int(value),)).fetchone()
        if not row:
            row = conn.execute("SELECT * FROM users WHERE minecraft_nickname = ? COLLATE NOCASE", (value,)).fetchone()
        if not row:
            row = conn.execute(
                "SELECT * FROM users WHERE lower(ltrim(telegram_username, '@')) = ?",
                (normalize_username(value),)
            ).fetchone()
        if not row:
            return None
        return {
            "telegram_id": int(row["telegram_id"]),
            "username": row["telegram_username"] or "",
            "minecraft": row["minecraft_nickname"] or "",
            "rank": row["rank"],
        }


def refresh_warn_count(conn, telegram_id: int, now: datetime | None = None) -> int:
    now = now or moscow_now()
    now_iso = moscow_iso(now)
    conn.execute(
        "UPDATE warn_history SET status = 'expired' "
        "WHERE telegram_id = ? AND status = 'active' "
        "AND expires_at IS NOT NULL AND expires_at <= ?",
        (telegram_id, now_iso)
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM warn_history WHERE telegram_id = ? AND status = 'active'",
        (telegram_id,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO warns(telegram_id, warn_count, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(telegram_id) DO UPDATE SET warn_count = excluded.warn_count, "
        "updated_at = excluded.updated_at",
        (telegram_id, count, now_iso)
    )
    return int(count)


async def warn_expiration_worker():
    while True:
        try:
            with db_connect() as conn:
                ids = [r[0] for r in conn.execute(
                    "SELECT DISTINCT telegram_id FROM warn_history WHERE status = 'active' "
                    "AND expires_at IS NOT NULL"
                ).fetchall()]
                for telegram_id in ids:
                    refresh_warn_count(conn, int(telegram_id))
                conn.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Warn expiration worker failed")
        await asyncio.sleep(60)


def get_rest_session(telegram_id: int):
    with db_connect() as conn:
        return conn.execute(
            """
            SELECT *
            FROM rest_sessions
            WHERE telegram_id = ?
            """,
            (telegram_id,)
        ).fetchone()


def save_rest_session(
    telegram_id: int,
    mode: str,
    step: str,
    prompt_message_id: int,
    data: dict
):
    with db_connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO rest_sessions
            (
                telegram_id,
                mode,
                step,
                prompt_message_id,
                data_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                mode,
                step,
                prompt_message_id,
                json.dumps(data, ensure_ascii=False)
            )
        )
        conn.commit()


def delete_rest_session(telegram_id: int):
    with db_connect() as conn:
        conn.execute(
            """
            DELETE FROM rest_sessions
            WHERE telegram_id = ?
            """,
            (telegram_id,)
        )
        conn.commit()


# =====================================================
# REST PERIOD HELPERS
# =====================================================

def activate_rest(
    conn,
    telegram_id: int,
    original_rank: str,
    end_time: datetime,
    request_id: int
):
    now = moscow_now()

    conn.execute(
        """
        UPDATE users
        SET rank = 'rest'
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    )

    conn.execute(
        """
        INSERT OR REPLACE INTO rest_periods
        (
            telegram_id,
            original_rank,
            started_at,
            ends_at,
            request_id
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            telegram_id,
            original_rank,
            moscow_iso(now),
            moscow_iso(end_time),
            request_id
        )
    )


def restore_rest(conn, telegram_id: int):
    period = conn.execute(
        """
        SELECT *
        FROM rest_periods
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    ).fetchone()

    if not period:
        return None

    original_rank = period["original_rank"]

    conn.execute(
        """
        UPDATE users
        SET rank = ?
        WHERE telegram_id = ?
        AND rank = 'rest'
        """,
        (original_rank, telegram_id)
    )

    conn.execute(
        """
        DELETE FROM rest_periods
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    )

    return original_rank


# =====================================================
# LEGACY TRACKING AND COMMAND ACCESS
# =====================================================

class LegacyAccessMiddleware(BaseMiddleware):
    """Отмечает замеченных участников основного чата и блокирует legacy-команды."""

    async def __call__(self, handler, event, data):
        if not isinstance(event, Message) or not event.from_user:
            return await handler(event, data)

        user = event.from_user

        # Постоянно заблокированным пользователям не даём пользоваться
        # ботом ни через команды, ни через обычные сообщения.
        if not user.is_bot and is_banned(user.id):
            if event.chat.type == "private":
                account_row = get_user(user.id)
                if account_row and account_row["rank"] == "warnbanned":
                    await event.reply(
                        "Вы были забанены в CloverTeam навсегда\n\n"
                        "Причина: Достижение максимального количества варнов"
                    )
                else:
                    await event.reply(
                        "Администрация запретила вам писать анкеты и пользоваться ботом."
                    )
            return None

        if (
            MAIN_CHAT_ID
            and event.chat.id == MAIN_CHAT_ID
            and not user.is_bot
        ):
            ensure_legacy_user(user.id, user.username or "")

        text = event.text or event.caption or ""
        if text.startswith("/"):
            row = get_user(user.id)
            if row and row["rank"] == "legacy":
                command_token = text.split(maxsplit=1)[0]
                command_name = command_token.split("@", 1)[0].lstrip("/").lower()

                if command_name != "reg":
                    await event.reply(
                        "Ваш статус — <b>legacy</b>.\n\n"
                        "Для активации аккаунта доступна только команда:\n"
                        "<code>/reg MinecraftNick</code>"
                    )
                    return None

        return await handler(event, data)


async def track_legacy_chat_member(event: ChatMemberUpdated):
    if not MAIN_CHAT_ID or event.chat.id != MAIN_CHAT_ID:
        return

    member = event.new_chat_member
    user = member.user
    if user.is_bot:
        return

    is_member = member.status in {"creator", "administrator", "member"}
    if member.status == "restricted":
        is_member = bool(getattr(member, "is_member", False))

    if is_member:
        ensure_legacy_user(user.id, user.username or "")


# =====================================================
# REGISTRATION CHECK
# =====================================================

async def require_registered(message: Message):

    if not message.from_user:
        return None

    telegram_id = message.from_user.id

    if is_banned(telegram_id):
        await message.reply(
            "Администрация ограничила доступ к командам бота."
        )
        return None

    account = get_account(telegram_id)

    if not account:
        await message.reply(
            "Вы не зарегистрированы в базе данных.\n\n"
            "Для начала подайте заявку через /start, "
            "затем используйте /reg."
        )
        return None

    return account


# =====================================================
# /START
# =====================================================

async def cmd_start(message: Message):

    if not message.from_user:
        return

    if message.chat.type != "private":
        try:
            await message.bot.send_message(
                message.from_user.id,
                "Для подачи заявки напишите мне в личные сообщения: /start"
            )
        except Exception:
            await message.reply(
                "Сначала откройте личные сообщения с ботом "
                "и отправьте ему /start."
            )
        return

    telegram_id = message.from_user.id

    if is_banned(telegram_id):
        await message.reply(
            "Администрация ограничила доступ к анкетам "
            "и командам бота."
        )
        return

    if get_user(telegram_id):
        await message.reply(
            "Вы уже зарегистрированы на данный момент."
        )
        return

    with db_connect() as conn:

        pending = conn.execute(
            """
            SELECT id
            FROM applications
            WHERE telegram_id = ?
            AND status IN ('awaiting_reply', 'pending')
            ORDER BY id DESC
            LIMIT 1
            """,
            (telegram_id,)
        ).fetchone()

        if pending:
            await message.reply(
                "Ваша заявка уже отправлена "
                "или ожидает заполнения."
            )
            return

        cur = conn.execute(
            """
            INSERT INTO applications
            (
                telegram_id,
                telegram_username,
                status,
                created_at
            )
            VALUES (?, ?, 'awaiting_reply', ?)
            """,
            (
                telegram_id,
                message.from_user.username or "",
                datetime.now(timezone.utc).isoformat(),
            )
        )

        app_id = cur.lastrowid
        conn.commit()

    prompt = await message.reply(APPLICATION_TEXT)

    with db_connect() as conn:
        conn.execute(
            """
            UPDATE applications
            SET prompt_message_id = ?
            WHERE id = ?
            """,
            (prompt.message_id, app_id)
        )
        conn.commit()


# =====================================================
# APPLICATION REPLY
# =====================================================

async def application_reply(message: Message):

    if not message.from_user or not message.text:
        return

    if message.chat.type != "private":
        return

    telegram_id = message.from_user.id

    if is_banned(telegram_id):
        await message.reply(
            "Администрация ограничила доступ к анкетам."
        )
        return

    with db_connect() as conn:
        app = conn.execute(
            """
            SELECT *
            FROM applications
            WHERE telegram_id = ?
            AND status = 'awaiting_reply'
            ORDER BY id DESC
            LIMIT 1
            """,
            (telegram_id,)
        ).fetchone()

    if not app:
        return

    if (
        not message.reply_to_message
        or message.reply_to_message.message_id
        != app["prompt_message_id"]
    ):
        return

    if len(message.text) > 5000:
        await message.reply(
            "Анкета слишком длинная. Максимум 5000 символов."
        )
        return

    if ADMIN_CHAT_ID == 0:
        await message.reply(
            "Ошибка: ADMIN_CHAT_ID не настроен."
        )
        return

    username = (
        "@" + message.from_user.username
        if message.from_user.username
        else "не указан"
    )

    admin_text = (
        "<b>Новая заявка CloverTeam</b>\n\n"
        f"Telegram: {safe_html(username)}\n"
        f"Telegram ID: <code>{telegram_id}</code>\n"
        f"Заявка №: <code>{app['id']}</code>\n\n"
        f"<b>Ответы:</b>\n{safe_html(message.text)}"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=f"app_approve:{app['id']}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"app_reject:{app['id']}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отпиздить",
                    callback_data=f"app_dismiss:{app['id']}"
                )
            ]
        ]
    )

    try:
        sent = await message.bot.send_message(
            ADMIN_CHAT_ID,
            admin_text,
            reply_markup=keyboard
        )

        with db_connect() as conn:
            conn.execute(
                """
                UPDATE applications
                SET application_text = ?,
                    admin_message_id = ?,
                    status = 'pending'
                WHERE id = ?
                """,
                (
                    message.text,
                    sent.message_id,
                    app["id"]
                )
            )
            conn.commit()

        await message.reply(
            "✅ Анкета успешно отправлена администрации!"
        )

    except Exception:
        log.exception("Application forwarding failed")
        await message.reply(
            "❌ Не удалось отправить анкету администрации."
        )


# =====================================================
# /ANKBAN — PERMANENT APPLICATION BAN
# =====================================================

async def cmd_ankban(message: Message) -> bool:
    """Блокирует автора анкеты, на сообщение которой ответила администрация."""
    if (
        message.chat.id != ADMIN_CHAT_ID
        or not message.from_user
        or not message.reply_to_message
        or not message.text
        or not message.text.split(maxsplit=1)[0].split("@", 1)[0].lower() == "/ankban"
    ):
        return False

    if not is_senior_admin(message.from_user.id):
        await message.reply("Команда доступна только st_admin и owner.")
        return True

    replied_message_id = message.reply_to_message.message_id

    with db_connect() as conn:
        app = conn.execute(
            """
            SELECT * FROM applications
            WHERE admin_message_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (replied_message_id,)
        ).fetchone()

        if not app:
            await message.reply(
                "Не удалось найти анкету. Ответьте командой /ankban "
                "непосредственно на сообщение с анкетой в админ-чате."
            )
            return True

        target_id = int(app["telegram_id"])
        username = (app["telegram_username"] or "").strip().lstrip("@")
        created_at = datetime.now(timezone.utc).isoformat()

        # Отдельная запись блокировки не удаляется командой /unreg.
        conn.execute(
            """
            INSERT INTO ank_bans
                (telegram_id, telegram_username, admin_id, application_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                telegram_username = excluded.telegram_username,
                admin_id = excluded.admin_id,
                application_id = excluded.application_id,
                created_at = excluded.created_at
            """,
            (target_id, username, message.from_user.id, app["id"], created_at)
        )

        # В основной таблице также сохраняем ранг banned.
        existing_user = conn.execute(
            "SELECT telegram_id FROM users WHERE telegram_id = ?",
            (target_id,)
        ).fetchone()

        if existing_user:
            conn.execute(
                """
                UPDATE users
                SET telegram_username = ?, rank = 'banned'
                WHERE telegram_id = ?
                """,
                (username, target_id)
            )
        else:
            conn.execute(
                """
                INSERT INTO users
                    (telegram_id, telegram_username, minecraft_nickname, rank)
                VALUES (?, ?, NULL, 'banned')
                """,
                (target_id, username)
            )

        conn.execute(
            "UPDATE applications SET status = 'ank_banned' WHERE id = ?",
            (app["id"],)
        )
        conn.commit()

    try:
        await message.bot.send_message(
            target_id,
            "Администрация запретила вам писать анкеты и пользоваться ботом."
        )
        notification = "Пользователь уведомлён."
    except Exception:
        log.warning("Could not notify ank-banned user %s", target_id, exc_info=True)
        notification = (
            "Не удалось отправить ему личное сообщение "
            "(возможно, пользователь не открывал диалог с ботом)."
        )

    await message.reply(
        "⛔ Пользователь заблокирован.\n"
        f"Telegram ID: <code>{target_id}</code>\n"
        f"Username: <code>{safe_html('@' + username if username else 'не указан')}</code>\n"
        f"Заявка №: <code>{app['id']}</code>\n"
        f"{notification}"
    )
    return True


# =====================================================
# APPLICATION APPROVE / REJECT
# =====================================================

async def application_decision(callback: CallbackQuery):
    if not callback.from_user or not is_senior_admin(callback.from_user.id):
        await callback.answer(
            "Только owner и st_admin могут принимать решения.",
            show_alert=True
        )
        return

    if callback.message and callback.message.chat.id != ADMIN_CHAT_ID:
        await callback.answer("Недоступно в этом чате.", show_alert=True)
        return

    try:
        action, app_id_text = (callback.data or "").split(":", 1)
        app_id = int(app_id_text)
    except (ValueError, AttributeError):
        await callback.answer("Некорректная заявка.", show_alert=True)
        return

    if action not in {"app_approve", "app_reject", "app_dismiss"}:
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    new_status = {
        "app_approve": "approved",
        "app_reject": "rejected",
        "app_dismiss": "dismissed",
    }[action]

    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        app = conn.execute(
            "SELECT * FROM applications WHERE id=? AND status='pending'",
            (app_id,)
        ).fetchone()

        if not app:
            conn.rollback()
            await callback.answer(
                "Заявка уже обработана или не найдена.",
                show_alert=True
            )
            return

        changed = conn.execute(
            "UPDATE applications SET status=? WHERE id=? AND status='pending'",
            (new_status, app_id)
        ).rowcount
        if changed != 1:
            conn.rollback()
            await callback.answer("Заявка уже обработана.", show_alert=True)
            return
        conn.commit()

    actor_username = callback.from_user.username or str(callback.from_user.id)
    actor_label = "@" + actor_username.lstrip("@")
    decision_line = {
        "approved": f"Одобрено {safe_html(actor_label)}",
        "rejected": f"Отклонено {safe_html(actor_label)}",
        "dismissed": f"Отпизжен {safe_html(actor_label)}",
    }[new_status]

    notification = None
    if new_status == "approved":
        notification = (
            "Ваша анкета была принята администрацией.\n\n"
            "Чтобы зарегистрироваться, используйте команду:\n"
            "/reg Minecraft_Nickname\n\n"
            "Введите свой настоящий Minecraft Java-ник."
        )
    elif new_status == "rejected":
        notification = (
            "Ваша анкета была отклонена администрацией. "
            "Спасибо за проявленный интерес."
        )
    else:
        notification = (
            "Ваша анкета была сделана некорректно по мнению администрации. "
            "Повторите попытку позже"
        )

    try:
        await callback.bot.send_message(app["telegram_id"], notification)
    except Exception:
        log.warning(
            "Could not notify applicant %s about decision %s",
            app["telegram_id"], new_status, exc_info=True
        )

    if callback.message:
        updated_text = (callback.message.text or "").rstrip() + "\n\n" + decision_line
        try:
            await callback.message.edit_text(updated_text, reply_markup=None)
        except Exception:
            log.exception("Could not update application admin message %s", app_id)
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
                await callback.message.reply(decision_line)
            except Exception:
                log.exception("Could not append application decision label %s", app_id)

    await callback.answer("Решение сохранено.")




# =====================================================
# /REG
# =====================================================


async def cmd_reg(message: Message):

    if not message.from_user:
        return

    telegram_id = message.from_user.id
    username = message.from_user.username or ""

    if message.chat.type != "private" and message.chat.id != MAIN_CHAT_ID:
        await message.reply(
            "Используйте /reg в личных сообщениях бота "
            "или в основном чате CloverTeam."
        )
        return

    if is_banned(telegram_id):
        await message.reply(
            "Администрация ограничила доступ к анкетам "
            "и командам бота."
        )
        return

    # Фиксируем legacy при вызове команды в основном чате.
    if message.chat.id == MAIN_CHAT_ID:
        ensure_legacy_user(telegram_id, username)

    # При вызове в ЛС проверяем членство в основном чате.
    elif MAIN_CHAT_ID:
        try:
            member = await message.bot.get_chat_member(
                MAIN_CHAT_ID,
                telegram_id
            )

            already_member = (
                member.status in {"creator", "administrator", "member"}
                or (
                    member.status == "restricted"
                    and getattr(member, "is_member", False)
                )
            )

            if already_member:
                ensure_legacy_user(telegram_id, username)

        except Exception:
            log.warning(
                "Could not verify main-chat membership for /reg: telegram_id=%s",
                telegram_id,
                exc_info=True
            )

    parts = (message.text or "").split(maxsplit=1)

    if len(parts) != 2:
        await message.reply(
            "Использование: <code>/reg MinecraftNick</code>"
        )
        return

    nickname = parts[1].strip()

    if not MC_NAME_RE.fullmatch(nickname):
        await message.reply(
            "Данный никнейм некорректен. Используйте 3–16 символов: "
            "латинские буквы, цифры или _."
        )
        return

    existing = get_user(telegram_id)
    legacy = bool(existing and existing["rank"] == "legacy")

    if existing and not legacy:
        await message.reply(
            "Вы уже зарегистрированы на данный момент."
        )
        return

    # Новые участники должны иметь одобренную заявку.
    if not legacy:
        with db_connect() as conn:
            approved = conn.execute(
                """
                SELECT id
                FROM applications
                WHERE telegram_id = ?
                AND status = 'approved'
                ORDER BY id DESC
                LIMIT 1
                """,
                (telegram_id,)
            ).fetchone()

        if not approved:
            await message.reply(
                "У вас нет доступа к этой команде. Сначала подайте анкету "
                "через /start и дождитесь её одобрения."
            )
            return

    # Проверяем, не занят ли Minecraft-ник.
    other = get_user_by_nickname(nickname)

    if other and other["telegram_id"] != telegram_id:
        await message.reply(
            "Этот Minecraft-ник уже привязан к другому аккаунту."
        )
        return

    # Сохраняем регистрацию и проверяем результат ДО сообщения об успехе.
    try:
        with db_connect() as conn:

            if legacy:
                cursor = conn.execute(
                    """
                    UPDATE users
                    SET telegram_username = ?,
                        minecraft_nickname = ?,
                        rank = 'player'
                    WHERE telegram_id = ?
                    AND rank = 'legacy'
                    """,
                    (
                        username,
                        nickname,
                        telegram_id
                    )
                )

                if cursor.rowcount != 1:
                    raise RuntimeError(
                        "Legacy registration UPDATE affected "
                        f"{cursor.rowcount} rows"
                    )

            else:
                cursor = conn.execute(
                    """
                    INSERT INTO users
                    (
                        telegram_id,
                        telegram_username,
                        minecraft_nickname,
                        rank
                    )
                    VALUES (?, ?, ?, 'player')
                    """,
                    (
                        telegram_id,
                        username,
                        nickname
                    )
                )

                if cursor.rowcount != 1:
                    raise RuntimeError(
                        "Registration INSERT did not insert exactly one row"
                    )

            # Проверяем запись непосредственно в той же транзакции.
            saved = conn.execute(
                """
                SELECT telegram_id, minecraft_nickname, rank
                FROM users
                WHERE telegram_id = ?
                """,
                (telegram_id,)
            ).fetchone()

            if (
                not saved
                or saved["minecraft_nickname"] != nickname
                or saved["rank"] != "player"
            ):
                raise RuntimeError(
                    "Registration verification failed"
                )

            # Фиксируем транзакцию только после проверки.
            conn.commit()

    except sqlite3.IntegrityError as error:
        log.warning(
            "Registration integrity error: telegram_id=%s error=%s",
            telegram_id,
            error
        )

        if "minecraft_nickname" in str(error):
            await message.reply(
                "Не удалось сохранить регистрацию: "
                "этот Minecraft-ник уже используется."
            )
        else:
            await message.reply(
                "Не удалось сохранить регистрацию: "
                "возможно, ваш аккаунт уже зарегистрирован."
            )
        return

    except Exception:
        log.exception(
            "Registration failed: telegram_id=%s nickname=%s",
            telegram_id,
            nickname
        )

        await message.reply(
            "Произошла ошибка при сохранении регистрации. "
            "Регистрация не подтверждена. Обратитесь к администрации."
        )
        return

    # Приглашение создаём только после подтверждённого сохранения.
    invite_text = ""

    if message.chat.id == MAIN_CHAT_ID:
        invite_text = (
            "\n\nВы уже состоите в основном чате CloverTeam."
        )

    elif MAIN_CHAT_ID:
        try:
            member = await message.bot.get_chat_member(
                MAIN_CHAT_ID,
                telegram_id
            )

            already_member = (
                member.status in {"creator", "administrator", "member"}
                or (
                    member.status == "restricted"
                    and getattr(member, "is_member", False)
                )
            )

            if not already_member:
                invite = await message.bot.create_chat_invite_link(
                    chat_id=MAIN_CHAT_ID,
                    creates_join_request=True,
                    expire_date=(
                        datetime.now(timezone.utc) + timedelta(hours=24)
                    ),
                    name=f"CloverTeam_{telegram_id}"
                )

                invite_text = (
                    "\n\n🍀 <b>Приглашение в основной чат CloverTeam</b>\n\n"
                    "Перейдите по ссылке ниже и отправьте заявку на вступление:\n"
                    f"{safe_html(invite.invite_link)}\n\n"
                    "⏳ Ссылка действует 24 часа."
                )

            else:
                invite_text = (
                    "\n\nВы уже состоите в основном чате CloverTeam."
                )

        except Exception:
            log.exception(
                "Could not create/check main chat join-request invite"
            )

            invite_text = (
                "\n\nРегистрация сохранена, но не удалось создать ссылку "
                "для вступления. Обратитесь к администрации."
            )

    await message.reply(
        "✅ Регистрация успешно завершена!\n\n"
        f"Telegram ID: <code>{telegram_id}</code>\n"
        f"Minecraft: <code>{safe_html(nickname)}</code>\n"
        "Ранг: <b>player</b>"
        f"{invite_text}"
    )



# =====================================================
# /UNREG
# =====================================================

async def cmd_unreg(message: Message):

    if not message.from_user:
        return

    if message.chat.type != "private":
        await message.reply(
            "Используйте /unreg в личных сообщениях бота."
        )
        return

    if not is_senior_admin(message.from_user.id):
        await message.reply(
            "У вас недостаточно прав для использования этой команды!"
        )
        return

    parts = (message.text or "").split(maxsplit=1)

    if len(parts) != 2:
        await message.reply(
            "Использование:\n"
            "/unreg Telegram_ID\n"
            "/unreg Minecraft_Nickname\n"
            "/unreg @username"
        )
        return

    target = parts[1].strip()

    if not target:
        await message.reply("Укажите пользователя.")
        return

    username = target.lstrip("@")

    with db_connect() as conn:

        user = None

        if target.isdigit():
            user = conn.execute(
                """
                SELECT *
                FROM users
                WHERE telegram_id = ?
                """,
                (int(target),)
            ).fetchone()

            if not user:
                user = conn.execute(
                    """
                    SELECT *
                    FROM users
                    WHERE minecraft_nickname = ? COLLATE NOCASE
                    """,
                    (target,)
                ).fetchone()

        else:
            matches = conn.execute(
                """
                SELECT *
                FROM users
                WHERE minecraft_nickname = ? COLLATE NOCASE
                   OR telegram_username = ? COLLATE NOCASE
                   OR telegram_username = ? COLLATE NOCASE
                """,
                (
                    target,
                    username,
                    "@" + username
                )
            ).fetchall()

            if len(matches) > 1:
                await message.reply(
                    "Найдено несколько совпадений.\n"
                    "Укажите точный Telegram ID пользователя."
                )
                return

            user = matches[0] if matches else None

        if not user:
            await message.reply(
                "Пользователь не найден в базе данных."
            )
            return

        telegram_id = user["telegram_id"]
        nickname = user["minecraft_nickname"]
        telegram_username = user["telegram_username"] or "не указан"
        old_rank = user["rank"]

        # При удалении регистрации очищаем членство/приглашения;
        # если удаляется лидер, удаляется и его клан.
        leader_clans = conn.execute(
            "SELECT id FROM clans WHERE leader_id = ?", (telegram_id,)
        ).fetchall()
        for clan_row in leader_clans:
            conn.execute("DELETE FROM clans WHERE id = ?", (clan_row["id"],))
        conn.execute("DELETE FROM clan_members WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM clan_invites WHERE telegram_id = ?", (telegram_id,))

        conn.execute(
            "DELETE FROM users WHERE telegram_id = ?",
            (telegram_id,)
        )

        conn.execute(
            "DELETE FROM rest_periods WHERE telegram_id = ?",
            (telegram_id,)
        )

        conn.execute(
            "DELETE FROM rest_sessions WHERE telegram_id = ?",
            (telegram_id,)
        )

        conn.execute(
            """
            UPDATE rest_requests
            SET status = 'cancelled'
            WHERE applicant_id = ?
            AND status IN (
                'pending',
                'awaiting_proof',
                'awaiting_rejection_reason'
            )
            """,
            (telegram_id,)
        )

        conn.commit()

    await message.reply(
        "✅ Регистрация пользователя удалена из базы данных.\n\n"
        f"Telegram ID: <code>{telegram_id}</code>\n"
        f"Username: <code>{safe_html(telegram_username)}</code>\n"
        f"Minecraft: <code>{safe_html(nickname)}</code>\n"
        f"Прежний ранг: <code>{safe_html(old_rank)}</code>"
    )

    try:
        await message.bot.send_message(
            telegram_id,
            "Ваша регистрация в CloverTeam была удалена администрацией."
        )
    except Exception:
        log.warning(
            "Could not notify unregistered user: %s",
            telegram_id
        )


# =====================================================
# REST KEYBOARDS
# =====================================================

def rest_start_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Взять рест на себя",
                    callback_data="rest_self"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Взять рест для друга",
                    callback_data="rest_friend"
                )
            ]
        ]
    )


def rest_manage_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Продлить",
                    callback_data="rest_extend"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Снять самостоятельно",
                    callback_data="rest_remove"
                )
            ]
        ]
    )


def rest_confirm_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да",
                    callback_data="rest_remove_yes"
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data="rest_remove_no"
                )
            ]
        ]
    )


def rest_admin_keyboard(request_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=f"rest_approve:{request_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"rest_reject:{request_id}"
                )
            ]
        ]
    )


# =====================================================
# /REST
# =====================================================

async def cmd_rest(message: Message):

    if not message.from_user:
        return

    telegram_id = message.from_user.id

    if message.chat.type != "private":
        try:
            await message.bot.send_message(
                telegram_id,
                "Для использования /rest перейдите в личные сообщения бота."
            )
        except Exception:
            await message.reply(
                "Сначала откройте личные сообщения с ботом "
                "и отправьте ему /rest."
            )
        return

    if is_banned(telegram_id):
        await message.reply(
            "Администрация ограничила доступ к командам бота."
        )
        return

    account = get_account(telegram_id)

    if not account:
        await message.reply(
            "Вы не зарегистрированы в базе данных."
        )
        return

    if get_rest_session(telegram_id):
        await message.reply(
            "У вас уже есть незавершённая анкета. "
            "Завершите её, прежде чем начинать новую."
        )
        return

    if account["rank"] == "rest":
        period = get_rest_period(telegram_id)

        if period:
            end = datetime.fromisoformat(
                period["ends_at"]
            ).astimezone(MOSCOW_TZ)

            await message.reply(
                "Что вы хотите сделать с рестом?\n\n"
                f"Текущий срок окончания: "
                f"<b>{end.strftime('%d.%m.%Y %H:%M')} МСК</b>",
                reply_markup=rest_manage_keyboard()
            )
        else:
            await message.reply(
                "Что вы хотите сделать с рестом?",
                reply_markup=rest_manage_keyboard()
            )

        return

    if account["rank"] not in REST_ELIGIBLE_RANKS:
        await message.reply(
            "Игроки с вашим рангом не могут подавать заявку на рест."
        )
        return

    with db_connect() as conn:
        pending = conn.execute(
            """
            SELECT id
            FROM rest_requests
            WHERE applicant_id = ?
            AND status IN (
                'pending',
                'awaiting_proof',
                'awaiting_rejection_reason'
            )
            ORDER BY id DESC
            LIMIT 1
            """,
            (telegram_id,)
        ).fetchone()

    if pending:
        await message.reply(
            "У вас уже есть заявка на рест, "
            "которая ожидает рассмотрения."
        )
        return

    await message.reply(
        "Как вы хотите подать рест?",
        reply_markup=rest_start_keyboard()
    )


# =====================================================
# REST CALLBACKS: START FORM
# =====================================================

async def rest_start_callback(callback: CallbackQuery):

    if not callback.from_user or not callback.message:
        return

    telegram_id = callback.from_user.id

    if callback.message.chat.type != "private":
        await callback.answer(
            "Используйте кнопки в личных сообщениях с ботом.",
            show_alert=True
        )
        return

    if is_banned(telegram_id):
        await callback.answer(
            "Доступ ограничен.",
            show_alert=True
        )
        return

    account = get_account(telegram_id)

    if not account:
        await callback.answer(
            "Вы не зарегистрированы.",
            show_alert=True
        )
        return

    if get_rest_session(telegram_id):
        await callback.answer(
            "У вас уже есть незавершённая анкета.",
            show_alert=True
        )
        return

    if account["rank"] == "rest":
        await callback.answer(
            "Вы уже находитесь в ресте.",
            show_alert=True
        )
        return

    if account["rank"] not in REST_ELIGIBLE_RANKS:
        await callback.answer(
            "Ваш ранг не позволяет подать заявку.",
            show_alert=True
        )
        return

    action = callback.data

    if action == "rest_self":
        mode = "self"
        text = REST_SELF_TEXT

    elif action == "rest_friend":
        mode = "friend"
        text = REST_FRIEND_TEXT

    else:
        await callback.answer("Неизвестное действие.")
        return

    prompt = await callback.message.reply(text)

    save_rest_session(
        telegram_id,
        mode,
        "form",
        prompt.message_id,
        {}
    )

    await callback.answer()


# =====================================================
# REST: SUBMIT REQUEST
# =====================================================

async def submit_rest_request(
    message: Message,
    mode: str,
    data: dict
):

    if ADMIN_CHAT_ID == 0:
        await message.reply(
            "Ошибка: ADMIN_CHAT_ID не настроен."
        )
        delete_rest_session(message.from_user.id)
        return

    if mode == "self":
        request_type = "self"
        target_id = message.from_user.id
        title = "🍀 <b>Новая заявка на взятие реста</b>"

    elif mode == "friend":
        request_type = "friend"
        target_id = None
        title = "🍀 <b>Новая заявка на взятие реста для друга</b>"

    else:
        return

    target_username = data["username"]
    target_nickname = data["nickname"]

    with db_connect() as conn:

        cur = conn.execute(
            """
            INSERT INTO rest_requests
            (
                applicant_id,
                target_id,
                request_type,
                reason,
                days,
                target_username,
                target_nickname,
                proof_file_id,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                message.from_user.id,
                target_id,
                request_type,
                data["reason"],
                data["days"],
                target_username,
                target_nickname,
                data.get("proof_file_id"),
                moscow_iso(moscow_now())
            )
        )

        request_id = cur.lastrowid
        conn.commit()

    applicant_username = (
        "@" + message.from_user.username
        if message.from_user.username
        else "не указан"
    )

    admin_text = (
        f"{title}\n\n"
        f"<b>Заявка №:</b> <code>{request_id}</code>\n"
        f"<b>Подал:</b> {safe_html(applicant_username)}\n"
        f"<b>Telegram ID подавшего:</b> "
        f"<code>{message.from_user.id}</code>\n\n"
        f"<b>Причина:</b>\n{safe_html(data['reason'])}\n\n"
        f"<b>Срок:</b> {data['days']} дн.\n"
        f"<b>Telegram игрока:</b> "
        f"{safe_html('@' + target_username)}\n"
        f"<b>Minecraft:</b> "
        f"<code>{safe_html(target_nickname)}</code>"
    )

    try:
        if mode == "friend":
            sent = await message.bot.send_photo(
                ADMIN_CHAT_ID,
                data["proof_file_id"],
                caption=admin_text,
                message_thread_id=REST_THREAD_ID or None,
                reply_markup=rest_admin_keyboard(request_id)
            )
        else:
            sent = await message.bot.send_message(
                ADMIN_CHAT_ID,
                admin_text,
                message_thread_id=REST_THREAD_ID or None,
                reply_markup=rest_admin_keyboard(request_id)
            )

        with db_connect() as conn:
            conn.execute(
                """
                UPDATE rest_requests
                SET admin_message_id = ?
                WHERE id = ?
                """,
                (sent.message_id, request_id)
            )
            conn.commit()

        delete_rest_session(message.from_user.id)

        await message.reply(
            "✅ Ваша заявка успешно отправлена администрации!\n"
            "Ожидайте решения в личных сообщениях бота."
        )

    except Exception:
        log.exception("Rest request forwarding failed")

        await message.reply(
            "Не удалось отправить заявку администрации. "
            "Попробуйте позже или обратитесь к администрации."
        )


# =====================================================
# REST: PRIVATE FORM REPLIES
# =====================================================

async def rest_private_input(message: Message) -> bool:

    if (
        not message.from_user
        or message.chat.type != "private"
    ):
        return False

    session = get_rest_session(message.from_user.id)

    if not session:
        return False

    if (
        not message.reply_to_message
        or message.reply_to_message.message_id
        != session["prompt_message_id"]
    ):
        return False

    telegram_id = message.from_user.id

    try:
        data = json.loads(session["data_json"] or "{}")
    except json.JSONDecodeError:
        data = {}

    mode = session["mode"]
    step = session["step"]

    # -------------------------------------------------
    # Основная анкета
    # -------------------------------------------------

    if step == "form":

        if not message.text:
            await message.reply(
                "Пожалуйста, отправьте ответы текстом "
                "одним сообщением в ответ на анкету."
            )
            return True

        values = parse_rest_lines(message.text, 4)

        if not values:
            await message.reply(
                "Не удалось распознать анкету.\n\n"
                "Укажите ровно 4 пункта, каждый с новой строки."
            )
            return True

        reason, days_text, username, nickname = values

        if len(reason) > 1000:
            await message.reply(
                "Причина слишком длинная. Максимум 1000 символов."
            )
            return True

        if not days_text.isdigit():
            await message.reply(
                "Количество дней необходимо указать числом."
            )
            return True

        days = int(days_text)

        if days < 1:
            await message.reply(
                "Нельзя указать 0 дней. Минимальный срок реста — 1 день."
            )
            return True

        if days > REST_MAX_DAYS:
            await message.reply(
                f"Максимальный срок реста — {REST_MAX_DAYS} дней."
            )
            return True

        username = normalize_username(username)

        if not re.fullmatch(r"[a-zA-Z0-9_]{5,32}", username):
            await message.reply(
                "Укажите корректный Telegram username без символа @."
            )
            return True

        if not MC_NAME_RE.fullmatch(nickname):
            await message.reply(
                "Minecraft-ник указан некорректно."
            )
            return True

        data = {
            "reason": reason,
            "days": days,
            "username": username,
            "nickname": nickname
        }

        if mode == "self":

            prompt = await message.reply(
                "Проверяю данные вашей регистрации..."
            )

            account = get_account(telegram_id)

            if not account:
                await prompt.edit_text(
                    "Не удалось найти вашу регистрацию."
                )
                delete_rest_session(telegram_id)
                return True

            actual_username = normalize_username(
                account["telegram"]
            )

            current_username = normalize_username(
                message.from_user.username or ""
            )

            if (
                username != current_username
                or username != actual_username
                or nickname.lower() != account["minecraft"].lower()
            ):
                await prompt.edit_text(
                    "Данные не совпадают с вашей регистрацией.\n\n"
                    "Проверьте Telegram username и Minecraft-ник."
                )
                return True

            await submit_rest_request(
                message,
                "self",
                data
            )
            return True

        if mode == "friend":

            data["proof_file_id"] = None

            prompt = await message.reply(
                "Теперь отправьте скриншот доказательства "
                "и разрешения вашего друга.\n\n"
                "Прикрепите изображение и отправьте его "
                "ответом на это сообщение."
            )

            save_rest_session(
                telegram_id,
                mode,
                "proof",
                prompt.message_id,
                data
            )
            return True

    # -------------------------------------------------
    # Скриншот друга
    # -------------------------------------------------

    if step == "proof":

        if not message.photo:
            await message.reply(
                "Для заявки на друга необходимо отправить "
                "скриншот изображением."
            )
            return True

        data["proof_file_id"] = message.photo[-1].file_id

        await submit_rest_request(
            message,
            "friend",
            data
        )
        return True

    # -------------------------------------------------
    # Продление
    # -------------------------------------------------

    if step == "extend":

        if not message.text:
            await message.reply(
                "Отправьте ответы текстом."
            )
            return True

        values = parse_rest_lines(message.text, 2)

        if not values:
            await message.reply(
                "Укажите ровно два пункта, каждый с новой строки."
            )
            return True

        date_text, reason = values
        requested_date = parse_date(date_text)

        if not requested_date:
            await message.reply(
                "Неверный формат даты.\n"
                "Используйте ДД.ММ.ГГГГ, например 25.12.2026."
            )
            return True

        if requested_date <= moscow_now().date():
            await message.reply(
                "Дата продления должна быть в будущем."
            )
            return True

        if not reason or len(reason) > 1000:
            await message.reply(
                "Причина должна содержать не более 1000 символов."
            )
            return True

        account = get_account(telegram_id)

        if not account or account["rank"] != "rest":
            await message.reply(
                "Вы больше не находитесь в ресте."
            )
            delete_rest_session(telegram_id)
            return True

        period = get_rest_period(telegram_id)

        if not period:
            await message.reply(
                "Не удалось найти срок вашего реста."
            )
            delete_rest_session(telegram_id)
            return True

        old_end = datetime.fromisoformat(
            period["ends_at"]
        ).astimezone(MOSCOW_TZ)

        requested_end = datetime.combine(
            requested_date,
            time(23, 59, 59),
            tzinfo=MOSCOW_TZ
        )

        if requested_end <= old_end:
            await message.reply(
                "Новая дата должна быть позже текущей даты окончания реста.\n"
                f"Сейчас рест заканчивается: "
                f"{old_end.strftime('%d.%m.%Y %H:%M')} МСК."
            )
            return True

        if ADMIN_CHAT_ID == 0:
            await message.reply(
                "Ошибка: ADMIN_CHAT_ID не настроен."
            )
            return True

        with db_connect() as conn:

            pending = conn.execute(
                """
                SELECT id
                FROM rest_requests
                WHERE applicant_id = ?
                AND request_type = 'extend'
                AND status = 'pending'
                """,
                (telegram_id,)
            ).fetchone()

            if pending:
                await message.reply(
                    "У вас уже есть заявка на продление, "
                    "ожидающая рассмотрения."
                )
                return True

            cur = conn.execute(
                """
                INSERT INTO rest_requests
                (
                    applicant_id,
                    target_id,
                    request_type,
                    reason,
                    target_username,
                    target_nickname,
                    requested_until,
                    status,
                    created_at
                )
                VALUES (?, ?, 'extend', ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    telegram_id,
                    telegram_id,
                    reason,
                    message.from_user.username or "",
                    account["minecraft"],
                    moscow_iso(requested_end),
                    moscow_iso(moscow_now())
                )
            )

            request_id = cur.lastrowid
            conn.commit()

        username = (
            "@" + message.from_user.username
            if message.from_user.username
            else "не указан"
        )

        admin_text = (
            "🍀 <b>Заявка на продление реста</b>\n\n"
            f"<b>Заявка №:</b> <code>{request_id}</code>\n"
            f"<b>Игрок:</b> {safe_html(username)}\n"
            f"<b>Telegram ID:</b> <code>{telegram_id}</code>\n"
            f"<b>Minecraft:</b> "
            f"<code>{safe_html(account['minecraft'])}</code>\n\n"
            f"<b>Продлить до:</b> "
            f"{requested_date.strftime('%d.%m.%Y')} 23:59 МСК\n\n"
            f"<b>Причина:</b>\n{safe_html(reason)}"
        )

        try:
            sent = await message.bot.send_message(
                ADMIN_CHAT_ID,
                admin_text,
                message_thread_id=REST_THREAD_ID or None,
                reply_markup=rest_admin_keyboard(request_id)
            )

            with db_connect() as conn:
                conn.execute(
                    """
                    UPDATE rest_requests
                    SET admin_message_id = ?
                    WHERE id = ?
                    """,
                    (sent.message_id, request_id)
                )
                conn.commit()

            delete_rest_session(telegram_id)

            await message.reply(
                "✅ Заявка на продление реста отправлена администрации."
            )

        except Exception:
            log.exception("Rest extension forwarding failed")
            await message.reply(
                "Не удалось отправить заявку администрации."
            )

        return True

    return False


# =====================================================
# REST: MANAGE CALLBACKS
# =====================================================

async def rest_manage_callback(callback: CallbackQuery):

    if not callback.from_user or not callback.message:
        return

    telegram_id = callback.from_user.id
    account = get_account(telegram_id)

    if not account or account["rank"] != "rest":
        await callback.answer(
            "Эта функция доступна только игрокам в ресте.",
            show_alert=True
        )
        return

    if callback.data == "rest_extend":

        if get_rest_session(telegram_id):
            await callback.answer(
                "У вас уже есть незавершённая анкета.",
                show_alert=True
            )
            return

        prompt = await callback.message.reply(
            REST_EXTEND_TEXT
        )

        save_rest_session(
            telegram_id,
            "extend",
            "extend",
            prompt.message_id,
            {}
        )

        await callback.answer()
        return

    if callback.data == "rest_remove":

        await callback.message.reply(
            "Вы уверены, что хотите снять рест?",
            reply_markup=rest_confirm_keyboard()
        )

        await callback.answer()
        return

    if callback.data == "rest_remove_yes":

        with db_connect() as conn:
            original_rank = restore_rest(
                conn,
                telegram_id
            )
            conn.commit()

        if original_rank is None:
            await callback.answer(
                "Вы уже не находитесь в ресте.",
                show_alert=True
            )
            return

        await callback.message.edit_reply_markup(
            reply_markup=None
        )

        await callback.message.reply(
            "Вы сняли свой рест. Добро пожаловать обратно!\n"
            f"Ваш восстановленный ранг: <b>{safe_html(original_rank)}</b>"
        )

        await callback.answer("Рест снят.")
        return

    if callback.data == "rest_remove_no":

        await callback.message.edit_reply_markup(
            reply_markup=None
        )

        await callback.message.reply(
            "Что вы хотите сделать с рестом?",
            reply_markup=rest_manage_keyboard()
        )

        await callback.answer()
        return


# =====================================================
# REST: ADMIN APPROVE / REJECT
# =====================================================

async def rest_admin_decision(callback: CallbackQuery):

    if (
        not callback.from_user
        or not is_senior_admin(callback.from_user.id)
    ):
        await callback.answer(
            "Только owner и st_admin могут принимать решения.",
            show_alert=True
        )
        return

    if not callback.message:
        await callback.answer()
        return

    if callback.message.chat.id != ADMIN_CHAT_ID:
        await callback.answer(
            "Недоступно в этом чате.",
            show_alert=True
        )
        return

    try:
        action, request_id_text = (
            callback.data or ""
        ).split(":", 1)

        request_id = int(request_id_text)

    except (ValueError, AttributeError):
        await callback.answer(
            "Некорректная заявка.",
            show_alert=True
        )
        return

    if action not in {
        "rest_approve",
        "rest_reject"
    }:
        await callback.answer(
            "Неизвестное действие.",
            show_alert=True
        )
        return

    with db_connect() as conn:

        request = conn.execute(
            """
            SELECT *
            FROM rest_requests
            WHERE id = ?
            AND status = 'pending'
            """,
            (request_id,)
        ).fetchone()

        if not request:
            await callback.answer(
                "Заявка уже обработана или не найдена.",
                show_alert=True
            )
            return

        if action == "rest_reject":

            prompt = await callback.message.reply(
                "По какой причине отклоняете рест?\n\n"
                "Ответьте текстом на это сообщение."
            )

            conn.execute(
                """
                UPDATE rest_requests
                SET status = 'awaiting_rejection_reason',
                    reject_prompt_message_id = ?,
                    reject_admin_id = ?
                WHERE id = ?
                AND status = 'pending'
                """,
                (
                    prompt.message_id,
                    callback.from_user.id,
                    request_id
                )
            )

            conn.commit()

            await callback.answer(
                "Ожидаю причину отклонения."
            )
            return

        request_type = request["request_type"]
        target_id = None
        target_user = None
        original_rank = None

        if request_type == "self":

            target_id = request["applicant_id"]

            target_user = conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?",
                (target_id,)
            ).fetchone()

            if not target_user:
                await callback.answer(
                    "Такого игрока не существует. "
                    "Проверьте свои данные.",
                    show_alert=True
                )
                return

            actual_nickname = (
                target_user["minecraft_nickname"]
                if isinstance(target_user, dict)
                else target_user["minecraft_nickname"]
            )

            actual_username = normalize_username(
                target_user["telegram_username"] or ""
            )

            if (
                actual_nickname.lower()
                != request["target_nickname"].lower()
                or actual_username
                != normalize_username(request["target_username"] or "")
            ):
                await callback.answer(
                    "Данные игрока не совпадают с регистрацией.",
                    show_alert=True
                )
                return

            original_rank = target_user["rank"]

        elif request_type == "friend":

            target_username = normalize_username(
                request["target_username"] or ""
            )

            target_nickname = request["target_nickname"] or ""

            target_user = conn.execute(
                """
                SELECT *
                FROM users
                WHERE minecraft_nickname = ? COLLATE NOCASE
                AND LOWER(REPLACE(telegram_username, '@', '')) = ?
                """,
                (
                    target_nickname,
                    target_username
                )
            ).fetchone()

            if not target_user:
                await callback.answer(
                    "Такого игрока не существует. "
                    "Проверьте его данные.",
                    show_alert=True
                )

                try:
                    await callback.bot.send_message(
                        request["applicant_id"],
                        "Такого игрока не существует. "
                        "Проверьте его данные."
                    )
                except Exception:
                    pass

                return

            target_id = target_user["telegram_id"]
            original_rank = target_user["rank"]

        elif request_type == "extend":

            target_id = request["applicant_id"]

            target_user = get_account(target_id)

            if not target_user or target_user["rank"] != "rest":
                await callback.answer(
                    "Игрок больше не находится в ресте.",
                    show_alert=True
                )
                return

            period = conn.execute(
                """
                SELECT *
                FROM rest_periods
                WHERE telegram_id = ?
                """,
                (target_id,)
            ).fetchone()

            if not period:
                await callback.answer(
                    "Не найден действующий срок реста.",
                    show_alert=True
                )
                return

            end_time = datetime.fromisoformat(
                request["requested_until"]
            ).astimezone(MOSCOW_TZ)

            old_end = datetime.fromisoformat(
                period["ends_at"]
            ).astimezone(MOSCOW_TZ)

            if end_time <= old_end:
                await callback.answer(
                    "Новая дата уже не позже текущей даты окончания.",
                    show_alert=True
                )
                return

            conn.execute(
                """
                UPDATE rest_periods
                SET ends_at = ?,
                    request_id = ?
                WHERE telegram_id = ?
                """,
                (
                    moscow_iso(end_time),
                    request_id,
                    target_id
                )
            )

        else:
            await callback.answer(
                "Неизвестный тип заявки.",
                show_alert=True
            )
            return

        if request_type in {"self", "friend"}:

            if original_rank not in REST_ELIGIBLE_RANKS:
                await callback.answer(
                    "Игрок не может получить рест с текущим рангом.",
                    show_alert=True
                )
                return

            if get_rest_period(target_id):
                await callback.answer(
                    "Игрок уже находится в ресте.",
                    show_alert=True
                )
                return

            now = moscow_now()

            end_time = now + timedelta(
                days=int(request["days"])
            )

            activate_rest(
                conn,
                target_id,
                original_rank,
                end_time,
                request_id
            )

        conn.execute(
            """
            UPDATE rest_requests
            SET status = 'approved',
                target_id = ?
            WHERE id = ?
            """,
            (target_id, request_id)
        )

        conn.commit()

    try:

        if request_type == "extend":
            notification = (
                "Ваша заявка на продление реста была принята.\n\n"
                f"Теперь ваш рест действует до "
                f"{end_time.strftime('%d.%m.%Y %H:%M')} МСК."
            )
        else:
            notification = (
                "Ваша заявка на рест была принята.\n\n"
                f"Теперь вы находитесь в ресте до "
                f"{end_time.strftime('%d.%m.%Y %H:%M')} МСК."
            )

        await callback.bot.send_message(
            target_id,
            notification
        )

        notify_label = "Игрок уведомлён."

    except Exception:
        log.exception(
            "Could not notify rest user: %s",
            target_id
        )
        notify_label = (
            "Не удалось отправить уведомление игроку."
        )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.reply(
        "✅ Заявка одобрена.\n"
        f"{notify_label}"
    )

    await callback.answer("Заявка одобрена.")


# =====================================================
# REST: REJECTION REASON
# =====================================================

async def rest_rejection_reply(message: Message) -> bool:

    if (
        not message.from_user
        or message.chat.id != ADMIN_CHAT_ID
        or not message.reply_to_message
        or not message.text
    ):
        return False

    if not is_senior_admin(message.from_user.id):
        return False

    reason = message.text.strip()

    if not reason:
        await message.reply(
            "Причина не может быть пустой."
        )
        return True

    if len(reason) > 1500:
        await message.reply(
            "Причина слишком длинная. Максимум 1500 символов."
        )
        return True

    with db_connect() as conn:

        request = conn.execute(
            """
            SELECT *
            FROM rest_requests
            WHERE reject_prompt_message_id = ?
            AND reject_admin_id = ?
            AND status = 'awaiting_rejection_reason'
            """,
            (
                message.reply_to_message.message_id,
                message.from_user.id
            )
        ).fetchone()

        if not request:
            return False

        conn.execute(
            """
            UPDATE rest_requests
            SET status = 'rejected'
            WHERE id = ?
            AND status = 'awaiting_rejection_reason'
            """,
            (request["id"],)
        )

        conn.commit()

    # При отклонении уведомляем заявителя.
    try:
        await message.bot.send_message(
            request["applicant_id"],
            "Вашу заявку на рест отклонили.\n\n"
            f"Причина: {reason}"
        )

        result = "Заявка отклонена. Заявитель уведомлён."

    except Exception:
        log.exception(
            "Could not notify rejected rest applicant"
        )

        result = (
            "Заявка отклонена, "
            "но уведомить заявителя не удалось."
        )

    await message.reply(result)

    return True


# =====================================================
# REST EXPIRATION WORKER
# =====================================================

async def rest_expiration_worker(bot: Bot):

    while True:

        try:
            now = moscow_now()

            with db_connect() as conn:

                expired = conn.execute(
                    """
                    SELECT *
                    FROM rest_periods
                    WHERE ends_at <= ?
                    """,
                    (moscow_iso(now),)
                ).fetchall()

                restored = []

                for item in expired:

                    original_rank = restore_rest(
                        conn,
                        item["telegram_id"]
                    )

                    if original_rank is not None:
                        restored.append({
                            "telegram_id": item["telegram_id"],
                            "original_rank": original_rank
                        })

                conn.commit()

            for item in restored:

                try:
                    await bot.send_message(
                        item["telegram_id"],
                        "Срок вашего реста закончился.\n\n"
                        "Ваш прежний ранг восстановлен: "
                        f"<b>{safe_html(item['original_rank'])}</b>.\n"
                        "Добро пожаловать обратно в CloverTeam!"
                    )

                except Exception:
                    log.warning(
                        "Could not notify expired rest user: %s",
                        item["telegram_id"]
                    )

        except Exception:
            log.exception(
                "Rest expiration worker error"
            )

        await asyncio.sleep(60)


# =====================================================
# RCON
# =====================================================

def create_rcon_packet(
    request_id: int,
    packet_type: int,
    payload: str
) -> bytes:

    encoded = payload.encode("utf-8") + b"\x00\x00"
    length = 4 + 4 + len(encoded)

    return (
        struct.pack("<i", length)
        + struct.pack("<i", request_id)
        + struct.pack("<i", packet_type)
        + encoded
    )


async def read_rcon_packet(reader: asyncio.StreamReader):

    length_data = await reader.readexactly(4)
    length = struct.unpack("<i", length_data)[0]

    if length < 10 or length > 4_194_304:
        raise RuntimeError(
            f"Некорректный размер RCON-пакета: {length}"
        )

    packet_data = await reader.readexactly(length)

    if len(packet_data) < 10:
        raise RuntimeError("Неполный RCON-пакет")

    request_id, packet_type = struct.unpack(
        "<ii",
        packet_data[:8]
    )

    payload = packet_data[8:-2].decode(
        "utf-8",
        errors="replace"
    )

    return request_id, packet_type, payload


async def rcon_sync(command: str) -> str:

    if not RCON_HOST or not RCON_PASSWORD:
        raise RuntimeError("RCON configuration is missing")

    reader = None
    writer = None

    try:
        reader, writer = await asyncio.open_connection(
            RCON_HOST,
            RCON_PORT
        )

        writer.write(
            create_rcon_packet(
                1,
                3,
                RCON_PASSWORD
            )
        )
        await writer.drain()

        auth_id, auth_type, auth_response = (
            await read_rcon_packet(reader)
        )

        if auth_id == -1:
            raise RuntimeError("RCON authentication failed")

        if auth_id != 1:
            raise RuntimeError(
                "Invalid RCON authentication response"
            )

        writer.write(
            create_rcon_packet(
                2,
                2,
                command
            )
        )
        await writer.drain()

        response_parts = []

        while True:
            response_id, response_type, response = (
                await read_rcon_packet(reader)
            )

            if response_id != 2:
                raise RuntimeError(
                    f"Invalid RCON response ID: {response_id}"
                )

            response_parts.append(response)

            if len(response) < 4096:
                break

        return "".join(response_parts)

    finally:
        if writer:
            writer.close()

            try:
                await writer.wait_closed()
            except Exception:
                pass


async def rcon_command(command: str) -> str:
    return await asyncio.wait_for(
        rcon_sync(command),
        timeout=15
    )


# =====================================================
# /WARN — PLAYER WARNINGS
# =====================================================

async def cmd_warn(message: Message):
    if not message.from_user:
        return

    actor_id = message.from_user.id
    actor = get_account(actor_id)
    if not actor:
        await message.reply("Ваш аккаунт не найден или недоступен.")
        return

    actor_rank = actor.get("rank")
    if actor_rank == "eventor" or actor_rank not in WARN_RANK_LEVEL and actor_rank != "owner":
        await message.reply("У вас недостаточно прав для использования этой команды.")
        return
    if actor_rank not in {"moder", "admin", "tech_admin", "st_admin", "owner"}:
        await message.reply("Выдавать варны могут администраторы ранга moder и выше (eventor исключён).")
        return

    args = extract_command(message)
    parts = args.split(maxsplit=2)
    if len(parts) != 3:
        await message.reply(
            "Использование:\n<code>/warn &lt;MinecraftNick/TG username/TG ID&gt; &lt;дни&gt; &lt;причина&gt;</code>\n\n"
            "Укажите 0 для перманентного варна."
        )
        return

    target_key, days_raw, reason = parts
    if not days_raw.isdigit():
        await message.reply("Количество дней должно быть целым неотрицательным числом.")
        return
    days = int(days_raw)
    reason = reason.strip()
    if not reason or len(reason) > 350 or any(c in reason for c in "\r\n\x00"):
        await message.reply("Причина должна быть одной строкой длиной до 350 символов.")
        return

    target = get_warn_target(target_key)
    if not target:
        await message.reply("Игрок не найден. Он должен быть зарегистрирован в CloverTeam.")
        return
    target_id = target["telegram_id"]
    target_rank = target.get("rank", "player")
    if target_rank in {"banned", "warnbanned"}:
        await message.reply("Этот пользователь уже заблокирован.")
        return

    # Уровень admin и tech_admin одинаковый. Нельзя варнить равного или старшего.
    if actor_rank != "owner":
        actor_level = WARN_RANK_LEVEL.get(actor_rank, -1)
        target_level = WARN_RANK_LEVEL.get(target_rank, -1)
        if target_rank in {"eventor", "rest"}:
            target_level = WARN_RANK_LEVEL.get(
                get_account(target_id).get("original_rank") if get_account(target_id) else "", -1
            ) if target_rank == "rest" else WARN_RANK_LEVEL.get("moder", 2)
        if target_level >= actor_level:
            await message.reply(
                "Вы не можете выдать варн администратору выше своего ранга/одному рангу."
            )
            return

    if not target["minecraft"] or not MC_NAME_RE.fullmatch(target["minecraft"]):
        await message.reply("У пользователя не указан корректный Minecraft-ник.")
        return

    now = moscow_now()
    expires_at = None if days == 0 else moscow_iso(now + timedelta(days=days))
    issued_at = moscow_iso(now)

    try:
        with db_connect() as conn:
            count = refresh_warn_count(conn, target_id, now)
            conn.execute(
                "INSERT INTO warn_history "
                "(telegram_id, admin_id, reason, days, issued_at, expires_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active')",
                (target_id, actor_id, reason, days, issued_at, expires_at)
            )
            count = refresh_warn_count(conn, target_id, now)
            should_ban = count >= 3

            if should_ban:
                conn.execute(
                    "UPDATE users SET rank = 'warnbanned' WHERE telegram_id = ?",
                    (target_id,)
                )
                conn.commit()
            else:
                conn.commit()
    except Exception:
        log.exception("Could not store warn")
        await message.reply("Не удалось сохранить варн в базе данных.")
        return

    if should_ban:
        # Гарантируем, что запись пользователя остаётся в users.
        with db_connect() as conn:
            conn.execute(
                "INSERT INTO users(telegram_id, telegram_username, minecraft_nickname, rank) "
                "VALUES (?, ?, ?, 'warnbanned') "
                "ON CONFLICT(telegram_id) DO UPDATE SET rank = 'warnbanned'",
                (target_id, target["username"], target["minecraft"] or None)
            )
            conn.commit()

        server_results = []
        try:
            await rcon_command(
                "ban " + target["minecraft"] + " " +
                "Достижение максимального количества варнов"
            )
            server_results.append("бан Minecraft выполнен")
        except Exception:
            log.exception("Minecraft ban via RCON failed for %s", target["minecraft"])
            server_results.append("ошибка Minecraft-бана")
        for command in (f"swl remove {target['minecraft']}", "swl reload"):
            try:
                await rcon_command(command)
                server_results.append(f"{command}: OK")
            except Exception:
                log.exception("RCON command failed: %s", command)
                server_results.append(f"{command}: ошибка")

        try:
            await message.bot.send_message(
                target_id,
                "Вы были забанены в CloverTeam навсегда\n\n"
                "Причина: Достижение максимального количества варнов"
            )
            dm_result = "Личное уведомление отправлено."
        except Exception:
            dm_result = "Не удалось отправить ЛС (пользователь мог не открыть диалог с ботом)."

        await message.reply(
            "⛔ Достигнут лимит варнов: пользователь заблокирован.\n"
            f"Игрок: <code>{safe_html(target['minecraft'])}</code>\n"
            f"Активных варнов: <b>{count}</b>\n"
            f"{safe_html(dm_result)}\n"
            f"RCON: {safe_html('; '.join(server_results))}"
        )
        return

    # Команда плагина получает причину в Base64 URL-safe, чтобы пробелы и символы
    # причины не ломали аргументы RCON.
    import base64
    encoded_reason = base64.urlsafe_b64encode(reason.encode("utf-8")).decode("ascii").rstrip("=")
    duration_arg = "PERMANENT" if days == 0 else str(days)
    try:
        await rcon_command(
            f"cloverwarn {target['minecraft']} {duration_arg} {encoded_reason}"
        )
        with db_connect() as conn:
            conn.execute(
                "UPDATE warn_history SET server_notified = 1 "
                "WHERE id = (SELECT MAX(id) FROM warn_history WHERE telegram_id = ?)",
                (target_id,)
            )
            conn.commit()
    except Exception:
        log.exception("Could not send warn to Minecraft plugin")
        await message.reply(
            "Варн сохранён в базе данных, но не удалось отправить его на сервер через RCON. "
            "Проверьте, что обновлённый плагин установлен."
        )
        return

    until_text = "перманентно" if days == 0 else expires_at
    try:
        await message.bot.send_message(
            target_id,
            "Вам был выдан варн\n"
            f"Причина: {safe_html(reason)}\n"
            f"До: {safe_html(until_text)}"
        )
        dm_result = "Игрок уведомлён в личных сообщениях."
    except Exception:
        dm_result = "Не удалось отправить игроку ЛС (возможно, он не открывал диалог с ботом)."

    await message.reply(
        "Варн выдан.\n"
        f"Игрок: <code>{safe_html(target['minecraft'])}</code>\n"
        f"Активных варнов: <b>{count}/3</b>\n"
        f"Срок: <b>{safe_html(until_text)}</b>\n"
        f"{safe_html(dm_result)}"
    )


# =====================================================
# /UNWARN — REMOVE ONE ACTIVE WARNING
# =====================================================

async def cmd_unwarn(message: Message):
    if not message.from_user:
        return

    actor_id = message.from_user.id
    actor = get_account(actor_id)
    if not actor:
        await message.reply("Ваш аккаунт не найден или недоступен.")
        return

    actor_rank = actor.get("rank")
    if actor_rank == "eventor" or actor_rank not in {
        "moder", "admin", "tech_admin", "st_admin", "owner"
    }:
        await message.reply(
            "Снимать варны могут администраторы ранга moder и выше (eventor исключён)."
        )
        return

    args = extract_command(message)
    parts = args.split()
    if len(parts) != 1:
        await message.reply(
            "Использование:\n"
            "<code>/unwarn &lt;MinecraftNick/TG username/TG ID&gt;</code>"
        )
        return

    target = get_warn_target(parts[0])
    if not target:
        await message.reply("Игрок не найден. Он должен быть зарегистрирован в CloverTeam.")
        return

    target_id = target["telegram_id"]
    target_rank = target.get("rank", "player")

    # Те же ограничения иерархии, что и у /warn.
    if actor_rank != "owner":
        actor_level = WARN_RANK_LEVEL.get(actor_rank, -1)
        target_level = WARN_RANK_LEVEL.get(target_rank, -1)
        if target_rank == "rest":
            target_account = get_account(target_id)
            target_level = WARN_RANK_LEVEL.get(
                target_account.get("original_rank") if target_account else "", -1
            )
        elif target_rank == "eventor":
            target_level = WARN_RANK_LEVEL["moder"]

        if target_level >= actor_level:
            await message.reply(
                "Вы не можете снимать варн администратору выше своего ранга/одному рангу."
            )
            return

    now = moscow_now()
    try:
        with db_connect() as conn:
            # Сначала обновляем просроченные записи, затем снимаем ровно
            # один самый поздний из оставшихся активных варнов.
            active_count = refresh_warn_count(conn, target_id, now)
            latest = conn.execute(
                "SELECT id, reason, days, issued_at FROM warn_history "
                "WHERE telegram_id = ? AND status = 'active' "
                "ORDER BY id DESC LIMIT 1",
                (target_id,)
            ).fetchone()

            if not latest:
                conn.commit()
                await message.reply("У этого игрока нет активных варнов.")
                return

            conn.execute(
                "UPDATE warn_history SET status = 'removed' WHERE id = ?",
                (latest["id"],)
            )
            remaining = refresh_warn_count(conn, target_id, now)
            conn.commit()
    except Exception:
        log.exception("Could not remove warning")
        await message.reply("Не удалось снять варн из базы данных.")
        return

    await message.reply(
        "Один активный варн снят.\n"
        f"Игрок: <code>{safe_html(target['minecraft'] or target['username'] or target_id)}</code>\n"
        f"Снятый варн: <code>#{int(latest['id'])}</code>\n"
        f"Причина: {safe_html(latest['reason'])}\n"
        f"Осталось активных варнов: <b>{remaining}/3</b>\n\n"
        "Ранг и серверные блокировки не изменялись."
    )


# =====================================================
# /ONLINE
# =====================================================

async def cmd_online(message: Message):

    account = await require_registered(message)

    if not account:
        return

    if account["rank"] == "legacy":
        await message.reply(
            "У вас недостаточно прав для использования этой команды!"
        )
        return

    try:
        response = await rcon_command("cloverstats")

        log.info(
            "RAW CLOVERSTATS RESPONSE: %r",
            response
        )

        match = re.search(
            r"CLOVER_STATS\s+players=(\d+)\s+ops=(\d+)\s+names=(.*)",
            response,
            re.DOTALL
        )

        if not match:
            await message.reply(
                "Не удалось обработать ответ Paper-плагина.\n"
                "Проверьте вывод команды cloverstats."
            )
            return

        players = int(match.group(1))
        ops = int(match.group(2))
        names = match.group(3).strip()

        if names == "-":
            names = "Нет игроков"

        await message.reply(
            "<b>Кол-во онлайн в сервере</b>\n\n"
            f"Игроков онлайн: <b>{players}</b>\n"
            f"Из них OP: <b>{ops}</b>\n\n"
            f"<b>Список игроков:</b>\n"
            f"{safe_html(names)}"
        )

    except asyncio.TimeoutError:
        await message.reply(
            "Время ожидания ответа RCON истекло."
        )

    except Exception:
        log.exception("Online command failed")
        await message.reply(
            "Не удалось получить онлайн сервера. "
            "Проверьте RCON и плагин."
        )


# =====================================================
# /SAY
# =====================================================

async def cmd_say(message: Message):

    account = await require_registered(message)

    if not account:
        return

    if account["rank"] not in STAFF_RANKS:
        await message.reply(
            "У вас недостаточно прав для использования этой команды!"
        )
        return

    text = extract_command(message)

    if not text:
        await message.reply(
            "Использование:\n/say текст"
        )
        return

    if (
        len(text) > 400
        or "\n" in text
        or "\r" in text
        or "\x00" in text
    ):
        await message.reply(
            "Сообщение слишком длинное "
            "или имеет недопустимый формат."
        )
        return

    telegram = (
        account["telegram"]
        or ("ID " + str(message.from_user.id))
    )

    payload = {
        "text": "",
        "extra": [
            {
                "text": "[TLG] ",
                "color": "aqua"
            },
            {
                "text": account["minecraft"],
                "color": "red"
            },
            {
                "text": f" (TG: {telegram}): ",
                "color": "gray"
            },
            {
                "text": text,
                "color": "white"
            }
        ]
    }

    try:
        await rcon_command(
            "tellraw @a "
            + json.dumps(
                payload,
                ensure_ascii=False
            )
        )

        await message.reply(
            "Сообщение отправлено в игровой чат."
        )

    except Exception:
        log.exception("Say command failed")
        await message.reply(
            "Не удалось отправить сообщение через RCON."
        )


# =====================================================
# /JAVA
# =====================================================

async def cmd_java(message: Message):

    account = await require_registered(message)

    if not account:
        return

    if account["rank"] not in JAVA_RANKS:
        await message.reply(
            "У вас недостаточно прав для использования этой команды!"
        )
        return

    command = extract_command(message).lstrip("/")

    if not command:
        await message.reply(
            "Использование:\n/java команда"
        )
        return

    if (
        len(command) > 240
        or any(c in command for c in "\r\n\x00")
    ):
        await message.reply(
            "Команда имеет недопустимый формат."
        )
        return

    try:
        status = await message.reply(
            "Отправка команды:\n"
            f"<code>{safe_html(command)}</code>"
        )

        output = await rcon_command(command)

        if not output:
            output = "Консоль не вернула результат."

        output = output[:3000]

        await status.edit_text(
            "Отправка команды:\n"
            f"<code>{safe_html(command)}</code>\n\n"
            "Результат:\n"
            f"{format_code(output)}"
        )

    except asyncio.TimeoutError:
        await message.reply(
            "Время ожидания ответа RCON истекло."
        )

    except Exception:
        log.exception("Java command failed")
        await message.reply(
            "Не удалось выполнить команду через RCON."
        )


# =====================================================
# PRIVATE MESSAGE ROUTER
# =====================================================

async def private_reply_router(message: Message):

    if message.chat.type != "private":
        return

    handled = await clan_creation_reply(message)
    if handled:
        return

    handled = await rest_private_input(message)
    if handled:
        return

    await application_reply(message)


# =====================================================
# ADMIN CHAT REPLY ROUTER
# =====================================================

async def admin_reply_router(message: Message):

    if message.chat.id != ADMIN_CHAT_ID:
        return

    handled = await clan_setup_reply(message)
    if handled:
        return

    handled = await cmd_ankban(message)

    if handled:
        return

    handled = await rest_rejection_reply(message)

    if handled:
        return




async def cmd_sitecode(message: Message):
    if message.chat.type != "private":
        return

    if not message.from_user:
        return

    parts = (message.text or "").strip().split(maxsplit=1)

    if len(parts) != 2:
        await message.answer(
            "Использование: <code>/sitecode КОД_ЗАПРОСА</code>"
        )
        return

    challenge = parts[1].strip()

    if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", challenge):
        await message.answer("Некорректный запрос подтверждения.")
        return

    telegram_id = message.from_user.id
    admin = get_admin(telegram_id)

    if not admin:
        await message.answer("Твой Telegram ID отсутствует в списке администрации.")
        return

    rank = str(admin.get("rank", "")).strip()
    nickname = str(admin.get("minecraft", "")).strip()

    if rank not in STAFF_RANKS or not nickname:
        await message.answer("Твоя запись не подходит для регистрации на сайте.")
        return

    challenge_hash = hashlib.sha256(
        challenge.encode("utf-8")
    ).hexdigest()

    con = sqlite3.connect(VERIFY_DB_PATH, timeout=5)
    con.row_factory = sqlite3.Row

    try:
        con.execute("PRAGMA busy_timeout = 5000")
        con.execute("BEGIN IMMEDIATE")

        record = con.execute("""
            SELECT *
            FROM registration_challenges
            WHERE challenge_hash = ?
              AND telegram_id = ?
              AND minecraft_nickname = ? COLLATE NOCASE
        """, (challenge_hash, telegram_id, nickname)).fetchone()

        now = int(datetime.now(timezone.utc).timestamp())

        if (
            not record
            or record["consumed"]
            or record["expires_at"] < now
        ):
            con.rollback()
            await message.answer(
                "Запрос не найден или срок его действия истёк. "
                "Создай новый запрос на сайте."
            )
            return

        if record["code_hash"]:
            con.rollback()
            await message.answer(
                "Для этого запроса код уже выдавался. Создай новый запрос на сайте."
            )
            return

        code = f"{secrets.randbelow(100_000_000):08d}"
        code_message = f"{challenge}:{code}".encode("utf-8")

        code_hash = hmac.new(
            VERIFY_SECRET.encode("utf-8"),
            code_message,
            hashlib.sha256
        ).hexdigest()

        cursor = con.execute("""
            UPDATE registration_challenges
            SET code_hash = ?,
                code_expires_at = ?
            WHERE id = ?
              AND code_hash IS NULL
              AND consumed = 0
        """, (code_hash, now + 300, record["id"]))

        if cursor.rowcount != 1:
            con.rollback()
            await message.answer("Не удалось выдать код. Создай новый запрос.")
            return

        con.commit()

    except Exception:
        con.rollback()
        log.exception("Site registration verification failed")
        await message.answer("Внутренняя ошибка. Попробуй создать новый запрос.")
        return

    finally:
        con.close()

    await message.answer(
        "<b>Подтверждение CloverTeam</b>\n\n"
        f"Твой код: <code>{code}</code>\n"
        "Срок действия — 5 минут.\n"
        "Введи его на сайте. Никому не пересылай этот код."
    )



# =====================================================
# CLANS, CLOVER TOKENS AND ADMIN RANK MANAGEMENT
# =====================================================

import random

FARMA_COOLDOWN = timedelta(hours=4)


def init_clan_schema():
    with db_connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS clans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            leader_id INTEGER NOT NULL,
            clan_type TEXT NOT NULL DEFAULT 'open' CHECK(clan_type IN ('open','closed')),
            reputation REAL NOT NULL DEFAULT 0,
            verified INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            setup_log_chat_id INTEGER,
            setup_log_message_id INTEGER,
            verification_deadline TEXT
        );
        CREATE TABLE IF NOT EXISTS clan_members (
            telegram_id INTEGER PRIMARY KEY,
            clan_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('leader','deputy','member')),
            joined_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_clan_members_clan ON clan_members(clan_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_one_clan_deputy ON clan_members(clan_id) WHERE role='deputy';
        CREATE TABLE IF NOT EXISTS clan_visuals (
            clan_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK(role IN ('leader','deputy','member')),
            visual TEXT NOT NULL,
            PRIMARY KEY(clan_id, role)
        );
        CREATE TABLE IF NOT EXISTS clan_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clan_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
            telegram_id INTEGER NOT NULL,
            invited_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(clan_id, telegram_id)
        );
        CREATE TABLE IF NOT EXISTS clan_setup_sessions (
            admin_id INTEGER PRIMARY KEY,
            clan_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            prompt_message_id INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS clan_creation_sessions (
            telegram_id INTEGER PRIMARY KEY,
            clan_name TEXT NOT NULL,
            questionnaire TEXT,
            deputy_id INTEGER,
            prompt_message_id INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS token_wallets (
            telegram_id INTEGER PRIMARY KEY,
            balance REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS token_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            kind TEXT NOT NULL,
            created_at TEXT NOT NULL,
            details TEXT
        );
        CREATE TABLE IF NOT EXISTS farm_cooldowns (
            telegram_id INTEGER PRIMARY KEY,
            last_farmed_at TEXT NOT NULL
        );
        """)
        clan_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(clans)").fetchall()
        }
        if "verification_deadline" not in clan_columns:
            conn.execute("ALTER TABLE clans ADD COLUMN verification_deadline TEXT")
        creation_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(clan_creation_sessions)").fetchall()
        }
        if "deputy_id" not in creation_columns:
            conn.execute("ALTER TABLE clan_creation_sessions ADD COLUMN deputy_id INTEGER")
        conn.commit()


def money_text(value) -> str:
    number = round(float(value or 0), 2)
    return f"{number:.2f}".rstrip("0").rstrip(".")


def clan_member(telegram_id: int):
    with db_connect() as conn:
        return conn.execute("""
            SELECT cm.*, c.name AS clan_name, c.leader_id, c.clan_type,
                   c.reputation, c.verified, c.created_at
            FROM clan_members cm JOIN clans c ON c.id=cm.clan_id
            WHERE cm.telegram_id=?
        """, (telegram_id,)).fetchone()


def resolve_registered_player(value: str):
    return get_warn_target(value)


def player_display(telegram_id: int) -> str:
    user = get_user(telegram_id)
    if not user:
        return f"ID {telegram_id}"
    username = (user["telegram_username"] or "").lstrip("@")
    if username:
        return f'<a href="tg://user?id={telegram_id}">@{safe_html(username)}</a>'
    return f'<a href="tg://user?id={telegram_id}">{safe_html(user["minecraft_nickname"] or telegram_id)}</a>'


def clan_keyboard(clan_id: int, clan_type: str):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Вступить", callback_data=f"clan_join:{clan_id}"),
        InlineKeyboardButton(text="Участники", callback_data=f"clan_members:{clan_id}"),
    ]])


def clan_setup_keyboard(clan_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Добавить лидера", callback_data=f"clan_visual:{clan_id}:leader"),
        InlineKeyboardButton(text="Добавить зам. лидера", callback_data=f"clan_visual:{clan_id}:deputy"),
        InlineKeyboardButton(text="Добавить участников", callback_data=f"clan_visual:{clan_id}:member"),
    ]])


def clan_setup_keyboard_remaining(clan_id: int, roles_done: set[str]):
    labels = {
        "leader": "Добавить лидера",
        "deputy": "Добавить зам. лидера",
        "member": "Добавить участников",
    }
    buttons = [InlineKeyboardButton(text=label, callback_data=f"clan_visual:{clan_id}:{role}")
               for role, label in labels.items() if role not in roles_done]
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None


def clan_setup_log_text(clan, verified: bool = False) -> str:
    leader = get_user(clan["leader_id"])
    username = (leader["telegram_username"] or "без username").lstrip("@") if leader else "неизвестно"
    nick = (leader["minecraft_nickname"] or "не указан") if leader else "не указан"
    text = (
        f"Создан новый клан игроком @{safe_html(username)}\n"
        f"Ник майнкрафта: <code>{safe_html(nick)}</code>\n"
        f"Айди ТГ: <code>{clan['leader_id']}</code>\n"
        f"Название клана: <b>{safe_html(clan['name'])}</b>\n"
        "Чтобы клан был подтвержден, необходимо добавить в LuckyPerms роли для обычных участников, зам. лидера и лидера"
    )
    with db_connect() as conn:
        leader_visual_set = conn.execute(
            "SELECT 1 FROM clan_visuals WHERE clan_id=? AND role='leader'",
            (clan["id"],)
        ).fetchone()
    if leader and leader["rank"] in STAFF_RANKS and leader_visual_set:
        text += f"\nОбнаружен админ. Используемый ник для проверки онлайна: <code>{safe_html(nick)}</code>"
    if verified:
        text += "\n<b>ВЕРИФИЦИРОВАН</b>"
    return text


def format_clan_age(created_at: str) -> str:
    try:
        created = datetime.fromisoformat(created_at)
        now = moscow_now()
        days = max(0, (now.date() - created.date()).days)
    except Exception:
        return "неизвестно"
    if days < 30:
        return f"{days} дн."
    months, rest_days = divmod(days, 30)
    return f"{months} мес. и {rest_days} дн."


async def cmd_clans(message: Message):
    if not message.from_user:
        return
    with db_connect() as conn:
        rows = conn.execute("SELECT id,name FROM clans ORDER BY id").fetchall()
    if not rows:
        await message.reply("Пока нет ни одного клана.")
        return
    lines = ["<b>Все кланы:</b>"]
    lines.extend(f"{row['id']} - {safe_html(row['name'])}" for row in rows)
    await message.reply("\n".join(lines))


async def cmd_clan(message: Message):
    if not message.from_user:
        return
    args = extract_command(message).strip()
    if not args.isdigit():
        own = clan_member(message.from_user.id)
        if own:
            args = str(own["clan_id"])
        else:
            await message.reply("Использование: /clan ID\nСписок кланов: /clans")
            return
    with db_connect() as conn:
        clan = conn.execute("SELECT * FROM clans WHERE id=?", (int(args),)).fetchone()
        if not clan:
            await message.reply("Клан с таким ID не найден.")
            return
        leader = conn.execute("SELECT telegram_username,minecraft_nickname FROM users WHERE telegram_id=?", (clan['leader_id'],)).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM clan_members WHERE clan_id=?", (clan['id'],)).fetchone()[0]
    leader_name = (leader['telegram_username'] or '').lstrip('@') if leader else ''
    leader_link = f'<a href="tg://user?id={clan["leader_id"]}">@{safe_html(leader_name)}</a>' if leader_name else f'<a href="tg://user?id={clan["leader_id"]}">{safe_html(leader["minecraft_nickname"] if leader else clan["leader_id"])}</a>'
    state = "" if clan["verified"] else " [Не верифицирован]"
    clan_type = "Открытый" if clan["clan_type"] == "open" else "Закрытый"
    text = (
        f'<b>[{clan["id"]}] Клан «{safe_html(clan["name"])}»{state}</b>\n'
        f"Лидер: {leader_link}\n"
        f"Основан: {clan['created_at'][:10]} ({format_clan_age(clan['created_at'])} назад)\n"
        f"Тип: {clan_type}\n"
        f"Репутация: {money_text(clan['reputation'])} 🍀\n"
        f"Участников: {count}"
    )
    await message.reply(text, reply_markup=clan_keyboard(clan['id'], clan['clan_type']))


async def cmd_addclan(message: Message):
    if not message.from_user:
        return
    account = await require_registered(message)
    if not account:
        return

    name = extract_command(message).strip()
    if len(name) >= 2 and name[0] == name[-1] and name[0] in {"'", '"'}:
        name = name[1:-1].strip()
    if not name or len(name) > 40 or any(ord(c) < 32 for c in name):
        await message.reply('Использование: /addclan "Название клана" (до 40 символов)')
        return
    if clan_member(message.from_user.id):
        await message.reply("Вы уже состоите в клане. Сначала покиньте его.")
        return

    with db_connect() as conn:
        exists = conn.execute("SELECT 1 FROM clans WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if exists:
            await message.reply("Клан с таким названием уже существует.")
            return
        conn.execute(
            """INSERT OR REPLACE INTO clan_creation_sessions
               (telegram_id, clan_name, questionnaire, prompt_message_id, created_at)
               VALUES (?, ?, NULL, NULL, ?)""",
            (message.from_user.id, name, moscow_iso(moscow_now()))
        )
        conn.commit()

    prompt_text = (
        "Чтобы создать клан, необходимо написать анкету:\n\n"
        "1 ) Цель создания вашего клана?\n\n"
        "2 ) Какой стиль построек предпочитает ваш клан?\n\n"
        "3 ) Кто ваш зам. лидер? (Ник майнкрафт и тг юзернейм)\n\n"
        "Отправьте ответы одним сообщением в ответ на это сообщение."
    )
    try:
        prompt = await message.bot.send_message(message.from_user.id, prompt_text)
    except Exception:
        with db_connect() as conn:
            conn.execute("DELETE FROM clan_creation_sessions WHERE telegram_id=?", (message.from_user.id,))
            conn.commit()
        await message.reply("Напишите боту в личные сообщения команду /start, затем повторите /addclan.")
        return

    with db_connect() as conn:
        conn.execute(
            "UPDATE clan_creation_sessions SET prompt_message_id=? WHERE telegram_id=?",
            (prompt.message_id, message.from_user.id)
        )
        conn.commit()

    if message.chat.type != "private":
        await message.reply("Я отправил анкету создания клана вам в личные сообщения.")
    else:
        await message.reply("Анкета отправлена выше. Заполните её одним сообщением.")


async def clan_creation_reply(message: Message) -> bool:
    if not message.from_user or message.chat.type != "private" or not message.text:
        return False
    if not message.reply_to_message:
        return False

    with db_connect() as conn:
        session = conn.execute(
            "SELECT * FROM clan_creation_sessions WHERE telegram_id=?",
            (message.from_user.id,)
        ).fetchone()

    if not session or session["prompt_message_id"] != message.reply_to_message.message_id:
        return False

    answers = message.text.strip()
    if len(answers) < 10 or len(answers) > 3000:
        await message.reply("Анкета должна содержать от 10 до 3000 символов. Ответьте на сообщение с вопросами.")
        return True

    lines = [re.sub(r"^\s*\d+\s*[).]\s*", "", line).strip()
             for line in answers.splitlines() if line.strip()]
    if len(lines) < 3:
        await message.reply("Нужно ответить на все 3 вопроса отдельными строками, включая ник и Telegram username заместителя.")
        return True

    deputy_parts = lines[2].split()
    deputy = None
    for candidate in deputy_parts:
        deputy = resolve_registered_player(candidate)
        if deputy:
            break
    if not deputy:
        await message.reply("Не удалось найти заместителя в базе. Укажите его Minecraft-ник или Telegram @username, а также убедитесь, что он зарегистрирован.")
        return True
    if deputy["telegram_id"] == message.from_user.id:
        await message.reply("Вы не можете назначить заместителем самого себя.")
        return True
    if clan_member(deputy["telegram_id"]):
        await message.reply("Указанный заместитель уже состоит в другом клане.")
        return True

    questionnaire = (
        f"1. Цель создания: {lines[0]}\n"
        f"2. Стиль построек: {lines[1]}\n"
        f"3. Заместитель: {deputy['minecraft'] or 'не указан'} / "
        f"@{(deputy['username'] or '').lstrip('@')}"
    )
    with db_connect() as conn:
        conn.execute(
            "UPDATE clan_creation_sessions SET questionnaire=?, deputy_id=? WHERE telegram_id=?",
            (questionnaire, deputy["telegram_id"], message.from_user.id)
        )
        conn.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да", callback_data=f"clan_rules_yes:{message.from_user.id}"),
        InlineKeyboardButton(text="Нет", callback_data=f"clan_rules_no:{message.from_user.id}")
    ]])
    await message.reply("Вы ознакомились с правилами клана?", reply_markup=kb)
    return True


async def clan_rules_callback(callback: CallbackQuery):
    if not callback.from_user:
        return
    try:
        action, owner_id_text = (callback.data or "").split(":", 1)
        owner_id = int(owner_id_text)
    except (ValueError, AttributeError):
        await callback.answer("Некорректное действие.", show_alert=True)
        return
    if callback.from_user.id != owner_id:
        await callback.answer("Эта кнопка предназначена создателю клана.", show_alert=True)
        return

    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        session = conn.execute(
            "SELECT * FROM clan_creation_sessions WHERE telegram_id=?",
            (owner_id,)
        ).fetchone()
        if not session or not session["questionnaire"]:
            conn.rollback()
            await callback.answer("Анкета не найдена или устарела.", show_alert=True)
            return

        if action == "clan_rules_no":
            conn.execute("DELETE FROM clan_creation_sessions WHERE telegram_id=?", (owner_id,))
            conn.commit()
            await callback.message.edit_text("Создание клана отменено. Клан не был создан.")
            await callback.answer("Отменено.")
            return

        if action != "clan_rules_yes":
            conn.rollback()
            await callback.answer("Неизвестное действие.", show_alert=True)
            return

        creator = conn.execute("SELECT * FROM users WHERE telegram_id=?", (owner_id,)).fetchone()
        deputy_row = conn.execute(
            "SELECT * FROM users WHERE telegram_id=?",
            (session["deputy_id"],)
        ).fetchone()

        if not creator or not deputy_row or deputy_row["telegram_id"] == owner_id:
            conn.rollback()
            await callback.answer("Не удалось проверить создателя или заместителя. Запустите /addclan заново.", show_alert=True)
            return

        occupied = conn.execute(
            "SELECT 1 FROM clan_members WHERE telegram_id IN (?, ?)",
            (owner_id, deputy_row["telegram_id"])
        ).fetchall()
        if occupied:
            conn.rollback()
            await callback.answer("Создатель или заместитель уже состоит в клане.", show_alert=True)
            return

        now = moscow_now()
        created_at = moscow_iso(now)
        deadline = moscow_iso(now + timedelta(hours=24))
        try:
            cur = conn.execute(
                """INSERT INTO clans
                   (name, leader_id, created_at, verification_deadline)
                   VALUES (?, ?, ?, ?)""",
                (session["clan_name"], owner_id, created_at, deadline)
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            await callback.answer("Клан с таким названием уже существует.", show_alert=True)
            return

        clan_id = cur.lastrowid
        conn.execute(
            "INSERT INTO clan_members(telegram_id,clan_id,role,joined_at) VALUES(?,?,'leader',?)",
            (owner_id, clan_id, created_at)
        )
        conn.execute(
            "INSERT INTO clan_members(telegram_id,clan_id,role,joined_at) VALUES(?,?,'deputy',?)",
            (deputy_row["telegram_id"], clan_id, created_at)
        )
        conn.execute("DELETE FROM clan_creation_sessions WHERE telegram_id=?", (owner_id,))
        conn.commit()

    creator_username = (creator["telegram_username"] or "").lstrip("@") or "не указан"
    creator_nick = creator["minecraft_nickname"] or "не указан"
    log_text = (
        f"Создан новый клан игроком @{safe_html(creator_username)}\n"
        f"Ник майнкрафта: <code>{safe_html(creator_nick)}</code>\n"
        f"ID: <code>{owner_id}</code>\n"
        f"Название клана: <b>{safe_html(session['clan_name'])}</b>\n"
        f"Анкета клана:\n{safe_html(session['questionnaire'])}\n\n"
        "Для того, чтобы клан существовал дольше, необходимо верифицировать его. "
        "Если в течении 24 часов не будет верификации, клан удалится"
    )
    verify_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Верифицировать", callback_data=f"clan_verify:{clan_id}")
    ]])
    if ADMIN_CHAT_ID:
        try:
            sent = await callback.bot.send_message(
                ADMIN_CHAT_ID,
                log_text,
                message_thread_id=ADMIN_THREAD_ID or None,
                reply_markup=verify_kb
            )
            with db_connect() as conn:
                conn.execute(
                    "UPDATE clans SET setup_log_chat_id=?, setup_log_message_id=? WHERE id=?",
                    (ADMIN_CHAT_ID, sent.message_id, clan_id)
                )
                conn.commit()
        except Exception:
            log.exception("Could not send clan creation log for clan %s", clan_id)

    try:
        await callback.bot.send_message(
            owner_id,
            f"Клан «{safe_html(session['clan_name'])}» создан! ID: <b>{clan_id}</b>. "
            "У администрации есть 24 часа на верификацию."
        )
    except Exception:
        pass
    try:
        await callback.bot.send_message(
            deputy_row["telegram_id"],
            f"Вас назначили заместителем клана «{safe_html(session['clan_name'])}»."
        )
    except Exception:
        pass

    if callback.message:
        await callback.message.edit_text("Правила приняты. Клан создан и отправлен администрации на верификацию.")
    await callback.answer("Клан создан.")


async def clan_verify_callback(callback: CallbackQuery):
    if not callback.from_user:
        return
    actor = get_user(callback.from_user.id)
    if not actor or actor["rank"] not in {"admin", "st_admin", "owner"}:
        await callback.answer("Верифицировать кланы могут только admin и выше.", show_alert=True)
        return
    try:
        clan_id = int((callback.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Некорректный ID клана.", show_alert=True)
        return

    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        clan = conn.execute("SELECT * FROM clans WHERE id=?", (clan_id,)).fetchone()
        if not clan:
            conn.rollback()
            await callback.answer("Клан не найден.", show_alert=True)
            return
        if clan["verified"]:
            conn.rollback()
            await callback.answer("Клан уже верифицирован.")
            return
        conn.execute(
            "UPDATE clans SET verified=1, verification_deadline=NULL WHERE id=? AND verified=0",
            (clan_id,)
        )
        conn.commit()

    if callback.message:
        updated = (callback.message.text or "").replace(
            "Если в течении 24 часов не будет верификации, клан удалится",
            "Клан верифицирован администрацией."
        )
        updated += f"\n\nВерифицирован: @{safe_html(callback.from_user.username or str(callback.from_user.id))}"
        try:
            await callback.message.edit_text(updated, reply_markup=None)
        except Exception:
            log.exception("Could not update clan verification log")
    await callback.answer("Клан верифицирован.")


async def clan_expiration_worker(bot: Bot):
    while True:
        try:
            now = moscow_iso(moscow_now())
            with db_connect() as conn:
                expired = conn.execute(
                    """SELECT id, name, leader_id FROM clans
                       WHERE verified=0 AND verification_deadline IS NOT NULL
                         AND verification_deadline <= ?""",
                    (now,)
                ).fetchall()
                for clan in expired:
                    conn.execute("DELETE FROM clans WHERE id=?", (clan["id"],))
                conn.commit()

            for clan in expired:
                try:
                    await bot.send_message(
                        clan["leader_id"],
                        f"Клан «{safe_html(clan['name'])}» удалён: он не прошёл верификацию в течение 24 часов."
                    )
                except Exception:
                    pass
        except Exception:
            log.exception("Clan expiration worker error")
        await asyncio.sleep(60)


async def cmd_lrest(message: Message):
    if not message.from_user:
        return
    user = get_user(message.from_user.id)
    if not user or user["rank"] not in {
        "helper", "moder", "eventor", "tech_admin", "admin", "st_admin", "owner"
    }:
        await message.reply("Команда доступна администрации ранга helper и выше.")
        return

    now = moscow_now()
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT u.telegram_id, u.telegram_username, u.minecraft_nickname,
                      rp.ends_at
               FROM rest_periods rp
               JOIN users u ON u.telegram_id=rp.telegram_id
               WHERE rp.ends_at > ?
               ORDER BY rp.ends_at""",
            (moscow_iso(now),)
        ).fetchall()

    if not rows:
        await message.reply("Активных рестов нету (И слава богу).")
        return

    lines = ["<b>Активные ресты:</b>"]
    for row in rows:
        name = row["telegram_username"] or row["minecraft_nickname"] or str(row["telegram_id"])
        try:
            ends = datetime.fromisoformat(row["ends_at"]).astimezone(MOSCOW_TZ)
            ends_text = ends.strftime("%d.%m.%Y в %H:%M")
        except Exception:
            ends_text = safe_html(row["ends_at"])
        lines.append(f"@{safe_html(name.lstrip('@'))} - В ресте до {ends_text} (ПО МСК)")
    await message.reply("\n".join(lines))




async def clan_visual_callback(callback: CallbackQuery):
    if not callback.from_user or not callback.message:
        return
    if not is_senior_admin(callback.from_user.id):
        await callback.answer("Настраивать визуалы могут только st_admin и owner.", show_alert=True)
        return
    _, clan_id_s, role = callback.data.split(":", 2)
    clan_id = int(clan_id_s)
    with db_connect() as conn:
        clan = conn.execute("SELECT * FROM clans WHERE id=?", (clan_id,)).fetchone()
        if not clan:
            await callback.answer("Клан не найден.", show_alert=True)
            return
        prompt = await callback.message.answer(f"Напишите визуал, по которому будет виден в list: <b>{role}</b>")
        conn.execute("INSERT OR REPLACE INTO clan_setup_sessions(admin_id,clan_id,role,prompt_message_id) VALUES(?,?,?,?)", (callback.from_user.id, clan_id, role, prompt.message_id))
        conn.commit()
    await callback.answer("Ожидаю визуал в ответ на сообщение.")


async def clan_setup_reply(message: Message) -> bool:
    if not message.from_user or message.chat.id != ADMIN_CHAT_ID or not message.text:
        return False
    if not is_senior_admin(message.from_user.id):
        return False
    if message.text.lstrip().startswith("/"):
        return False
    with db_connect() as conn:
        session = conn.execute("SELECT * FROM clan_setup_sessions WHERE admin_id=?", (message.from_user.id,)).fetchone()
        if not session:
            return False
        visual = message.text.strip()
        if not visual or len(visual) > 80:
            await message.reply("Визуал должен содержать от 1 до 80 символов. Отправьте его ответом на запрос.")
            return True
        conn.execute("INSERT OR REPLACE INTO clan_visuals(clan_id,role,visual) VALUES(?,?,?)", (session["clan_id"], session["role"], visual))
        conn.execute("DELETE FROM clan_setup_sessions WHERE admin_id=?", (message.from_user.id,))
        visuals = conn.execute("SELECT role FROM clan_visuals WHERE clan_id=?", (session["clan_id"],)).fetchall()
        clan = conn.execute("SELECT * FROM clans WHERE id=?", (session["clan_id"],)).fetchone()
        complete = {r["role"] for r in visuals} >= {"leader", "deputy", "member"}
        conn.commit()
    try:
        await message.bot.delete_message(message.chat.id, session["prompt_message_id"])
    except Exception:
        pass
    await message.reply(f"Визуал для роли «{session['role']}» сохранён: <code>{safe_html(visual)}</code>")
    if clan and clan["setup_log_chat_id"] and clan["setup_log_message_id"]:
        try:
            await message.bot.edit_message_text(
                clan_setup_log_text(clan, verified=complete),
                chat_id=clan["setup_log_chat_id"],
                message_id=clan["setup_log_message_id"],
                reply_markup=clan_setup_keyboard_remaining(session["clan_id"], {r["role"] for r in visuals})
            )
        except Exception:
            log.exception("Could not update clan setup log")
    return True


async def cmd_addmemberclan(message: Message):
    if not message.from_user:
        return
    member = clan_member(message.from_user.id)
    if not member or member["role"] not in {"leader", "deputy"}:
        await message.reply("Приглашать игроков могут только лидер и заместитель клана.")
        return
    target_value = extract_command(message).strip()
    if message.reply_to_message and message.reply_to_message.from_user:
        target_value = str(message.reply_to_message.from_user.id)
    if not target_value:
        await message.reply("Использование: ответьте на сообщение игрока командой /addmemberclan или укажите ID/@username/ник.")
        return
    target = resolve_registered_player(target_value)
    if not target:
        await message.reply("Игрок не найден в базе users.")
        return
    if clan_member(target["telegram_id"]):
        await message.reply("Игрок уже состоит в клане.")
        return
    with db_connect() as conn:
        conn.execute("INSERT OR REPLACE INTO clan_invites(clan_id,telegram_id,invited_by,created_at) VALUES(?,?,?,?)", (member["clan_id"], target["telegram_id"], message.from_user.id, moscow_iso(moscow_now())))
        conn.commit()
    try:
        await message.bot.send_message(target["telegram_id"], f"{player_display(target['telegram_id'])}, вас пригласили в клан <b>{safe_html(member['clan_name'])}</b>.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Принять приглашение", callback_data=f"clan_invite_accept:{member['clan_id']}")]]))
        await message.reply("Приглашение отправлено.")
    except Exception:
        await message.reply("Не удалось отправить сообщение игроку. Пусть он сначала откроет личные сообщения с ботом.")


async def cmd_remmemberclan(message: Message):
    if not message.from_user:
        return
    own = clan_member(message.from_user.id)
    if not own or own["role"] not in {"leader", "deputy"}:
        await message.reply("Исключать игроков могут только лидер и заместитель клана.")
        return
    target_value = extract_command(message).strip()
    if message.reply_to_message and message.reply_to_message.from_user:
        target_value = str(message.reply_to_message.from_user.id)
    target = resolve_registered_player(target_value) if target_value else None
    if not target:
        await message.reply("Укажите зарегистрированного игрока через ID, @username, Minecraft-ник или ответом на его сообщение.")
        return
    with db_connect() as conn:
        target_member = conn.execute("SELECT * FROM clan_members WHERE telegram_id=? AND clan_id=?", (target["telegram_id"], own["clan_id"])).fetchone()
        if not target_member:
            await message.reply("Этот игрок не состоит в вашем клане.")
            return
        if target_member["role"] == "leader":
            await message.reply("Лидера нельзя исключить из клана.")
            return
        if own["role"] == "deputy" and target_member["role"] == "deputy":
            await message.reply("Заместитель не может исключить другого заместителя.")
            return
        conn.execute("DELETE FROM clan_members WHERE telegram_id=?", (target["telegram_id"],))
        conn.execute("DELETE FROM clan_invites WHERE telegram_id=?", (target["telegram_id"],))
        conn.commit()
    await message.reply("Игрок исключён из клана.")
    try:
        await message.bot.send_message(target["telegram_id"], f"Вас исключили из клана «{safe_html(own['clan_name'])}».")
    except Exception:
        pass


async def cmd_clandeputy(message: Message):
    if not message.from_user:
        return
    own = clan_member(message.from_user.id)
    if not own or own["role"] != "leader":
        await message.reply("Назначать заместителя может только лидер клана.")
        return
    value = extract_command(message).strip()
    if message.reply_to_message and message.reply_to_message.from_user:
        value = str(message.reply_to_message.from_user.id)
    target = resolve_registered_player(value) if value else None
    if not target:
        await message.reply("Использование: /clandeputy ID|@username|MinecraftNick или ответом на сообщение игрока.")
        return
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM clan_members WHERE telegram_id=? AND clan_id=?", (target["telegram_id"], own["clan_id"])).fetchone()
        if not row or row["role"] == "leader":
            await message.reply("Заместителем можно назначить только участника вашего клана.")
            return
        conn.execute("UPDATE clan_members SET role='member' WHERE clan_id=? AND role='deputy'", (own["clan_id"],))
        conn.execute("UPDATE clan_members SET role='deputy' WHERE telegram_id=?", (target["telegram_id"],))
        conn.commit()
    await message.reply(f"Игрок {safe_html(target['minecraft'] or target['username'] or target['telegram_id'])} назначен заместителем.")


async def cmd_clantype(message: Message):
    if not message.from_user:
        return
    own = clan_member(message.from_user.id)
    if not own or own["role"] != "leader":
        await message.reply("Менять тип клана может только лидер.")
        return
    value = extract_command(message).strip().lower()
    if value not in {"open", "closed", "открытый", "закрытый"}:
        await message.reply("Использование: /clantype open или /clantype closed")
        return
    clan_type = "open" if value in {"open", "открытый"} else "closed"
    with db_connect() as conn:
        conn.execute("UPDATE clans SET clan_type=? WHERE id=?", (clan_type, own["clan_id"]))
        conn.commit()
    await message.reply("Тип клана изменён: " + ("открытый" if clan_type == "open" else "закрытый"))


async def cmd_delclan(message: Message):
    if not message.from_user:
        return
    arg = extract_command(message).strip()
    if not arg.isdigit():
        await message.reply("Использование: /delclan ID")
        return
    clan_id = int(arg)
    with db_connect() as conn:
        clan = conn.execute("SELECT * FROM clans WHERE id=?", (clan_id,)).fetchone()
    if not clan:
        await message.reply("Клан не найден.")
        return
    actor = get_account(message.from_user.id)
    own = clan_member(message.from_user.id)
    allowed = (own and own["clan_id"] == clan_id and own["role"] == "leader") or (actor and actor["rank"] in {"moder", "eventor", "tech_admin", "admin", "st_admin", "owner"})
    if not allowed:
        await message.reply("Удалить этот клан может его лидер или администрация ранга moder и выше.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да", callback_data=f"clan_delete_yes:{clan_id}:{message.from_user.id}"),
        InlineKeyboardButton(text="Нет", callback_data=f"clan_delete_no:{clan_id}:{message.from_user.id}"),
    ]])
    await message.reply(f"Вы действительно хотите удалить клан «{safe_html(clan['name'])}»?", reply_markup=kb)


async def clan_delete_callback(callback: CallbackQuery):
    if not callback.from_user or not callback.message:
        return
    parts = callback.data.split(":")
    action, clan_id, owner_id = parts[0], int(parts[1]), int(parts[2])
    if callback.from_user.id != owner_id:
        await callback.answer("Эта кнопка предназначена другому пользователю.", show_alert=True)
        return
    if action == "clan_delete_no":
        await callback.message.delete()
        await callback.answer("Отменено.")
        return
    with db_connect() as conn:
        clan = conn.execute("SELECT name FROM clans WHERE id=?", (clan_id,)).fetchone()
        if not clan:
            await callback.message.delete()
            await callback.answer("Клан уже удалён.")
            return
        conn.execute("DELETE FROM clans WHERE id=?", (clan_id,))
        conn.commit()
    await callback.message.edit_text(f"Клан «{safe_html(clan['name'])}» удалён.")
    await callback.answer("Клан удалён.")


async def clan_join_callback(callback: CallbackQuery):
    if not callback.from_user:
        return
    account = get_account(callback.from_user.id)
    if not account:
        await callback.answer("Для вступления нужно быть зарегистрированным игроком.", show_alert=True)
        return
    _, clan_id_s = callback.data.split(":", 1)
    clan_id = int(clan_id_s)
    with db_connect() as conn:
        clan = conn.execute("SELECT * FROM clans WHERE id=?", (clan_id,)).fetchone()
        if not clan:
            await callback.answer("Клан не найден.", show_alert=True)
            return
        if conn.execute("SELECT 1 FROM clan_members WHERE telegram_id=?", (callback.from_user.id,)).fetchone():
            await callback.answer("Вы уже состоите в клане.", show_alert=True)
            return
        if clan["clan_type"] != "open":
            await callback.answer("Клан закрытый. Вступить можно только по приглашению лидера или заместителя.", show_alert=True)
            return
        conn.execute("INSERT INTO clan_members(telegram_id,clan_id,role,joined_at) VALUES(?,?,'member',?)", (callback.from_user.id, clan_id, moscow_iso(moscow_now())))
        conn.commit()
    await callback.answer("Вы вступили в клан!", show_alert=True)
    try:
        await callback.message.bot.send_message(clan["leader_id"], f"Игрок {player_display(callback.from_user.id)} вступил в ваш клан «{safe_html(clan['name'])}».")
    except Exception:
        pass


async def clan_invite_accept_callback(callback: CallbackQuery):
    if not callback.from_user:
        return
    clan_id = int(callback.data.split(":", 1)[1])
    with db_connect() as conn:
        invite = conn.execute("SELECT * FROM clan_invites WHERE clan_id=? AND telegram_id=?", (clan_id, callback.from_user.id)).fetchone()
        clan = conn.execute("SELECT * FROM clans WHERE id=?", (clan_id,)).fetchone()
        if not invite or not clan:
            await callback.answer("Приглашение не найдено или уже недействительно.", show_alert=True)
            return
        if conn.execute("SELECT 1 FROM clan_members WHERE telegram_id=?", (callback.from_user.id,)).fetchone():
            await callback.answer("Вы уже состоите в клане.", show_alert=True)
            return
        conn.execute("INSERT INTO clan_members(telegram_id,clan_id,role,joined_at) VALUES(?,?,'member',?)", (callback.from_user.id, clan_id, moscow_iso(moscow_now())))
        conn.execute("DELETE FROM clan_invites WHERE id=?", (invite["id"],))
        conn.commit()
    await callback.answer("Вы вступили в клан!", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None)
    try:
        await callback.message.bot.send_message(clan["leader_id"], f"Игрок {player_display(callback.from_user.id)} принял приглашение в клан «{safe_html(clan['name'])}».")
    except Exception:
        pass


async def clan_members_callback(callback: CallbackQuery):
    if not callback.message:
        return
    clan_id = int(callback.data.split(":", 1)[1])
    with db_connect() as conn:
        rows = conn.execute("""
            SELECT cm.role, u.telegram_id, u.telegram_username, u.minecraft_nickname
            FROM clan_members cm JOIN users u ON u.telegram_id=cm.telegram_id
            WHERE cm.clan_id=? ORDER BY CASE cm.role WHEN 'leader' THEN 0 WHEN 'deputy' THEN 1 ELSE 2 END, u.minecraft_nickname
        """, (clan_id,)).fetchall()
    if not rows:
        await callback.answer("Участников пока нет.", show_alert=True)
        return
    labels = {"leader": "Лидер", "deputy": "Заместитель", "member": "Участник"}
    lines = ["<b>Участники клана:</b>"]
    for row in rows:
        nick = row["minecraft_nickname"] or row["telegram_username"] or str(row["telegram_id"])
        lines.append(f"• {labels[row['role']]} — {safe_html(nick)}")
    await callback.message.answer("\n".join(lines))
    await callback.answer()


async def cmd_leaveclan(message: Message):
    if not message.from_user:
        return
    own = clan_member(message.from_user.id)
    if not own:
        await message.reply("Вы не состоите в клане.")
        return
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        clan = conn.execute("SELECT id,name,leader_id FROM clans WHERE id=?", (own["clan_id"],)).fetchone()
        if not clan:
            conn.rollback()
            await message.reply("Клан не найден.")
            return
        if own["role"] == "leader":
            count = conn.execute("SELECT COUNT(*) FROM clan_members WHERE clan_id=?", (own["clan_id"],)).fetchone()[0]
            if count > 1:
                conn.rollback()
                await message.reply("Вы лидер клана. Сначала передайте лидерство командой /transferclan.")
                return
            conn.execute("DELETE FROM clans WHERE id=?", (own["clan_id"],))
            result = "Вы были последним участником и удалили свой клан, покинув его."
        else:
            conn.execute("DELETE FROM clan_members WHERE telegram_id=?", (message.from_user.id,))
            result = f"Вы покинули клан «{safe_html(clan['name'])}»."
        conn.execute("DELETE FROM clan_invites WHERE telegram_id=?", (message.from_user.id,))
        conn.commit()
    await message.reply(result)
    if own["role"] != "leader":
        try:
            await message.bot.send_message(clan["leader_id"], f"Игрок {player_display(message.from_user.id)} покинул клан «{safe_html(clan['name'])}».")
        except Exception:
            pass


async def cmd_transferclan(message: Message):
    if not message.from_user:
        return
    own = clan_member(message.from_user.id)
    if not own or own["role"] != "leader":
        await message.reply("Передать лидерство может только лидер клана.")
        return
    value = extract_command(message).strip()
    if message.reply_to_message and message.reply_to_message.from_user:
        value = str(message.reply_to_message.from_user.id)
    if not value:
        await message.reply("Использование: /transferclan ID | @username | MinecraftNick или ответом на сообщение участника.")
        return
    target = resolve_registered_player(value)
    if not target:
        await message.reply("Игрок не найден в базе users.")
        return
    if target["telegram_id"] == message.from_user.id:
        await message.reply("Вы уже являетесь лидером этого клана.")
        return
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        target_member = conn.execute("SELECT role FROM clan_members WHERE telegram_id=? AND clan_id=?", (target["telegram_id"], own["clan_id"])).fetchone()
        if not target_member:
            conn.rollback()
            await message.reply("Передать лидерство можно только участнику вашего клана.")
            return
        conn.execute("UPDATE clan_members SET role='member' WHERE telegram_id=? AND clan_id=?", (message.from_user.id, own["clan_id"]))
        conn.execute("UPDATE clan_members SET role='leader' WHERE telegram_id=? AND clan_id=?", (target["telegram_id"], own["clan_id"]))
        conn.execute("UPDATE clans SET leader_id=? WHERE id=?", (target["telegram_id"], own["clan_id"]))
        conn.commit()
    display = target["minecraft"] or target["username"] or str(target["telegram_id"])
    await message.reply(f"Лидерство клана «{safe_html(own['clan_name'])}» передано игроку <b>{safe_html(display)}</b>. Вы теперь обычный участник.")
    try:
        await message.bot.send_message(target["telegram_id"], f"Вы стали лидером клана «{safe_html(own['clan_name'])}».")
    except Exception:
        pass


async def cmd_cancelinvite(message: Message):
    if not message.from_user:
        return
    own = clan_member(message.from_user.id)
    if not own or own["role"] not in {"leader", "deputy"}:
        await message.reply("Отменять приглашения могут только лидер и заместитель клана.")
        return
    value = extract_command(message).strip()
    if message.reply_to_message and message.reply_to_message.from_user:
        value = str(message.reply_to_message.from_user.id)
    if not value:
        await message.reply("Использование: /cancelinvite ID | @username | MinecraftNick или ответом на сообщение игрока.")
        return
    target = resolve_registered_player(value)
    if not target:
        await message.reply("Игрок не найден в базе users.")
        return
    with db_connect() as conn:
        invite = conn.execute("SELECT id FROM clan_invites WHERE clan_id=? AND telegram_id=?", (own["clan_id"], target["telegram_id"])).fetchone()
        if not invite:
            await message.reply("У этого игрока нет активного приглашения в ваш клан.")
            return
        conn.execute("DELETE FROM clan_invites WHERE id=?", (invite["id"],))
        conn.commit()
    await message.reply("Приглашение отменено.")
    try:
        await message.bot.send_message(target["telegram_id"], f"Приглашение в клан «{safe_html(own['clan_name'])}» было отменено.")
    except Exception:
        pass


async def cmd_claninfo(message: Message):
    if not message.from_user:
        return
    value = extract_command(message).strip()
    if value:
        if not value.isdigit():
            await message.reply("Использование: /claninfo ID. Без ID показывает ваш клан.")
            return
        clan_id = int(value)
    else:
        own = clan_member(message.from_user.id)
        if not own:
            await message.reply("Укажите ID клана: /claninfo ID")
            return
        clan_id = own["clan_id"]
    with db_connect() as conn:
        clan = conn.execute("SELECT * FROM clans WHERE id=?", (clan_id,)).fetchone()
        if not clan:
            await message.reply("Клан с таким ID не найден.")
            return
        members = conn.execute("""
            SELECT cm.role,u.telegram_id,u.telegram_username,u.minecraft_nickname
            FROM clan_members cm LEFT JOIN users u ON u.telegram_id=cm.telegram_id
            WHERE cm.clan_id=?
            ORDER BY CASE cm.role WHEN 'leader' THEN 0 WHEN 'deputy' THEN 1 ELSE 2 END, u.minecraft_nickname COLLATE NOCASE
        """, (clan_id,)).fetchall()
        invite_count = conn.execute("SELECT COUNT(*) FROM clan_invites WHERE clan_id=?", (clan_id,)).fetchone()[0]
        visuals = {r["role"]: r["visual"] for r in conn.execute("SELECT role,visual FROM clan_visuals WHERE clan_id=?", (clan_id,)).fetchall()}
    labels = {"leader": "Лидер", "deputy": "Заместитель", "member": "Участник"}
    lines = [
        f"<b>Подробная информация о клане [{clan['id']}] «{safe_html(clan['name'])}»</b>",
        f"Статус: {'Подтверждён' if clan['verified'] else 'Не подтверждён'}",
        f"Тип: {'Открытый' if clan['clan_type']=='open' else 'Закрытый'}",
        f"Основан: {safe_html(clan['created_at'])}",
        f"Возраст: {format_clan_age(clan['created_at'])}",
        f"Репутация: <b>{money_text(clan['reputation'])}</b> 🍀",
        f"Участников: <b>{len(members)}</b>",
        f"Активных приглашений: <b>{invite_count}</b>",
        "", "<b>Состав:</b>"
    ]
    for row in members:
        nick = row["minecraft_nickname"] or row["telegram_username"] or str(row["telegram_id"])
        lines.append(f"• {labels.get(row['role'], 'Участник')} — {safe_html(nick)}")
    lines.extend(["", "<b>Визуалы LuckyPerms:</b>"])
    for role in ("leader", "deputy", "member"):
        visual = visuals.get(role)
        lines.append(f"• {labels[role]}: <code>{safe_html(visual)}</code>" if visual else f"• {labels[role]}: не настроен")
    await message.reply("\\n".join(lines), reply_markup=clan_keyboard(clan_id, clan["clan_type"]))



async def cmd_clanonline(message: Message):
    if not message.from_user:
        return
    own = clan_member(message.from_user.id)
    if not own:
        await message.reply("Вы не являетесь участником клана")
        return

    with db_connect() as conn:
        members = conn.execute(
            """SELECT u.minecraft_nickname
               FROM clan_members cm
               JOIN users u ON u.telegram_id=cm.telegram_id
               WHERE cm.clan_id=?""",
            (own["clan_id"],)
        ).fetchall()

    try:
        output = await rcon_command("list")
    except Exception:
        log.exception("Clan online RCON failed")
        await message.reply("Не удалось получить список игроков через RCON.")
        return

    # Vanilla/Paper /list usually returns:
    # There are N of a max of M players online: name1, name2
    online_names = set()
    for line in output.splitlines():
        if ":" in line:
            tail = line.split(":", 1)[1]
            for item in re.split(r"[,;]", tail):
                nick = item.strip()
                if nick:
                    online_names.add(nick.casefold())

    online = []
    for row in members:
        nick = (row["minecraft_nickname"] or "").strip()
        if nick and nick.casefold() in online_names:
            online.append(nick)

    text = (
        f"<b>Онлайн клана «{safe_html(own['clan_name'])}»</b>\n"
        f"Сейчас онлайн: <b>{len(online)}</b>\n"
        + ("\n".join(f"• <code>{safe_html(n)}</code>" for n in sorted(online, key=str.casefold))
           if online else "Никого из клана сейчас нет онлайн.")
    )
    await message.reply(text)




async def cmd_farma(message: Message):
    if not message.from_user:
        return
    account = await require_registered(message)
    if not account:
        return
    now = moscow_now()
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT last_farmed_at FROM farm_cooldowns WHERE telegram_id=?", (message.from_user.id,)).fetchone()
        if row:
            last = datetime.fromisoformat(row["last_farmed_at"])
            ready = last + FARMA_COOLDOWN
            if now < ready:
                remain = ready - now
                total = int(remain.total_seconds())
                h, rem = divmod(total, 3600)
                m, sec = divmod(rem, 60)
                await message.reply(f"Фармить можно будет через {h} ч. {m} мин. {sec} сек.")
                return
        roll = random.random() * 100
        if roll < 88.95:
            tier, low, high, mult_low, mult_high = "Обычная награда", 5, 50, 1, 3
        elif roll < 98.95:
            tier, low, high, mult_low, mult_high = "Джекпот", 100, 500, 2, 5
        elif roll < 99.95:
            tier, low, high, mult_low, mult_high = "Суперджекпот", 1000, 5000, 3, 10
        else:
            tier, low, high, mult_low, mult_high = "Мегаджекпот", 10000, 100000, 5, 15
        base = random.randint(low, high)
        coefficient = round(random.uniform(mult_low, mult_high), 2)
        reward = round(base * coefficient, 2)
        conn.execute("INSERT INTO farm_cooldowns(telegram_id,last_farmed_at) VALUES(?,?) ON CONFLICT(telegram_id) DO UPDATE SET last_farmed_at=excluded.last_farmed_at", (message.from_user.id, moscow_iso(now)))
        conn.execute("INSERT INTO token_wallets(telegram_id,balance) VALUES(?,?) ON CONFLICT(telegram_id) DO UPDATE SET balance=balance+excluded.balance", (message.from_user.id, reward))
        conn.execute("INSERT INTO token_transactions(telegram_id,amount,kind,created_at,details) VALUES(?,?,?,?,?)", (message.from_user.id, reward, "farm", moscow_iso(now), f"{tier}; base={base}; coefficient={coefficient}"))
        balance = conn.execute("SELECT balance FROM token_wallets WHERE telegram_id=?", (message.from_user.id,)).fetchone()[0]
        conn.commit()
    await message.reply(f"🍀 <b>{tier}!</b>\nБазовая награда: {base}\nКоэффициент: x{coefficient:.2f}\nНачислено: <b>{money_text(reward)}</b> токенов\nБаланс: <b>{money_text(balance)}</b> 🍀")


async def cmd_balance(message: Message):
    if not message.from_user:
        return
    account = await require_registered(message)
    if not account:
        return
    with db_connect() as conn:
        row = conn.execute("SELECT balance FROM token_wallets WHERE telegram_id=?", (message.from_user.id,)).fetchone()
    balance = row["balance"] if row else 0
    await message.reply(f"Ваш баланс: <b>{money_text(balance)}</b> 🍀")


async def cmd_clancoin(message: Message):
    if not message.from_user:
        return
    account = await require_registered(message)
    if not account:
        return
    own = clan_member(message.from_user.id)
    if not own:
        await message.reply("Вы не состоите в клане.")
        return
    arg = extract_command(message).strip().replace(",", ".")
    try:
        amount = round(float(arg), 2)
    except ValueError:
        amount = 0
    if amount <= 0:
        await message.reply("Использование: /clancoin количество (положительное число, до 2 знаков после запятой)")
        return
    with db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        wallet = conn.execute("SELECT balance FROM token_wallets WHERE telegram_id=?", (message.from_user.id,)).fetchone()
        balance = wallet["balance"] if wallet else 0
        if balance + 1e-9 < amount:
            conn.rollback()
            await message.reply(f"Недостаточно токенов. Ваш баланс: {money_text(balance)} 🍀")
            return
        conn.execute("UPDATE token_wallets SET balance=balance-? WHERE telegram_id=?", (amount, message.from_user.id))
        conn.execute("UPDATE clans SET reputation=reputation+? WHERE id=?", (amount, own["clan_id"]))
        now = moscow_iso(moscow_now())
        conn.execute("INSERT INTO token_transactions(telegram_id,amount,kind,created_at,details) VALUES(?,?,?,?,?)", (message.from_user.id, -amount, "clancoin", now, f"clan_id={own['clan_id']}"))
        new_balance = conn.execute("SELECT balance FROM token_wallets WHERE telegram_id=?", (message.from_user.id,)).fetchone()[0]
        new_rep = conn.execute("SELECT reputation FROM clans WHERE id=?", (own["clan_id"],)).fetchone()[0]
        conn.commit()
    await message.reply(f"В репутацию клана «{safe_html(own['clan_name'])}» переведено <b>{money_text(amount)}</b> 🍀\nВаш баланс: {money_text(new_balance)}\nРепутация клана: {money_text(new_rep)}")


async def cmd_addadmins(message: Message):
    if not message.from_user:
        return
    if not is_senior_admin(message.from_user.id):
        await message.reply("Команда доступна только st_admin и owner.")
        return
    parts = extract_command(message).split()
    if len(parts) != 2:
        await message.reply("Использование: /addadmins Telegram_ID|@username|MinecraftNick rank\nРанги: helper, moder, eventor, tech_admin, admin, st_admin, owner")
        return
    target = resolve_registered_player(parts[0])
    rank = parts[1].lower()
    if not target or rank not in STAFF_RANKS:
        await message.reply("Игрок не найден или указан недопустимый ранг.")
        return
    actor = get_account(message.from_user.id)
    if actor["rank"] == "st_admin" and rank in {"st_admin", "owner"}:
        await message.reply("st_admin не может назначать st_admin или owner.")
        return
    with db_connect() as conn:
        conn.execute("UPDATE users SET rank=? WHERE telegram_id=?", (rank, target["telegram_id"]))
        conn.commit()
    await message.reply(f"Игрок <code>{safe_html(target['minecraft'] or target['username'] or target['telegram_id'])}</code> назначен на ранг <b>{rank}</b>.")


async def cmd_deladmins(message: Message):
    if not message.from_user:
        return
    if not is_senior_admin(message.from_user.id):
        await message.reply("Команда доступна только st_admin и owner.")
        return
    arg = extract_command(message).strip()
    target = resolve_registered_player(arg) if arg else None
    if not target:
        await message.reply("Использование: /deladmins Telegram_ID|@username|MinecraftNick")
        return
    actor = get_account(message.from_user.id)
    if target["rank"] == "owner" and actor["rank"] != "owner":
        await message.reply("Только owner может снять ранг owner.")
        return
    if target["rank"] == "st_admin" and actor["rank"] != "owner":
        await message.reply("Только owner может снять ранг st_admin.")
        return
    with db_connect() as conn:
        conn.execute("UPDATE users SET rank='player' WHERE telegram_id=?", (target["telegram_id"],))
        conn.commit()
    await message.reply(f"Административный ранг пользователя <code>{safe_html(target['minecraft'] or target['username'] or target['telegram_id'])}</code> снят. Теперь он player.")


# =====================================================
# STARTUP
# =====================================================

async def main():

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    if not RCON_HOST or not RCON_PASSWORD:
        raise RuntimeError("RCON settings are missing")

    init_db()
    init_clan_schema()

    bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

    dp = Dispatcher()
    dp.message.outer_middleware(LegacyAccessMiddleware())

    # ---------------------------------------------
    # User commands
    # ---------------------------------------------

    dp.message.register(
        cmd_sitecode,
        Command("sitecode")
    )

    dp.message.register(
        cmd_start,
        Command("start")
    )

    dp.message.register(
        cmd_rest,
        Command("rest")
    )

    dp.message.register(
        cmd_reg,
        Command("reg")
    )

    dp.message.register(
        cmd_unreg,
        Command("unreg")
    )

    dp.message.register(
        cmd_online,
        Command("online")
    )

    dp.message.register(
        cmd_say,
        Command("say")
    )

    dp.message.register(
        cmd_java,
        Command("java")
    )

    dp.message.register(cmd_clans, Command("clans"))
    dp.message.register(cmd_claninfo, Command("claninfo"))
    dp.message.register(cmd_leaveclan, Command("leaveclan"))
    dp.message.register(cmd_transferclan, Command("transferclan"))
    dp.message.register(cmd_cancelinvite, Command("cancelinvite"))
    dp.message.register(cmd_clan, Command("clan"))
    dp.message.register(cmd_addclan, Command("addclan"))
    dp.message.register(cmd_addmemberclan, Command("addmemberclan"))
    dp.message.register(cmd_remmemberclan, Command("remmemberclan"))
    dp.message.register(cmd_delclan, Command("delclan"))
    dp.message.register(cmd_clanonline, Command("clanonline"))
    dp.message.register(cmd_clandeputy, Command("clandeputy"))
    dp.message.register(cmd_clantype, Command("clantype"))
    dp.message.register(cmd_farma, Command("farma"))
    dp.message.register(cmd_balance, Command("balance"))
    dp.message.register(cmd_clancoin, Command("clancoin"))
    dp.message.register(cmd_lrest, Command("lrest"))
    dp.message.register(cmd_addadmins, Command("addadmins"))
    dp.message.register(cmd_deladmins, Command("deladmins"))

    dp.message.register(
        cmd_warn,
        Command("warn")
    )

    dp.message.register(
        cmd_unwarn,
        Command("unwarn")
    )

    # ---------------------------------------------
    # Private replies
    # ---------------------------------------------

    dp.message.register(
        private_reply_router,
        F.chat.type == "private",
        F.reply_to_message
    )

    # ---------------------------------------------
    # Admin replies
    # ---------------------------------------------

    dp.message.register(
        admin_reply_router,
        F.chat.id == ADMIN_CHAT_ID,
        F.text
    )

    # ---------------------------------------------
    # Main chat membership tracking
    # ---------------------------------------------

    dp.chat_member.register(track_legacy_chat_member)

    # ---------------------------------------------
    # Rest callbacks
    # ---------------------------------------------

    dp.callback_query.register(
        rest_start_callback,
        F.data.in_({
            "rest_self",
            "rest_friend"
        })
    )

    dp.callback_query.register(
        rest_manage_callback,
        F.data.in_({
            "rest_extend",
            "rest_remove",
            "rest_remove_yes",
            "rest_remove_no"
        })
    )

    dp.callback_query.register(
        rest_admin_decision,
        F.data.startswith("rest_approve:")
        | F.data.startswith("rest_reject:")
    )

    # ---------------------------------------------
    # Application callbacks
    # ---------------------------------------------

    dp.callback_query.register(
        application_decision,
        F.data.startswith("app_")
    )

    dp.callback_query.register(clan_rules_callback, F.data.startswith("clan_rules_"))
    dp.callback_query.register(clan_verify_callback, F.data.startswith("clan_verify:"))
    dp.callback_query.register(clan_visual_callback, F.data.startswith("clan_visual:"))
    dp.callback_query.register(clan_join_callback, F.data.startswith("clan_join:"))
    dp.callback_query.register(clan_invite_accept_callback, F.data.startswith("clan_invite_accept:"))
    dp.callback_query.register(clan_members_callback, F.data.startswith("clan_members:"))
    dp.callback_query.register(clan_delete_callback, F.data.startswith("clan_delete_yes:") | F.data.startswith("clan_delete_no:"))

    # ---------------------------------------------
    # Background task
    # ---------------------------------------------

    expiration_task = asyncio.create_task(
        rest_expiration_worker(bot)
    )

    clan_expiration_task = asyncio.create_task(
        clan_expiration_worker(bot)
    )

    warn_expiration_task = asyncio.create_task(
        warn_expiration_worker()
    )

    log.info("CloverTeam bot started")

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )

    finally:
        expiration_task.cancel()
        clan_expiration_task.cancel()
        warn_expiration_task.cancel()

        for task in (expiration_task, clan_expiration_task, warn_expiration_task):
            try:
                await task
            except asyncio.CancelledError:
                pass

        await bot.session.close()




if __name__ == "__main__":
    asyncio.run(main())
