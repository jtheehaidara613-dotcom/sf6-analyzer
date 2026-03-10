"""database.py の単体テスト。

init_db・save_result・fetch_results・fetch_stats・インデックス・WALモードを検証する。
"""

import sqlite3
from pathlib import Path

import pytest

import database
from schemas import (
    CharacterName,
    CharacterState,
    FrameState,
    LethalResult,
    Position,
    PunishOpportunity,
    AnalyzeResponse,
)


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _use_tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """全テストで一時ディレクトリのDBを使う（本番DBを汚染しない）。"""
    monkeypatch.setattr(database, "_DB_PATH", tmp_path / "test_results.db")
    database.init_db()


def _make_response(
    video_url: str = "https://www.youtube.com/watch?v=test",
    char_p1: CharacterName = CharacterName.RYU,
    char_p2: CharacterName = CharacterName.CHUN_LI,
    p1_hp: int = 8000,
    p2_hp: int = 5000,
    is_punishable: bool = False,
    is_lethal: bool = False,
) -> AnalyzeResponse:
    """テスト用 AnalyzeResponse を生成するヘルパー。"""
    def _state(char: CharacterName, hp: int) -> CharacterState:
        return CharacterState(
            character=char,
            position=Position(x=400.0, y=600.0),
            hp=hp,
            drive_gauge=5000,
            sa_stock=1,
            frame_state=FrameState.NEUTRAL,
        )

    return AnalyzeResponse(
        video_url=video_url,
        frame_number=100,
        round_number=1,
        player1_state=_state(char_p1, p1_hp),
        player2_state=_state(char_p2, p2_hp),
        punish_opportunity=PunishOpportunity(
            is_punishable=is_punishable,
            frame_advantage=5 if is_punishable else -2,
            description="test",
        ),
        lethal_result=LethalResult(
            is_lethal=is_lethal,
            target_hp=p2_hp,
            estimated_max_damage=3000 if is_lethal else 1000,
            description="test",
        ),
    )


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_table_exists(self) -> None:
        """analysis_results テーブルが作成されること。"""
        with sqlite3.connect(database._DB_PATH) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_results'"
            ).fetchone()
        assert row is not None

    def test_indexes_exist(self) -> None:
        """必要なインデックスが全て作成されること。"""
        expected = {
            "idx_video_url",
            "idx_created_at",
            "idx_char_p1_date",
            "idx_char_p2_date",
        }
        with sqlite3.connect(database._DB_PATH) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='analysis_results'"
            ).fetchall()
        created = {r[0] for r in rows}
        assert expected <= created, f"不足インデックス: {expected - created}"

    def test_idempotent(self) -> None:
        """複数回呼んでもエラーにならないこと（IF NOT EXISTS）。"""
        database.init_db()
        database.init_db()


# ---------------------------------------------------------------------------
# WAL モード
# ---------------------------------------------------------------------------

class TestWalMode:
    def test_wal_mode_enabled(self) -> None:
        """_connect() 後に journal_mode が WAL であること。"""
        with database._connect() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.upper() == "WAL"


# ---------------------------------------------------------------------------
# save_result
# ---------------------------------------------------------------------------

class TestSaveResult:
    def test_returns_positive_id(self) -> None:
        """保存が成功し、正の整数 ID が返ること。"""
        row_id = database.save_result(_make_response())
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_sequential_ids(self) -> None:
        """連続保存でIDが単調増加すること。"""
        ids = [database.save_result(_make_response()) for _ in range(3)]
        assert ids == sorted(ids)
        assert len(set(ids)) == 3

    def test_fields_stored_correctly(self) -> None:
        """保存したフィールドが正しく格納されること。"""
        resp = _make_response(
            video_url="https://www.youtube.com/watch?v=abc123",
            char_p1=CharacterName.JP,
            char_p2=CharacterName.MARISA,
            p1_hp=9500,
            p2_hp=2000,
            is_punishable=True,
            is_lethal=True,
        )
        database.save_result(resp)

        rows = database.fetch_results()
        assert len(rows) == 1
        r = rows[0]
        assert r["video_url"] == "https://www.youtube.com/watch?v=abc123"
        assert r["character_p1"] == "jp"
        assert r["character_p2"] == "marisa"
        assert r["p1_hp"] == 9500
        assert r["p2_hp"] == 2000
        assert r["is_punishable"] == 1
        assert r["is_lethal"] == 1


# ---------------------------------------------------------------------------
# fetch_results
# ---------------------------------------------------------------------------

class TestFetchResults:
    def _populate(self) -> None:
        database.save_result(_make_response(
            video_url="https://www.youtube.com/watch?v=url1",
            char_p1=CharacterName.RYU,
            char_p2=CharacterName.CHUN_LI,
        ))
        database.save_result(_make_response(
            video_url="https://www.youtube.com/watch?v=url2",
            char_p1=CharacterName.JP,
            char_p2=CharacterName.MARISA,
        ))
        database.save_result(_make_response(
            video_url="https://www.youtube.com/watch?v=url2",
            char_p1=CharacterName.RYU,
            char_p2=CharacterName.MARISA,
        ))

    def test_no_filter_returns_all(self) -> None:
        self._populate()
        assert len(database.fetch_results()) == 3

    def test_video_url_filter(self) -> None:
        self._populate()
        rows = database.fetch_results(video_url="url2")
        assert len(rows) == 2
        for r in rows:
            assert "url2" in r["video_url"]

    def test_character_filter_p1(self) -> None:
        self._populate()
        rows = database.fetch_results(character="ryu")
        assert len(rows) == 2
        for r in rows:
            assert r["character_p1"] == "ryu" or r["character_p2"] == "ryu"

    def test_character_filter_p2(self) -> None:
        self._populate()
        rows = database.fetch_results(character="marisa")
        assert len(rows) == 2

    def test_character_filter_matches_p1_or_p2(self) -> None:
        """character は P1・P2 どちらにあってもヒットすること。"""
        self._populate()
        rows = database.fetch_results(character="jp")
        assert len(rows) == 1
        assert rows[0]["character_p1"] == "jp"

    def test_combined_filters(self) -> None:
        self._populate()
        rows = database.fetch_results(video_url="url2", character="marisa")
        assert len(rows) == 2

    def test_limit(self) -> None:
        self._populate()
        rows = database.fetch_results(limit=2)
        assert len(rows) == 2

    def test_offset(self) -> None:
        self._populate()
        all_rows = database.fetch_results()
        paged = database.fetch_results(limit=2, offset=1)
        assert len(paged) == 2
        assert paged[0]["id"] == all_rows[1]["id"]

    def test_order_by_created_at_desc(self) -> None:
        """デフォルトで created_at の降順に返ること。"""
        self._populate()
        rows = database.fetch_results()
        dates = [r["created_at"] for r in rows]
        assert dates == sorted(dates, reverse=True)

    def test_empty_returns_empty_list(self) -> None:
        assert database.fetch_results() == []

    def test_payload_is_valid_json(self) -> None:
        """payload フィールドが有効な JSON 文字列であること。"""
        import json
        database.save_result(_make_response())
        rows = database.fetch_results()
        payload = json.loads(rows[0]["payload"])
        assert "video_url" in payload


# ---------------------------------------------------------------------------
# fetch_stats
# ---------------------------------------------------------------------------

class TestFetchStats:
    def test_empty_db(self) -> None:
        stats = database.fetch_stats()
        assert stats["total"] == 0
        assert stats["punishable_rate"] == 0.0
        assert stats["lethal_rate"] == 0.0

    def test_total_count(self) -> None:
        for _ in range(5):
            database.save_result(_make_response())
        assert database.fetch_stats()["total"] == 5

    def test_punishable_rate(self) -> None:
        """punishable_rate が正しく計算されること（2/4 = 50.0%）。"""
        database.save_result(_make_response(is_punishable=True))
        database.save_result(_make_response(is_punishable=True))
        database.save_result(_make_response(is_punishable=False))
        database.save_result(_make_response(is_punishable=False))
        assert database.fetch_stats()["punishable_rate"] == 50.0

    def test_lethal_rate(self) -> None:
        """lethal_rate が正しく計算されること（1/4 = 25.0%）。"""
        database.save_result(_make_response(is_lethal=True))
        for _ in range(3):
            database.save_result(_make_response(is_lethal=False))
        assert database.fetch_stats()["lethal_rate"] == 25.0

    def test_video_url_filter(self) -> None:
        database.save_result(_make_response(video_url="https://www.youtube.com/watch?v=aaa"))
        database.save_result(_make_response(video_url="https://www.youtube.com/watch?v=bbb"))
        database.save_result(_make_response(video_url="https://www.youtube.com/watch?v=bbb"))
        stats = database.fetch_stats(video_url="bbb")
        assert stats["total"] == 2

    def test_returns_required_keys(self) -> None:
        keys = {"total", "punishable_rate", "lethal_rate", "avg_p1_hp", "avg_p2_hp", "avg_max_damage"}
        assert keys <= set(database.fetch_stats().keys())
