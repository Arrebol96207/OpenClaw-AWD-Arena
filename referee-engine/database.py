import asyncio
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, TypeVar

from redaction import DEFAULT_REDACTED_VALUE, is_sensitive_key

DB_PATH = os.getenv("OPENCLAW_DB_PATH", os.path.join(os.path.dirname(__file__), "openclaw.db"))
REDACTED_VALUE = DEFAULT_REDACTED_VALUE
DB_RETRY_ATTEMPTS = 4
DB_RETRY_BASE_DELAY_SECONDS = 0.05
DB_LOCK_ERROR_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)
T = TypeVar("T")

_connection_lock = threading.Lock()
_cached_connection: Optional[sqlite3.Connection] = None


def _is_transient_sqlite_lock_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in DB_LOCK_ERROR_MARKERS)


async def _run_db_sync(func: Callable[..., T], *args: Any, attempts: int = DB_RETRY_ATTEMPTS, **kwargs: Any) -> T:
    for attempt in range(max(1, attempts)):
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except sqlite3.OperationalError as exc:
            if not _is_transient_sqlite_lock_error(exc) or attempt >= attempts - 1:
                raise
            await asyncio.sleep(DB_RETRY_BASE_DELAY_SECONDS * (2 ** attempt))
    raise RuntimeError("unreachable database retry state")


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _get_cached_connection() -> sqlite3.Connection:
    global _cached_connection
    with _connection_lock:
        if _cached_connection is None:
            _cached_connection = _connect()
        return _cached_connection


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _is_sensitive_config_key(key: Any) -> bool:
    return is_sensitive_key(key)


def _sanitize_config_for_storage(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_config_key(key):
                if item not in (None, "", [], {}):
                    sanitized[key] = REDACTED_VALUE
                continue
            sanitized[key] = _sanitize_config_for_storage(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_config_for_storage(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_config_for_storage(item) for item in value]
    return value


def _scrub_persisted_config_table(conn: sqlite3.Connection, table_name: str, key_column: str) -> None:
    rows = conn.execute(f"SELECT {key_column}, config_json FROM {table_name}").fetchall()
    for row in rows:
        try:
            config = json.loads(row["config_json"])
        except Exception:
            continue
        sanitized_config = _sanitize_config_for_storage(config)
        if sanitized_config == config:
            continue
        conn.execute(
            f"UPDATE {table_name} SET config_json = ? WHERE {key_column} = ?",
            (json.dumps(sanitized_config, ensure_ascii=False), row[key_column]),
        )


def _scrub_persisted_configs(conn: sqlite3.Connection) -> None:
    _scrub_persisted_config_table(conn, "matches", "match_id")
    _scrub_persisted_config_table(conn, "loops", "loop_id")


def _submission_flag_for_storage(value: Any) -> str:
    if value is None:
        return ""
    return REDACTED_VALUE if str(value) else ""


def _scrub_persisted_submissions(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE submissions
        SET flag = ?
        WHERE flag IS NOT NULL AND flag != '' AND flag != ?
        """,
        (REDACTED_VALUE, REDACTED_VALUE),
    )


def _init_db_sync() -> None:
    conn = _get_cached_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS matches (
              match_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              config_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              finished_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              match_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              data_json TEXT NOT NULL,
              timestamp TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS submissions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              match_id TEXT NOT NULL,
              attacker_id INTEGER NOT NULL,
              victim_id INTEGER,
              declared_target_player_id INTEGER,
              flag TEXT NOT NULL,
              success INTEGER NOT NULL,
              reason TEXT NOT NULL,
              points INTEGER NOT NULL,
              timestamp TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS loops (
              loop_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              repeat_count INTEGER NOT NULL,
              current_iteration INTEGER NOT NULL,
              current_match_id TEXT,
              last_match_id TEXT,
              config_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              stopped_at TEXT
            )
            """
        )
        existing_submission_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(submissions)").fetchall()
        }
        if "flag_slot" not in existing_submission_columns:
            conn.execute("ALTER TABLE submissions ADD COLUMN flag_slot TEXT")
        if "flag_index" not in existing_submission_columns:
            conn.execute("ALTER TABLE submissions ADD COLUMN flag_index INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_match_time ON events(match_id, timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_match_type_time ON events(match_id, event_type, timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_submissions_match_time ON submissions(match_id, timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_loops_updated_at ON loops(updated_at)")
        _scrub_persisted_configs(conn)
        _scrub_persisted_submissions(conn)
        conn.commit()
    finally:
        conn.close()


def _save_match_sync(match_id: str, status: str, config_dict: Dict[str, Any], created_at: datetime) -> None:
    stored_config = _sanitize_config_for_storage(config_dict)
    conn = _get_cached_connection()
    try:
        conn.execute(
            """
            INSERT INTO matches(match_id, status, config_json, created_at, finished_at)
            VALUES(?, ?, ?, ?, NULL)
            ON CONFLICT(match_id) DO UPDATE SET
              status=excluded.status,
              config_json=excluded.config_json,
              created_at=excluded.created_at
            """,
            (match_id, status, json.dumps(stored_config, ensure_ascii=False), created_at.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _update_match_status_sync(match_id: str, status: str, finished_at: Optional[datetime]) -> None:
    conn = _get_cached_connection()
    try:
        conn.execute(
            """
            UPDATE matches
            SET status = ?, finished_at = COALESCE(?, finished_at)
            WHERE match_id = ?
            """,
            (status, _to_iso(finished_at), match_id),
        )
        conn.commit()
    finally:
        conn.close()


def _save_event_sync(match_id: str, event_type: str, data: Dict[str, Any], timestamp: datetime) -> None:
    conn = _get_cached_connection()
    conn.execute(
        """
        INSERT INTO events(match_id, event_type, data_json, timestamp)
        VALUES(?, ?, ?, ?)
        """,
        (match_id, event_type, json.dumps(data, ensure_ascii=False), timestamp.isoformat()),
    )
    conn.commit()


def _save_loop_sync(
    loop_id: str,
    status: str,
    repeat_count: int,
    current_iteration: int,
    config_dict: Dict[str, Any],
    created_at: datetime,
    updated_at: datetime,
    current_match_id: Optional[str] = None,
    last_match_id: Optional[str] = None,
    stopped_at: Optional[datetime] = None,
) -> None:
    stored_config = _sanitize_config_for_storage(config_dict)
    conn = _get_cached_connection()
    try:
        conn.execute(
            """
            INSERT INTO loops(
              loop_id,
              status,
              repeat_count,
              current_iteration,
              current_match_id,
              last_match_id,
              config_json,
              created_at,
              updated_at,
              stopped_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(loop_id) DO UPDATE SET
              status=excluded.status,
              repeat_count=excluded.repeat_count,
              current_iteration=excluded.current_iteration,
              current_match_id=excluded.current_match_id,
              last_match_id=excluded.last_match_id,
              config_json=excluded.config_json,
              updated_at=excluded.updated_at,
              stopped_at=excluded.stopped_at
            """,
            (
                loop_id,
                status,
                repeat_count,
                current_iteration,
                current_match_id,
                last_match_id,
                json.dumps(stored_config, ensure_ascii=False),
                created_at.isoformat(),
                updated_at.isoformat(),
                _to_iso(stopped_at),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _get_loop_sync(loop_id: str) -> Optional[Dict[str, Any]]:
    conn = _get_cached_connection()
    try:
        row = conn.execute(
            """
            SELECT loop_id, status, repeat_count, current_iteration, current_match_id, last_match_id,
                   config_json, created_at, updated_at, stopped_at
            FROM loops
            WHERE loop_id = ?
            """,
            (loop_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "loop_id": row["loop_id"],
            "status": row["status"],
            "repeat_count": row["repeat_count"],
            "current_iteration": row["current_iteration"],
            "current_match_id": row["current_match_id"],
            "last_match_id": row["last_match_id"],
            "config": json.loads(row["config_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "stopped_at": row["stopped_at"],
        }
    finally:
        conn.close()


def _list_loops_sync() -> List[Dict[str, Any]]:
    conn = _get_cached_connection()
    try:
        rows = conn.execute(
            """
            SELECT loop_id, status, repeat_count, current_iteration, current_match_id, last_match_id,
                   config_json, created_at, updated_at, stopped_at
            FROM loops
            ORDER BY datetime(updated_at) DESC, datetime(created_at) DESC
            """
        ).fetchall()
        return [
            {
                "loop_id": row["loop_id"],
                "status": row["status"],
                "repeat_count": row["repeat_count"],
                "current_iteration": row["current_iteration"],
                "current_match_id": row["current_match_id"],
                "last_match_id": row["last_match_id"],
                "config": json.loads(row["config_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "stopped_at": row["stopped_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def _load_all_matches_sync() -> List[Dict[str, Any]]:
    conn = _get_cached_connection()
    try:
        matches = conn.execute(
            """
            SELECT match_id, status, config_json, created_at, finished_at
            FROM matches
            ORDER BY datetime(created_at) DESC
            """
        ).fetchall()

        output: List[Dict[str, Any]] = []
        for row in matches:
            event_rows = conn.execute(
                """
                SELECT event_type, data_json, timestamp
                FROM events
                WHERE match_id = ?
                ORDER BY datetime(timestamp) ASC
                """,
                (row["match_id"],),
            ).fetchall()

            events = [
                {
                    "type": event_row["event_type"],
                    "data": json.loads(event_row["data_json"]),
                    "timestamp": event_row["timestamp"],
                    "match_id": row["match_id"],
                }
                for event_row in event_rows
            ]

            output.append(
                {
                    "match_id": row["match_id"],
                    "status": row["status"],
                    "config": json.loads(row["config_json"]),
                    "created_at": row["created_at"],
                    "finished_at": row["finished_at"],
                    "events": events,
                }
            )

        return output
    finally:
        conn.close()


def _save_submission_sync(match_id: str, submission: Dict[str, Any]) -> None:
    conn = _get_cached_connection()
    try:
        conn.execute(
            """
            INSERT INTO submissions(
              match_id,
              attacker_id,
              victim_id,
              declared_target_player_id,
              flag,
              flag_slot,
              flag_index,
              success,
              reason,
              points,
              timestamp
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_id,
                int(submission.get("attacker_id", 0)),
                int(submission["victim_id"]) if submission.get("victim_id") is not None else None,
                submission.get("declared_target_player_id"),
                _submission_flag_for_storage(submission.get("flag", "")),
                submission.get("flag_slot"),
                int(submission["flag_index"]) if submission.get("flag_index") is not None else None,
                1 if submission.get("success") else 0,
                str(submission.get("reason", "unknown")),
                int(submission.get("points", 0)),
                str(submission.get("timestamp", "")),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _load_submissions_sync(match_id: str) -> List[Dict[str, Any]]:
    conn = _get_cached_connection()
    try:
        rows = conn.execute(
            """
            SELECT attacker_id, victim_id, declared_target_player_id, flag, flag_slot, flag_index, success, reason, points, timestamp
            FROM submissions
            WHERE match_id = ?
            ORDER BY datetime(timestamp) ASC, id ASC
            """,
            (match_id,),
        ).fetchall()

        submissions: List[Dict[str, Any]] = []
        for row in rows:
            submission: Dict[str, Any] = {
                "attacker_id": row["attacker_id"],
                "victim_id": row["victim_id"],
                "declared_target_player_id": row["declared_target_player_id"],
                "flag": row["flag"],
                "success": bool(row["success"]),
                "reason": row["reason"],
                "points": row["points"],
                "timestamp": row["timestamp"],
            }
            if row["flag_slot"] is not None:
                submission["flag_slot"] = row["flag_slot"]
            if row["flag_index"] is not None:
                submission["flag_index"] = row["flag_index"]
            submissions.append(submission)

        return submissions
    finally:
        conn.close()


async def init_db() -> None:
    await _run_db_sync(_init_db_sync)


async def save_match(match_id: str, status: str, config_dict: Dict[str, Any], created_at: datetime) -> None:
    await _run_db_sync(_save_match_sync, match_id, status, config_dict, created_at)


async def update_match_status(match_id: str, status: str, finished_at: Optional[datetime] = None) -> None:
    await _run_db_sync(_update_match_status_sync, match_id, status, finished_at)


async def save_event(match_id: str, event_type: str, data: Dict[str, Any], timestamp: datetime) -> None:
    await _run_db_sync(_save_event_sync, match_id, event_type, data, timestamp)


async def load_all_matches() -> List[Dict[str, Any]]:
    return await _run_db_sync(_load_all_matches_sync)


def _list_matches_summary_sync() -> List[Dict[str, Any]]:
    conn = _get_cached_connection()
    try:
        rows = conn.execute(
            """
            SELECT match_id, status, config_json, created_at, finished_at
            FROM matches
            ORDER BY datetime(created_at) DESC
            """
        ).fetchall()
        # Pre-fetch werewolf winners + last sheriff in one pass; the event-type indexes
        # created during init keep this fast even after many finished matches.
        winners: Dict[str, Dict[str, Any]] = {}
        finished_rows = conn.execute(
            """
            SELECT match_id, data_json
            FROM events
            WHERE event_type = 'WEREWOLF_GAME_FINISHED'
            ORDER BY datetime(timestamp) DESC
            """
        ).fetchall()
        for ev in finished_rows:
            mid = ev["match_id"]
            if mid in winners:
                continue
            try:
                payload = json.loads(ev["data_json"])
            except Exception:
                continue
            winners[mid] = {
                "winner": payload.get("winner"),
                "winner_label": payload.get("winner_label"),
                "finished_reason": payload.get("reason"),
                "final_day": payload.get("day"),
            }
        sheriffs: Dict[str, int] = {}
        sheriff_rows = conn.execute(
            """
            SELECT match_id, data_json
            FROM events
            WHERE event_type = 'WEREWOLF_SHERIFF_ASSIGNED'
            ORDER BY datetime(timestamp) DESC
            """
        ).fetchall()
        for ev in sheriff_rows:
            mid = ev["match_id"]
            if mid in sheriffs:
                continue
            try:
                payload = json.loads(ev["data_json"])
                pid = payload.get("player_id")
                if isinstance(pid, int):
                    sheriffs[mid] = pid
            except Exception:
                pass

        resources_destroyed = set()
        resource_rows = conn.execute(
            """
            SELECT match_id
            FROM events
            WHERE event_type = 'MATCH_RESOURCES_DESTROYED'
            """
        ).fetchall()
        for ev in resource_rows:
            resources_destroyed.add(ev["match_id"])

        player_code_exports: Dict[str, Dict[str, Any]] = {}
        export_rows = conn.execute(
            """
            SELECT match_id, data_json
            FROM events
            WHERE event_type IN ('PLAYER_CODE_EXPORT_READY', 'PLAYER_CODE_EXPORT_FAILED')
            ORDER BY datetime(timestamp) DESC, id DESC
            """
        ).fetchall()
        for ev in export_rows:
            mid = ev["match_id"]
            if mid in player_code_exports:
                continue
            try:
                payload = json.loads(ev["data_json"])
            except Exception:
                continue
            if isinstance(payload, dict):
                player_code_exports[mid] = payload

        output: List[Dict[str, Any]] = []
        for row in rows:
            config = json.loads(row["config_json"])
            players = config.get("players") or []
            match_config = config.get("match") or {}
            entry = {
                "match_id": row["match_id"],
                "name": match_config.get("name") or row["match_id"],
                "mode": config.get("mode") or "awd",
                "status": row["status"],
                "player_count": len(players),
                "duration": match_config.get("duration"),
                "created_at": row["created_at"],
                "finished_at": row["finished_at"],
                "resource_destroyed": row["match_id"] in resources_destroyed,
            }
            if row["match_id"] in player_code_exports:
                entry["player_code_export"] = player_code_exports[row["match_id"]]
            if entry["mode"] == "werewolf":
                werewolf_config = config.get("werewolf") or {}
                board = werewolf_config.get("board") or "standard_guard"
                entry["werewolf_board"] = board
                entry["werewolf_board_label"] = (
                    "12 人白狼王骑士" if board == "white_wolf_king_knight" else "12 人预女猎守"
                )
                w = winners.get(row["match_id"])
                if w:
                    entry["werewolf_winner"] = w.get("winner")
                    entry["werewolf_winner_label"] = w.get("winner_label")
                    entry["werewolf_finished_reason"] = w.get("finished_reason")
                    entry["werewolf_final_day"] = w.get("final_day")
                if row["match_id"] in sheriffs:
                    entry["werewolf_final_sheriff_id"] = sheriffs[row["match_id"]]
            output.append(entry)
        return output
    finally:
        conn.close()


def _delete_match_sync(match_id: str) -> int:
    """Delete a match and all its events/submissions. Returns rows affected on matches table."""
    conn = _get_cached_connection()
    try:
        conn.execute("DELETE FROM events WHERE match_id = ?", (match_id,))
        conn.execute("DELETE FROM submissions WHERE match_id = ?", (match_id,))
        cur = conn.execute("DELETE FROM matches WHERE match_id = ?", (match_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _prune_old_matches_sync(retention_days: int) -> int:
    """Delete finished/aborted/error matches older than retention_days. Returns count deleted."""
    if retention_days <= 0:
        return 0
    conn = _get_cached_connection()
    try:
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
        rows = conn.execute(
            "SELECT match_id FROM matches WHERE status IN ('finished','aborted','error') AND datetime(created_at) < datetime(?)",
            (cutoff,),
        ).fetchall()
        ids = [row["match_id"] for row in rows]
        for mid in ids:
            conn.execute("DELETE FROM events WHERE match_id = ?", (mid,))
            conn.execute("DELETE FROM submissions WHERE match_id = ?", (mid,))
        conn.executemany("DELETE FROM matches WHERE match_id = ?", [(mid,) for mid in ids])
        conn.commit()
        if ids:
            conn.execute("VACUUM")
        return len(ids)
    finally:
        conn.close()


async def prune_old_matches(retention_days: int) -> int:
    return await _run_db_sync(_prune_old_matches_sync, retention_days)


async def delete_match(match_id: str) -> int:
    return await _run_db_sync(_delete_match_sync, match_id)


async def list_matches_summary() -> List[Dict[str, Any]]:
    return await _run_db_sync(_list_matches_summary_sync)


async def save_loop(
    loop_id: str,
    status: str,
    repeat_count: int,
    current_iteration: int,
    config_dict: Dict[str, Any],
    created_at: datetime,
    updated_at: datetime,
    current_match_id: Optional[str] = None,
    last_match_id: Optional[str] = None,
    stopped_at: Optional[datetime] = None,
) -> None:
    await _run_db_sync(
        _save_loop_sync,
        loop_id,
        status,
        repeat_count,
        current_iteration,
        config_dict,
        created_at,
        updated_at,
        current_match_id,
        last_match_id,
        stopped_at,
    )


async def get_loop(loop_id: str) -> Optional[Dict[str, Any]]:
    return await _run_db_sync(_get_loop_sync, loop_id)


async def list_loops() -> List[Dict[str, Any]]:
    return await _run_db_sync(_list_loops_sync)


async def save_submission(match_id: str, submission: Dict[str, Any]) -> None:
    await _run_db_sync(_save_submission_sync, match_id, submission)


async def load_submissions(match_id: str) -> List[Dict[str, Any]]:
    return await _run_db_sync(_load_submissions_sync, match_id)
