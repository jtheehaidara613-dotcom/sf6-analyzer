"""SF6 AI動画解析システム - DB永続化モジュール。

解析結果（AnalyzeResponse）をSQLiteに保存・参照する。
外部依存なし。テーブルはアプリ起動時に自動作成される。

テーブル:
    analysis_results — 解析1件ごとのレコード
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from schemas import AnalyzeResponse

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent / "data" / "results.db"


def init_db() -> None:
    """DBとテーブルを初期化する。存在する場合はスキップ。"""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_results (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at    TEXT    NOT NULL,
                video_url     TEXT    NOT NULL,
                character_p1  TEXT    NOT NULL,
                character_p2  TEXT    NOT NULL,
                round_number  INTEGER NOT NULL,
                frame_number  INTEGER NOT NULL,
                p1_hp         INTEGER NOT NULL,
                p2_hp         INTEGER NOT NULL,
                p1_drive      INTEGER NOT NULL,
                p2_drive      INTEGER NOT NULL,
                p1_sa         INTEGER NOT NULL,
                p2_sa         INTEGER NOT NULL,
                is_punishable INTEGER NOT NULL,
                is_lethal     INTEGER NOT NULL,
                estimated_max_damage INTEGER NOT NULL,
                payload       TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_video_url   ON analysis_results (video_url)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at  ON analysis_results (created_at)
        """)
        # character フィルタ + created_at ソートを INDEX のみで処理するための複合インデックス
        # fetch_results(character=...) の WHERE (character_p1=? OR character_p2=?) ORDER BY created_at DESC
        # に対して SQLite が 2 つのインデックスを OR-merge できる
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_char_p1_date
            ON analysis_results (character_p1, created_at DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_char_p2_date
            ON analysis_results (character_p2, created_at DESC)
        """)
    logger.info("DB初期化完了: %s", _DB_PATH)


@contextmanager
def _connect() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    # WAL モード: 読み取りと書き込みを同時実行可能にする（ライブ監視の競合防止）
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_result(result: AnalyzeResponse) -> int:
    """解析結果をDBに保存し、採番されたIDを返す。"""
    payload = result.model_dump_json()
    created_at = datetime.now(timezone.utc).isoformat()

    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO analysis_results (
                created_at, video_url, character_p1, character_p2,
                round_number, frame_number,
                p1_hp, p2_hp, p1_drive, p2_drive, p1_sa, p2_sa,
                is_punishable, is_lethal, estimated_max_damage, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                result.video_url,
                result.player1_state.character.value,
                result.player2_state.character.value,
                result.round_number,
                result.frame_number,
                result.player1_state.hp,
                result.player2_state.hp,
                result.player1_state.drive_gauge,
                result.player2_state.drive_gauge,
                result.player1_state.sa_stock,
                result.player2_state.sa_stock,
                int(result.punish_opportunity.is_punishable),
                int(result.lethal_result.is_lethal),
                result.lethal_result.estimated_max_damage,
                payload,
            ),
        )
        row_id = cur.lastrowid

    logger.debug("解析結果を保存: id=%d url=%s", row_id, result.video_url[:60])
    return row_id


def fetch_results(
    video_url: str | None = None,
    character: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """保存済み解析結果を条件付きで取得する。

    Args:
        video_url: 絞り込むURL（部分一致）。None で全件対象。
        character: 絞り込むキャラクター名（p1 or p2）。None で全件対象。
        limit: 最大取得件数（デフォルト 50）。
        offset: スキップ件数（ページネーション用）。

    Returns:
        メタデータの辞書リスト（payload を含む）。
    """
    conditions = []
    params: list = []

    if video_url:
        conditions.append("video_url LIKE ?")
        params.append(f"%{video_url}%")
    if character:
        conditions.append("(character_p1 = ? OR character_p2 = ?)")
        params.extend([character, character])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.extend([limit, offset])

    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, created_at, video_url, character_p1, character_p2,
                   round_number, frame_number,
                   p1_hp, p2_hp, is_punishable, is_lethal, estimated_max_damage,
                   payload
            FROM analysis_results
            {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()

    return [dict(r) for r in rows]


def fetch_stats(video_url: str | None = None) -> dict:
    """集計統計を返す。

    Returns:
        total, punishable_rate, lethal_rate, avg_p1_hp, avg_p2_hp を含む辞書。
    """
    conditions = []
    params: list = []

    if video_url:
        conditions.append("video_url LIKE ?")
        params.append(f"%{video_url}%")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*)                        AS total,
                AVG(is_punishable) * 100        AS punishable_rate,
                AVG(is_lethal) * 100            AS lethal_rate,
                AVG(p1_hp)                      AS avg_p1_hp,
                AVG(p2_hp)                      AS avg_p2_hp,
                AVG(estimated_max_damage)       AS avg_max_damage
            FROM analysis_results
            {where}
            """,
            params,
        ).fetchone()

    return {
        "total": row["total"] or 0,
        "punishable_rate": round(row["punishable_rate"] or 0, 1),
        "lethal_rate": round(row["lethal_rate"] or 0, 1),
        "avg_p1_hp": round(row["avg_p1_hp"] or 0),
        "avg_p2_hp": round(row["avg_p2_hp"] or 0),
        "avg_max_damage": round(row["avg_max_damage"] or 0),
    }
