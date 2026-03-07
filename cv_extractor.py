"""SF6 AI動画解析システム - CV抽出モジュール。

YouTube/Twitchの配信フレームからSF6のHUD情報を読み取る。

検出項目:
  - 体力（HP）バー比率
  - ドライブゲージ比率
  - SAゲージストック数
  - フレーム状態（HITSTUN / RECOVERY / NEUTRAL）← 複数フレーム比較で推定

キャラクター自動識別は非対応（手動選択を使用）。

フレーム状態の推定ロジック:
  - 複数フレーム間の HP 変化 → HITSTUN の判定
  - 複数フレーム間のモーション量 → RECOVERY（低モーション）/ NEUTRAL の判定
  - 連続して動きが止まっているフレーム数 → 残り硬直フレーム数の推定
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

# キャラクターが映る画面領域（フレーム状態検出に使用）
_CHAR_ROI = {
    "p1": (0,   150, 960,  960),  # x1, y1, x2, y2
    "p2": (960, 150, 1920, 960),
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

# フレーム状態推定のしきい値
_HP_DELTA_HITSTUN = 0.012    # HP が 1.2% 以上減少 → HITSTUN
_MOTION_RECOVERY  = 0.004    # モーションがこれ以下 → RECOVERY 候補
_MOTION_NEUTRAL   = 0.015    # モーションがこれ以上 → NEUTRAL
_RECOVERY_EST_FRAMES = 20    # RECOVERY と判定したときの推定残りフレーム数


# ---------------------------------------------------------------------------
# フレームキャプチャ
# ---------------------------------------------------------------------------

def capture_frames_from_url(url: str, n_frames: int = 8) -> list[np.ndarray]:
    """配信 / 動画 URL から複数フレームを取得する。

    ストリームを開き、バッファクリア後に n_frames フレームを連続取得する。
    フレーム間隔は配信のフレームレート依存（30fps なら約 33ms/frame）。

    Args:
        url: 配信または動画のURL。
        n_frames: 取得するフレーム数（デフォルト: 8 ≒ 約 265ms@30fps）。

    Returns:
        BGR 形式の numpy 配列リスト（空の場合もある）。

    Raises:
        RuntimeError: フレーム取得に1枚も成功しなかった場合。
    """
    if "twitch.tv" in url.lower():
        stream_url = _resolve_twitch_url(url)
    else:
        stream_url = _resolve_youtube_url(url)

    logger.info("ストリームURL解決完了: %s", stream_url[:80])

    cap = cv2.VideoCapture(stream_url)

    # バッファをクリアして最新フレームに追いつく
    for _ in range(10):
        cap.read()

    frames: list[np.ndarray] = []
    for _ in range(n_frames):
        ret, frame = cap.read()
        if ret and frame is not None:
            frames.append(frame)

    cap.release()

    if not frames:
        raise RuntimeError(
            "フレームの取得に失敗しました（URLが無効か配信が終了している可能性があります）"
        )

    logger.info("フレーム取得完了: %d枚 (%dx%d)", len(frames), frames[0].shape[1], frames[0].shape[0])
    return frames


def _resolve_youtube_url(url: str) -> str:
    """yt-dlp で YouTube のストリームURLを解決する。"""
    import yt_dlp

    ydl_opts = {
        "format": "best[height<=1080][ext=mp4]/best[height<=1080]/best",
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
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
    """バー領域の充填率（0.0〜1.0）を返す。"""
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return 0.0

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    active = hsv[:, :, 2] > 25

    col_active = np.any(active, axis=0)
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
    """SA ストック数（0〜3）を推定する。"""
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return 0

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(binary)
    pip_count = sum(
        1 for i in range(1, num_labels)
        if stats[i, cv2.CC_STAT_AREA] >= 8
    )
    return min(3, pip_count)


# ---------------------------------------------------------------------------
# フレーム状態推定
# ---------------------------------------------------------------------------

def _motion_score(frame_a: np.ndarray, frame_b: np.ndarray,
                  roi: tuple[int, int, int, int]) -> float:
    """2フレーム間の ROI 内モーション量（0.0〜1.0）を返す。"""
    x1, y1, x2, y2 = roi
    a = cv2.cvtColor(frame_a[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY).astype(np.float32)
    b = cv2.cvtColor(frame_b[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(np.abs(a - b).mean() / 255.0)


def _normalize_frames(frames: list[np.ndarray]) -> list[np.ndarray]:
    """全フレームを 1920×1080 に正規化する。"""
    result = []
    for f in frames:
        h, w = f.shape[:2]
        if (w, h) != (1920, 1080):
            f = cv2.resize(f, (1920, 1080))
        result.append(f)
    return result


def detect_frame_state(
    frames: list[np.ndarray],
    player: str,
    hp_coords: tuple[int, int, int, int],
    fill_from_right: bool,
) -> tuple[FrameState, int]:
    """複数フレームからプレイヤーのフレーム状態と残り硬直を推定する。

    Args:
        frames: 正規化済みフレームリスト（時系列順）。
        player: "p1" または "p2"。
        hp_coords: HP バーの座標 (x1, y1, x2, y2)。
        fill_from_right: P2 の場合 True。

    Returns:
        (FrameState, remaining_recovery_frames) のタプル。
    """
    if len(frames) < 2:
        return FrameState.NEUTRAL, 0

    roi = _CHAR_ROI[player]

    # HP 変化量（フレーム列全体）
    hp_first = _bar_ratio(frames[0], *hp_coords, fill_from_right=fill_from_right)
    hp_last  = _bar_ratio(frames[-1], *hp_coords, fill_from_right=fill_from_right)
    hp_delta = hp_last - hp_first  # 負 = ダメージを受けた

    # フレーム間モーション量
    motion_scores = [
        _motion_score(frames[i], frames[i + 1], roi)
        for i in range(len(frames) - 1)
    ]
    avg_motion    = float(np.mean(motion_scores))
    recent_motion = motion_scores[-1]

    logger.debug(
        "%s: hp_delta=%.3f avg_motion=%.4f recent_motion=%.4f",
        player, hp_delta, avg_motion, recent_motion,
    )

    # --- 判定ロジック ---
    # 1) HP が有意に減少 → ヒットを受けた直後 → HITSTUN
    if hp_delta < -_HP_DELTA_HITSTUN:
        return FrameState.HITSTUN, 0

    # 2) 直近モーションが非常に低い → RECOVERY または BLOCKSTUN
    if recent_motion < _MOTION_RECOVERY and avg_motion < _MOTION_NEUTRAL:
        # 静止が続いているフレーム数をカウント
        static_count = 0
        for s in reversed(motion_scores):
            if s < _MOTION_RECOVERY:
                static_count += 1
            else:
                break
        # 残り硬直は「推定総硬直 - 既に静止しているフレーム数」
        remaining = max(0, _RECOVERY_EST_FRAMES - static_count * 2)
        return FrameState.RECOVERY, remaining

    # 3) その他 → NEUTRAL
    return FrameState.NEUTRAL, 0


# ---------------------------------------------------------------------------
# メイン関数
# ---------------------------------------------------------------------------

def extract_game_state_from_frames(
    frames: list[np.ndarray],
    character_p1: CharacterName,
    character_p2: CharacterName,
    frame_number: int = 0,
    round_number: int = 1,
) -> GameState:
    """複数フレームからゲーム状態を読み取る。

    最終フレームで HP/ゲージを取得し、フレーム列全体でフレーム状態を推定する。

    Args:
        frames: BGR 形式のフレームリスト（時系列順）。
        character_p1: P1 のキャラクター。
        character_p2: P2 のキャラクター。
        frame_number: フレーム番号。
        round_number: ラウンド番号。

    Returns:
        解析結果の GameState。
    """
    frames = _normalize_frames(frames)
    latest = frames[-1]

    # HP・ゲージ（最終フレームから読み取り）
    p1_hp_ratio    = _bar_ratio(latest, *_HUD["p1_hp"],    fill_from_right=False)
    p2_hp_ratio    = _bar_ratio(latest, *_HUD["p2_hp"],    fill_from_right=True)
    p1_drive_ratio = _bar_ratio(latest, *_HUD["p1_drive"], fill_from_right=False)
    p2_drive_ratio = _bar_ratio(latest, *_HUD["p2_drive"], fill_from_right=True)
    p1_sa = _sa_stock_count(latest, *_HUD["p1_sa"])
    p2_sa = _sa_stock_count(latest, *_HUD["p2_sa"])

    # フレーム状態（複数フレームから推定）
    p1_state, p1_recovery = detect_frame_state(frames, "p1", _HUD["p1_hp"], False)
    p2_state, p2_recovery = detect_frame_state(frames, "p2", _HUD["p2_hp"], True)

    p1_max_hp = _MAX_HP.get(character_p1, 10000)
    p2_max_hp = _MAX_HP.get(character_p2, 10000)

    logger.info(
        "CV解析完了 | P1 HP=%.1f%% %s | P2 HP=%.1f%% %s(残%dF)",
        p1_hp_ratio * 100, p1_state.value,
        p2_hp_ratio * 100, p2_state.value, p2_recovery,
    )

    player1 = CharacterState(
        character=character_p1,
        position=Position(x=400.0, y=600.0),
        hp=int(p1_hp_ratio * p1_max_hp),
        drive_gauge=int(p1_drive_ratio * 10000),
        sa_stock=p1_sa,
        frame_state=p1_state,
        last_move=None,
        remaining_recovery_frames=p1_recovery,
    )
    player2 = CharacterState(
        character=character_p2,
        position=Position(x=700.0, y=600.0),
        hp=int(p2_hp_ratio * p2_max_hp),
        drive_gauge=int(p2_drive_ratio * 10000),
        sa_stock=p2_sa,
        frame_state=p2_state,
        last_move=None,
        remaining_recovery_frames=p2_recovery,
    )

    return GameState(
        player1=player1,
        player2=player2,
        frame_number=frame_number,
        round_number=round_number,
    )
