"""SF6 AI動画解析システム - CV抽出モジュール。

YouTube/Twitchの配信フレームからSF6のHUD情報を読み取る。

検出項目:
  - 体力（HP）バー比率
  - ドライブゲージ比率
  - SAゲージストック数

非対応（単一フレームでは判定困難）:
  - フレーム状態（RECOVERY/HITSTUNなど）
  - キャラクター自動識別（手動選択を使用）
"""

import logging

import cv2
import numpy as np

from schemas import CharacterName, CharacterState, FrameState, GameState, Position

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SF6 HUD 座標定義（1920×1080 基準）
# キャリブレーションが必要な場合はここを調整する
# ---------------------------------------------------------------------------

_HUD = {
    # P1（左側）: バーは左→右に伸びる
    "p1_hp":    (168, 47, 623, 61),   # x1, y1, x2, y2
    "p1_drive": (168, 73, 623, 83),
    "p1_sa":    (168, 89, 395, 103),
    # P2（右側）: バーは右→左に伸びる
    "p2_hp":    (1297, 47, 1752, 61),
    "p2_drive": (1297, 73, 1752, 83),
    "p2_sa":    (1525, 89, 1752, 103),
}

_MAX_HP: dict[CharacterName, int] = {
    CharacterName.RYU: 10000,
    CharacterName.CHUN_LI: 9500,
    CharacterName.JAMIE: 10500,
    CharacterName.LUKE: 10000,
    CharacterName.KEN: 10000,
    CharacterName.CAMMY: 9500,
    CharacterName.JP: 10000,
}


# ---------------------------------------------------------------------------
# フレームキャプチャ
# ---------------------------------------------------------------------------

def capture_frame_from_url(url: str) -> np.ndarray:
    """配信 / 動画 URL から1フレームを取得する。

    YouTube には yt-dlp、Twitch には streamlink を使用する。

    Args:
        url: 配信または動画のURL。

    Returns:
        BGR形式の numpy 配列（H×W×3）。

    Raises:
        RuntimeError: フレーム取得に失敗した場合。
    """
    if "twitch.tv" in url.lower():
        stream_url = _resolve_twitch_url(url)
    else:
        stream_url = _resolve_youtube_url(url)

    logger.info("ストリームURL解決完了: %s", stream_url[:80])

    cap = cv2.VideoCapture(stream_url)
    # ライブ配信の場合は末尾フレームを取得するため少し読み飛ばす
    for _ in range(5):
        ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise RuntimeError("フレームの取得に失敗しました（URLが無効か配信が終了している可能性があります）")

    logger.info("フレーム取得完了: %dx%d", frame.shape[1], frame.shape[0])
    return frame


def _resolve_youtube_url(url: str) -> str:
    """yt-dlp で YouTube のストリームURLを解決する。"""
    import yt_dlp  # 遅延インポート（未使用時のオーバーヘッド回避）

    ydl_opts = {
        "format": "best[height<=1080][ext=mp4]/best[height<=1080]/best",
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        # ライブ配信とVODで取得キーが異なる
        stream_url = info.get("url") or info["formats"][-1]["url"]
    return stream_url


def _resolve_twitch_url(url: str) -> str:
    """streamlink で Twitch のストリームURLを解決する。"""
    import subprocess

    result = subprocess.run(
        ["streamlink", "--stream-url", url, "best"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    stream_url = result.stdout.strip()
    if not stream_url:
        raise RuntimeError(
            f"Twitch ストリームURLの解決に失敗しました: {result.stderr.strip()}"
        )
    return stream_url


# ---------------------------------------------------------------------------
# HUD 読み取り
# ---------------------------------------------------------------------------

def _bar_ratio(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int,
               fill_from_right: bool = False) -> float:
    """バー領域の充填率（0.0〜1.0）を返す。

    バーの背景（暗いピクセル）を除外し、残ったピクセルの幅で比率を計算する。
    P1は左→右（fill_from_right=False）、P2は右→左（fill_from_right=True）。
    """
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return 0.0

    # 輝度が低いピクセル（背景）を除外
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    active = hsv[:, :, 2] > 25  # V値 > 25 を「バー部分」とみなす

    col_active = np.any(active, axis=0)  # 列ごとにいずれかの行がアクティブか
    if not np.any(col_active):
        return 0.0

    valid = np.where(col_active)[0]
    total = x2 - x1

    if fill_from_right:
        filled = total - int(valid[0])
    else:
        filled = int(valid[-1]) + 1

    return min(1.0, filled / total)


def _sa_stock_count(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> int:
    """SA ストック数（0〜3）を推定する。

    SA ゲージの点灯しているピクを連結成分解析でカウントする。
    """
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return 0

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)

    # ノイズ除去
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(binary)
    # 背景(label=0)を除き、面積が十分な成分をSAピップとしてカウント
    min_area = 8
    pip_count = sum(
        1 for i in range(1, num_labels)
        if stats[i, cv2.CC_STAT_AREA] >= min_area
    )
    return min(3, pip_count)


# ---------------------------------------------------------------------------
# メイン関数
# ---------------------------------------------------------------------------

def extract_game_state_from_frame(
    frame: np.ndarray,
    character_p1: CharacterName,
    character_p2: CharacterName,
    frame_number: int = 0,
    round_number: int = 1,
) -> GameState:
    """フレーム画像からゲーム状態を読み取る。

    体力・ドライブゲージ・SAストックを画像解析で取得する。
    フレーム状態（RECOVERY/HITSTUN）は単一フレームでは判定できないため
    NEUTRAL として返す。

    Args:
        frame: BGR形式のフレーム画像。
        character_p1: P1 のキャラクター。
        character_p2: P2 のキャラクター。
        frame_number: フレーム番号。
        round_number: ラウンド番号。

    Returns:
        解析結果の GameState。
    """
    # 1920×1080 に正規化
    h, w = frame.shape[:2]
    if (w, h) != (1920, 1080):
        frame = cv2.resize(frame, (1920, 1080))
        logger.debug("フレームを 1920×1080 にリサイズしました（元: %dx%d）", w, h)

    # HP 比率
    p1_hp_ratio = _bar_ratio(frame, *_HUD["p1_hp"], fill_from_right=False)
    p2_hp_ratio = _bar_ratio(frame, *_HUD["p2_hp"], fill_from_right=True)

    # ドライブゲージ比率
    p1_drive_ratio = _bar_ratio(frame, *_HUD["p1_drive"], fill_from_right=False)
    p2_drive_ratio = _bar_ratio(frame, *_HUD["p2_drive"], fill_from_right=True)

    # SA ストック
    p1_sa = _sa_stock_count(frame, *_HUD["p1_sa"])
    p2_sa = _sa_stock_count(frame, *_HUD["p2_sa"])

    p1_max_hp = _MAX_HP.get(character_p1, 10000)
    p2_max_hp = _MAX_HP.get(character_p2, 10000)

    logger.info(
        "HUD 読み取り完了 | P1 HP=%.1f%% Drive=%.1f%% SA=%d | P2 HP=%.1f%% Drive=%.1f%% SA=%d",
        p1_hp_ratio * 100, p1_drive_ratio * 100, p1_sa,
        p2_hp_ratio * 100, p2_drive_ratio * 100, p2_sa,
    )

    player1 = CharacterState(
        character=character_p1,
        position=Position(x=400.0, y=600.0),
        hp=int(p1_hp_ratio * p1_max_hp),
        drive_gauge=int(p1_drive_ratio * 10000),
        sa_stock=p1_sa,
        frame_state=FrameState.NEUTRAL,
        last_move=None,
        remaining_recovery_frames=0,
    )
    player2 = CharacterState(
        character=character_p2,
        position=Position(x=700.0, y=600.0),
        hp=int(p2_hp_ratio * p2_max_hp),
        drive_gauge=int(p2_drive_ratio * 10000),
        sa_stock=p2_sa,
        frame_state=FrameState.NEUTRAL,
        last_move=None,
        remaining_recovery_frames=0,
    )

    return GameState(
        player1=player1,
        player2=player2,
        frame_number=frame_number,
        round_number=round_number,
    )
