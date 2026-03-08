"""SF6 AI動画解析システム - ライブ配信リアルタイム解析モジュール。

バックグラウンドスレッドでライブストリームを継続ポーリングし、
最新のゲーム状態・反撃チャンス・リーサル判定を更新し続ける。

クラス:
    LiveSession   : 1セッション（1配信）の解析ループを管理するスレッド。
    SessionManager: 全セッションのライフサイクルを管理するシングルトン。
"""

import logging
import threading
import time
import uuid
from enum import Enum
from typing import Optional

import cv2

from schemas import AnalyzeResponse, CharacterName

logger = logging.getLogger(__name__)


class SessionStatus(str, Enum):
    """ライブセッションの状態。"""

    STARTING = "starting"
    RUNNING  = "running"
    STOPPED  = "stopped"
    ERROR    = "error"


class LiveSession:
    """1ライブ配信セッションの解析ループを管理する。

    start() でバックグラウンドスレッドを起動し、stop() で停止する。
    最新の解析結果は get_latest() でスレッドセーフに取得できる。
    """

    def __init__(
        self,
        session_id: str,
        video_url: str,
        character_p1: CharacterName,
        character_p2: CharacterName,
        interval_sec: float = 2.0,
    ) -> None:
        self.session_id    = session_id
        self.video_url     = video_url
        self.character_p1  = character_p1
        self.character_p2  = character_p2
        self.interval_sec  = interval_sec
        self.status        = SessionStatus.STARTING
        self.error_message: Optional[str] = None

        self._latest_result: Optional[AnalyzeResponse] = None
        self._lock        = threading.Lock()
        self._stop_event  = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """バックグラウンドスレッドを起動する。"""
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"live-{self.session_id[:8]}")
        self._thread.start()

    def stop(self) -> None:
        """解析ループを停止してスレッドの終了を待つ。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10.0)
        with self._lock:
            self.status = SessionStatus.STOPPED

    def get_latest(self) -> Optional[AnalyzeResponse]:
        """最新の解析結果をスレッドセーフに返す。まだ結果がなければ None。"""
        with self._lock:
            return self._latest_result

    # ------------------------------------------------------------------
    # バックグラウンドスレッド本体
    # ------------------------------------------------------------------

    def _run(self) -> None:
        from cv_extractor import (
            _resolve_twitch_url,
            _resolve_youtube_url,
            extract_game_state_from_frames,
            is_match_scene,
        )
        from logic_engine.lethal_calculator import calculate_lethal
        from logic_engine.punish_detector import detect_punish_opportunity

        # URL 解決（キャッシュ済みなら即時）
        try:
            if "twitch.tv" in self.video_url.lower():
                stream_url = _resolve_twitch_url(self.video_url)
            else:
                stream_url = _resolve_youtube_url(self.video_url)
        except Exception as exc:
            logger.error("URL解決失敗: %s", exc)
            with self._lock:
                self.status = SessionStatus.ERROR
                self.error_message = str(exc)
            return

        cap = cv2.VideoCapture(stream_url)
        frame_counter = 0

        with self._lock:
            self.status = SessionStatus.RUNNING

        logger.info("ライブセッション開始: %s | interval=%.1fs", self.session_id, self.interval_sec)

        while not self._stop_event.is_set():
            loop_start = time.monotonic()

            # バッファフラッシュ（ライブ配信の遅延を追い抜いて最新フレームへ）
            for _ in range(10):
                cap.read()

            # 解析用フレームを4枚取得
            frames = []
            for _ in range(4):
                ret, frame = cap.read()
                if ret and frame is not None:
                    frames.append(frame)

            if not frames:
                logger.warning("フレーム取得失敗 — 再接続を試みます")
                cap.release()
                self._stop_event.wait(2.0)
                if self._stop_event.is_set():
                    break
                cap = cv2.VideoCapture(stream_url)
                continue

            # 試合シーン以外はスキップ
            if not is_match_scene(frames[0]):
                logger.debug("非試合シーン — スキップ")
                self._stop_event.wait(max(0.0, self.interval_sec - (time.monotonic() - loop_start)))
                continue

            # ゲーム状態解析 + 反撃・リーサル判定
            try:
                frame_counter += 1
                game_state = extract_game_state_from_frames(
                    frames, self.character_p1, self.character_p2,
                    frame_number=frame_counter,
                )
                punish_result = detect_punish_opportunity(
                    attacker=game_state.player1,
                    defender=game_state.player2,
                )
                lethal_result = calculate_lethal(
                    attacker=game_state.player1,
                    defender=game_state.player2,
                )
                result = AnalyzeResponse(
                    video_url=self.video_url,
                    frame_number=game_state.frame_number,
                    round_number=game_state.round_number,
                    player1_state=game_state.player1,
                    player2_state=game_state.player2,
                    punish_opportunity=punish_result,
                    lethal_result=lethal_result,
                )
                with self._lock:
                    self._latest_result = result

                logger.info(
                    "ライブ解析完了 | punishable=%s lethal=%s | P1 HP=%d P2 HP=%d",
                    punish_result.is_punishable,
                    lethal_result.is_lethal,
                    game_state.player1.hp,
                    game_state.player2.hp,
                )

            except Exception as exc:
                logger.warning("解析エラー（スキップ）: %s", exc)

            elapsed = time.monotonic() - loop_start
            self._stop_event.wait(max(0.0, self.interval_sec - elapsed))

        cap.release()
        logger.info("ライブセッション終了: %s", self.session_id)


class SessionManager:
    """全ライブセッションのライフサイクルを管理するシングルトン。"""

    def __init__(self) -> None:
        self._sessions: dict[str, LiveSession] = {}
        self._lock = threading.Lock()

    def create(
        self,
        video_url: str,
        character_p1: CharacterName,
        character_p2: CharacterName,
        interval_sec: float = 2.0,
    ) -> LiveSession:
        """新しいライブセッションを生成して開始する。"""
        session_id = str(uuid.uuid4())
        session = LiveSession(
            session_id=session_id,
            video_url=video_url,
            character_p1=character_p1,
            character_p2=character_p2,
            interval_sec=interval_sec,
        )
        with self._lock:
            self._sessions[session_id] = session
        session.start()
        logger.info("セッション生成: %s", session_id)
        return session

    def get(self, session_id: str) -> Optional[LiveSession]:
        """セッションIDでセッションを取得する。存在しなければ None。"""
        return self._sessions.get(session_id)

    def stop(self, session_id: str) -> bool:
        """セッションを停止して削除する。存在しなければ False。"""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.stop()
        with self._lock:
            self._sessions.pop(session_id, None)
        logger.info("セッション停止: %s", session_id)
        return True

    def stop_all(self) -> None:
        """全セッションを停止する（アプリ終了時に呼ぶ）。"""
        for session in list(self._sessions.values()):
            session.stop()
        with self._lock:
            self._sessions.clear()
        logger.info("全セッション停止完了")


# アプリ全体で共有するシングルトン
session_manager = SessionManager()
