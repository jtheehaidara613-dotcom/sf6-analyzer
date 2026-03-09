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
import time

import cv2
import numpy as np

from schemas import CharacterName, CharacterState, FrameState, GameState, Position

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# キャラクター略称 → CharacterName マッピング（SF6 HUD表示準拠）
# ---------------------------------------------------------------------------

_CHAR_ABBR_MAP: dict[str, CharacterName] = {
    "RYU":      CharacterName.RYU,
    "KEN":      CharacterName.KEN,
    "LUKE":     CharacterName.LUKE,
    "JAMIE":    CharacterName.JAMIE,
    "CHUN-LI":  CharacterName.CHUN_LI,
    "CHUNLI":   CharacterName.CHUN_LI,
    "CHUN LI":  CharacterName.CHUN_LI,
    "GUILE":    CharacterName.GUILE,
    "KIMBERLY": CharacterName.KIMBERLY,
    "KIM":      CharacterName.KIMBERLY,
    "JURI":     CharacterName.JURI,
    "BLANKA":   CharacterName.BLANKA,
    "DHALSIM":  CharacterName.DHALSIM,
    "DEE JAY":  CharacterName.DEE_JAY,
    "DEEJAY":   CharacterName.DEE_JAY,
    "MANON":    CharacterName.MANON,
    "MARISA":   CharacterName.MARISA,
    "JP":       CharacterName.JP,
    "J.P.":     CharacterName.JP,
    "ZANGIEF":  CharacterName.ZANGIEF,
    "LILY":     CharacterName.LILY,
    "CAMMY":    CharacterName.CAMMY,
    "RASHID":   CharacterName.RASHID,
    "AKI":      CharacterName.AKI,
    "ED":       CharacterName.ED,
    "AKUMA":    CharacterName.AKUMA,
    "M.BISON":  CharacterName.M_BISON,
    "MBISON":   CharacterName.M_BISON,
    "BISON":    CharacterName.M_BISON,
    "TERRY":    CharacterName.TERRY,
    "MAI":      CharacterName.MAI,
    "ELENA":    CharacterName.ELENA,
}

# SF6 HUD上のキャラクター略称テキスト領域（1920×1080 基準）
_CHAR_NAME_ROI = {
    "p1": (0,  30, 110, 60),   # P1 左端 (x1, y1, x2, y2)
    "p2": (1810, 30, 1920, 60), # P2 右端
}

# ---------------------------------------------------------------------------
# yt-dlp URL キャッシュ（YouTube URLは有効期限があるため TTL を設定）
# ---------------------------------------------------------------------------

_URL_CACHE_TTL = 3600.0  # 1時間（YouTube URLの一般的な有効期限）
_url_cache: dict[str, tuple[str, float]] = {}  # source_url → (stream_url, expire_at)


def _get_cached_stream_url(source_url: str) -> str | None:
    """キャッシュから有効なストリームURLを返す。期限切れまたは未登録の場合は None。"""
    entry = _url_cache.get(source_url)
    if entry and time.time() < entry[1]:
        logger.debug("URLキャッシュヒット: %s", source_url[:60])
        return entry[0]
    return None


def _set_cached_stream_url(source_url: str, stream_url: str) -> None:
    """ストリームURLをキャッシュに登録する。"""
    _url_cache[source_url] = (stream_url, time.time() + _URL_CACHE_TTL)
    logger.debug("URLキャッシュ登録: %s → %s…", source_url[:60], stream_url[:60])

# ---------------------------------------------------------------------------
# SF6 HUD 座標定義（1920×1080 基準）
# キャリブレーションが必要な場合はここを調整する
# ---------------------------------------------------------------------------

_HUD = {
    # P1（左側）: バーは左→右に伸びる
    # NOTE: x=90〜200 はキャラアバター領域のため除外
    "p1_hp":    (200,  66, 856,  93),   # x1, y1, x2, y2
    "p1_drive": (712, 114, 828, 132),  # P1ドライブ（中央左、左詰め）
    "p1_sa":    (700, 88, 870, 135),   # SAストック指標（菱形アイコン群）
    # P2（右側）: HPは左→右、Driveは右→左に伸びる
    # NOTE: x=1750〜1855 はキャラアバター領域のため除外
    "p2_hp":    (1184, 66, 1750, 93),
    "p2_drive": (1092, 114, 1208, 132), # P2ドライブ（中央右、右詰め）
    "p2_sa":    (1050, 88, 1220, 135),  # SAストック指標（菱形アイコン群）
    # ラウンド勝利ドット（タイマー両脇）
    "p1_round": (870, 58, 950, 82),    # P1側の2個の勝利ドット
    "p2_round": (970, 58, 1050, 82),   # P2側の2個の勝利ドット
}

# ゲージ検出に使用する色相範囲 (OpenCV HSV, 0-180スケール)
_HUD_HUE = {
    # SF6 HP バーはキャラごとに色が異なる（例: JP=青、Juri=ピンク、Ryu=赤等）
    # None を指定すると _bar_ratio が「高彩度・高輝度」全色で検出するモードになる
    "p1_hp":    None,   # 任意色（キャラ固有色に対応）
    "p2_hp":    None,   # 任意色（キャラ固有色に対応）
    "p1_drive": (15,  55),               # 黄色〜黄緑（通常時）
    "p2_drive": (15,  55),               # 黄色〜黄緑（通常時）
    # バーンアウト中: ドライブゲージが赤くフラッシュする
    "drive_burnout": [(0, 15), (155, 180)],  # 赤〜オレンジ（バーンアウト色）
}

# バーンアウト判定: ドライブゲージ領域の赤ピクセル比率がこれ以上 → バーンアウト中
_BURNOUT_RED_THRESH = 0.20

# キャラクターが映る画面領域（フレーム状態検出に使用）
_CHAR_ROI = {
    "p1": (0,   150, 960,  960),  # x1, y1, x2, y2
    "p2": (960, 150, 1920, 960),
}

_MAX_HP: dict[CharacterName, int] = {
    # SF6 各キャラクターの最大HP（公式値）
    CharacterName.RYU:      10000,
    CharacterName.KEN:      10000,
    CharacterName.LUKE:     10000,
    CharacterName.JAMIE:    10500,
    CharacterName.CHUN_LI:  9500,
    CharacterName.CAMMY:    9500,
    CharacterName.JURI:     9500,
    CharacterName.KIMBERLY: 9500,
    CharacterName.GUILE:    10000,
    CharacterName.ZANGIEF:  11000,
    CharacterName.BLANKA:   10500,
    CharacterName.DHALSIM:  9000,
    CharacterName.DEE_JAY:  10000,
    CharacterName.MANON:    10000,
    CharacterName.MARISA:   11000,
    CharacterName.LILY:     10000,
    CharacterName.RASHID:   9500,
    CharacterName.ED:       9500,
    CharacterName.AKI:      9500,
    CharacterName.JP:       10000,
    CharacterName.AKUMA:    9000,
    CharacterName.M_BISON:  10500,
    CharacterName.TERRY:    10000,
    CharacterName.MAI:      9500,
    CharacterName.ELENA:    9500,
}

# フレーム状態推定のしきい値
_HP_DELTA_HITSTUN = 0.012    # HP が 1.2% 以上減少 → HITSTUN
_MOTION_RECOVERY  = 0.004    # モーションがこれ以下 → RECOVERY 候補
_MOTION_NEUTRAL   = 0.015    # モーションがこれ以上 → NEUTRAL
_RECOVERY_EST_FRAMES = 20    # RECOVERY と判定したときの推定残りフレーム数

# SAゲージストック数判定閾値（輝度ピクセル比率）
# 実際の動画で calibrate_cv.py を使ってキャリブレーション可能
_SA_THRESH_3 = 0.42   # この比率以上 → 3ストック
_SA_THRESH_2 = 0.24   # この比率以上 → 2ストック
_SA_THRESH_1 = 0.10   # この比率以上 → 1ストック


# ---------------------------------------------------------------------------
# フレームキャプチャ
# ---------------------------------------------------------------------------

def capture_frames_from_url(url: str, n_frames: int = 8, start_sec: float | None = None) -> list[np.ndarray]:
    """配信 / 動画 URL から複数フレームを取得する。

    ストリームを開き、バッファクリア後に n_frames フレームを連続取得する。
    フレーム間隔は配信のフレームレート依存（30fps なら約 33ms/frame）。

    Args:
        url: 配信または動画のURL。
        n_frames: 取得するフレーム数（デフォルト: 8 ≒ 約 265ms@30fps）。
        start_sec: シーク先の秒数（指定時はその位置から取得。None の場合は先頭 / 最新フレーム）。

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

    if start_sec is not None:
        cap.set(cv2.CAP_PROP_POS_MSEC, start_sec * 1000)
        logger.info("シーク完了: %.1f秒", start_sec)
    else:
        # ライブ配信: バッファをクリアして最新フレームに追いつく
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


def _resolve_youtube_url(url: str, low_res: bool = False) -> str:
    """yt-dlp で YouTube のストリームURLを解決する（キャッシュあり）。

    Args:
        url: YouTube の動画 / 配信 URL。
        low_res: True のときスキャン用低解像度（480p 以下）を返す。
                 シーン判定のみに使用し、解析用フレームには False を使う。
    """
    cache_key = f"{url}::{'lr' if low_res else 'hr'}"
    cached = _get_cached_stream_url(cache_key)
    if cached:
        return cached

    import yt_dlp

    if low_res:
        fmt = (
            "bestvideo[height<=480][ext=mp4][vcodec^=avc]"
            "/bestvideo[height<=480][ext=mp4]"
            "/bestvideo[height<=480][vcodec^=avc]"
            "/bestvideo[height<=480]"
            "/worstvideo/worst"
        )
    else:
        fmt = (
            "bestvideo[height>=1080][vcodec^=avc]"
            "/bestvideo[height>=1080]"
            "/bestvideo[height>=720][vcodec^=avc]"
            "/bestvideo[height>=720]"
            "/bestvideo/best"
        )

    ydl_opts = {"format": fmt, "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        stream_url = info.get("url") or info["formats"][-1]["url"]

    _set_cached_stream_url(cache_key, stream_url)
    return stream_url


def _resolve_twitch_url(url: str) -> str:
    """streamlink で Twitch のストリームURLを解決する（キャッシュあり）。"""
    cached = _get_cached_stream_url(url)
    if cached:
        return cached

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

    _set_cached_stream_url(url, stream_url)
    return stream_url


# ---------------------------------------------------------------------------
# HUD 読み取り
# ---------------------------------------------------------------------------

def _bar_ratio(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int,
               fill_from_right: bool = False,
               hue_range: tuple[int, int] | list[tuple[int, int]] | None = None) -> float:
    """バー領域の充填率（0.0〜1.0）を返す。

    hue_range が指定された場合は色相フィルタで対象色のみを検出する。
    複数の色相範囲をリストで渡すと OR で合成する（色変化するバー用）。
    指定しない場合は輝度閾値（V>25）にフォールバックする。
    """
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return 0.0

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    if hue_range is not None:
        ranges = [hue_range] if isinstance(hue_range, tuple) else hue_range
        active = np.zeros(hsv.shape[:2], dtype=bool)
        for h_min, h_max in ranges:
            active |= (
                (hsv[:, :, 0] >= h_min) & (hsv[:, :, 0] <= h_max)
                & (hsv[:, :, 1] > 80)
                & (hsv[:, :, 2] > 80)
            )
    else:
        # hue_range=None の場合: 高彩度・高輝度のピクセル全色を対象とする
        # これにより JP(青), Juri(ピンク), Ryu(赤) 等キャラ固有色のHPバーを統一検出
        active = (hsv[:, :, 1] > 80) & (hsv[:, :, 2] > 80)

    # 列ごとの密度で判定（行数の15%以上がマッチした列のみ有効）
    col_density = active.sum(axis=0) / active.shape[0]
    col_active = col_density >= 0.15

    if not np.any(col_active):
        return 0.0

    # アンチエイリアス等による小さなギャップ（≤10列）を埋める
    filled_active = col_active.copy()
    gap = 0
    last_true = -1
    for i, v in enumerate(col_active):
        if v:
            if last_true >= 0 and gap <= 10:
                filled_active[last_true + 1:i] = True
            gap = 0
            last_true = i
        else:
            gap += 1

    # ROI 実サイズを基準にする（フレーム解像度が _HUD 座標より小さい場合に対応）
    total = roi.shape[1]

    if fill_from_right:
        # 右端からの連続ブロック長を求める
        edge = total - 1
        while edge >= 0 and not filled_active[edge]:
            edge -= 1
        if edge < 0:
            return 0.0
        start = edge
        while start > 0 and filled_active[start - 1]:
            start -= 1
        filled = total - start
    else:
        # 左端からの連続ブロック長を求める
        edge = 0
        while edge < total and filled_active[edge]:
            edge += 1
        filled = edge

    return min(1.0, filled / total)


def _sa_stock_count(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int,
                    label: str = "") -> int:
    """SA ストック数（0〜3）を推定する。

    SF6 のSAゲージは六角形アイコン1個の輝度で表現される。
    アイコン領域の紫/白ピクセル比率でストックの有無を判定し、
    輝度レベルから 0〜3 を推定する。

    閾値は _SA_THRESH_1 / _SA_THRESH_2 / _SA_THRESH_3 で調整できる。
    calibrate_cv.py を使ってraw ratioを確認しキャリブレーションすること。

    Args:
        frame: 正規化済みフレーム。
        x1, y1, x2, y2: SAアイコン領域の座標。
        label: デバッグログ用のラベル（例: "p1", "p2"）。

    Returns:
        SAストック数 (0〜3)。
    """
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return 0

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    # 紫〜白（SAアイコンの発光色: 色相 120-170、低彩度の白も含む）
    bright_mask = (
        ((hsv[:, :, 0] >= 120) & (hsv[:, :, 0] <= 170) & (hsv[:, :, 1] > 50))
        | (hsv[:, :, 2] > 200)
    )
    ratio = bright_mask.sum() / bright_mask.size
    logger.debug("SA ratio %s=%.4f (thresholds: 1=%.2f 2=%.2f 3=%.2f)",
                 label, ratio, _SA_THRESH_1, _SA_THRESH_2, _SA_THRESH_3)

    if ratio >= _SA_THRESH_3:
        return 3
    elif ratio >= _SA_THRESH_2:
        return 2
    elif ratio >= _SA_THRESH_1:
        return 1
    return 0


# ---------------------------------------------------------------------------
# バーンアウト状態検出
# ---------------------------------------------------------------------------

def is_in_burnout(frame: np.ndarray, player: str) -> bool:
    """プレイヤーがバーンアウト状態かどうかを判定する。

    SF6のバーンアウト中はドライブゲージ領域が赤くフラッシュする。
    通常時の黄緑色ピクセルがなく、代わりに赤ピクセルが多い場合に True を返す。

    Args:
        frame: 正規化済みフレーム（1920×1080）。
        player: "p1" または "p2"。

    Returns:
        バーンアウト中なら True。
    """
    key = f"{player}_drive"
    x1, y1, x2, y2 = _HUD[key]
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return False

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    # 通常のドライブ色（黄緑）が存在するか
    normal_mask = (
        (hsv[:, :, 0] >= 15) & (hsv[:, :, 0] <= 55)
        & (hsv[:, :, 1] > 80) & (hsv[:, :, 2] > 80)
    )
    if normal_mask.sum() / normal_mask.size > 0.05:
        return False  # 黄緑が残っている → 通常状態

    # 赤系ピクセルが多ければバーンアウト
    red_ranges = _HUD_HUE["drive_burnout"]
    red_mask = np.zeros(hsv.shape[:2], dtype=bool)
    for h_min, h_max in red_ranges:
        red_mask |= (
            (hsv[:, :, 0] >= h_min) & (hsv[:, :, 0] <= h_max)
            & (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 80)
        )
    ratio = red_mask.sum() / red_mask.size
    result = ratio >= _BURNOUT_RED_THRESH
    logger.debug("バーンアウト判定 %s: red_ratio=%.3f → %s", player, ratio, result)
    return result


def drive_gauge_ratio(frame: np.ndarray, player: str) -> tuple[float, bool]:
    """ドライブゲージ比率とバーンアウト状態を返す。

    バーンアウト中は比率 0.0 を返す（ゲージが空の状態として扱う）。

    Args:
        frame: 正規化済みフレーム。
        player: "p1" または "p2"。

    Returns:
        (ゲージ比率 0.0〜1.0, バーンアウト中かどうか) のタプル。
    """
    burnout = is_in_burnout(frame, player)
    if burnout:
        return 0.0, True

    key = f"{player}_drive"
    fill_right = (player == "p2")
    ratio = _bar_ratio(frame, *_HUD[key], fill_from_right=fill_right, hue_range=_HUD_HUE[key])
    return ratio, False


# ---------------------------------------------------------------------------
# ラウンド番号検出
# ---------------------------------------------------------------------------

def _round_wins(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> int:
    """勝利ドット領域から何ラウンド勝ったかを返す（0〜2）。

    SF6のラウンド勝利ドットは「未勝利=暗い円」「勝利=明るい金色/白色の円」で表される。
    各ドットを左右に半分して個別に輝度を計算し、有効なドット数をカウントする。

    Args:
        frame: 正規化済みフレーム（1920×1080）。
        x1, y1, x2, y2: ラウンドドット領域の座標。

    Returns:
        勝利ラウンド数 (0, 1, 2)。
    """
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return 0

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    h, w = roi.shape[:2]
    mid = w // 2

    wins = 0
    for dot_roi in (hsv[:, :mid], hsv[:, mid:]):
        if dot_roi.size == 0:
            continue
        # 金色/白色の輝度が高いピクセルを検出（勝利ドット: H=15-40 高S 高V、または白V>220）
        gold_mask = (
            (dot_roi[:, :, 0] >= 15) & (dot_roi[:, :, 0] <= 45)
            & (dot_roi[:, :, 1] > 60)
            & (dot_roi[:, :, 2] > 150)
        )
        white_mask = dot_roi[:, :, 2] > 220
        active = gold_mask | white_mask
        ratio = active.sum() / active.size
        if ratio >= 0.10:
            wins += 1

    return wins


def detect_round_number(frame: np.ndarray) -> int:
    """フレームのラウンド勝利ドットから現在のラウンド番号を推定する。

    P1・P2のラウンド勝利数の合計 + 1 で現ラウンドを計算する。
    例: P1が1勝 → ラウンド2、P1/P2が1勝ずつ → ラウンド3。

    Args:
        frame: BGR 形式の 1 フレーム（任意解像度。内部で 1920×1080 に正規化）。

    Returns:
        推定ラウンド番号（1〜3）。
    """
    h, w = frame.shape[:2]
    if (w, h) != (1920, 1080):
        frame = cv2.resize(frame, (1920, 1080))

    p1_wins = _round_wins(frame, *_HUD["p1_round"])
    p2_wins = _round_wins(frame, *_HUD["p2_round"])
    round_number = min(3, p1_wins + p2_wins + 1)

    logger.debug("ラウンド番号推定: P1勝=%d P2勝=%d → ラウンド%d", p1_wins, p2_wins, round_number)
    return round_number


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

    # 2) 「動いていた→今は止まっている」 → RECOVERY または BLOCKSTUN
    # avg_motion > threshold: フレーム群の中で動きがあった
    # recent_motion < threshold: 直近は静止している
    # これにより、ずっと静止しているニュートラル状態を誤検知しない
    if recent_motion < _MOTION_RECOVERY and avg_motion > _MOTION_RECOVERY:
        # 静止が続いているフレーム数をカウント
        static_count = 0
        for s in reversed(motion_scores):
            if s < _MOTION_RECOVERY:
                static_count += 1
            else:
                break
        # HP変化が大きいほど長いコンボ → 硬直フレーム数も多めに見積もる
        hp_factor = int(abs(hp_delta) * 400)   # 10% ダメージ → +40F 相当（上限で丸める）
        est_frames = min(40, _RECOVERY_EST_FRAMES + hp_factor)
        remaining = max(0, est_frames - static_count * 2)
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

    # HP バー（複数フレームの中央値でノイズを除去）
    # P1: 左詰め / P2: 右詰め
    def _med_hp(key: str, fill_right: bool) -> float:
        ratios = [_bar_ratio(f, *_HUD[key], fill_from_right=fill_right,
                             hue_range=_HUD_HUE[key]) for f in frames]
        return float(np.median(ratios))

    p1_hp_ratio = _med_hp("p1_hp", fill_right=False)
    p2_hp_ratio = _med_hp("p2_hp", fill_right=True)

    # ドライブゲージ: バーンアウト判定込みで最終フレームで確定
    # バーンアウト中は drive_gauge=0 として扱う
    p1_drive_ratio, p1_burnout = drive_gauge_ratio(latest, "p1")
    p2_drive_ratio, p2_burnout = drive_gauge_ratio(latest, "p2")
    if p1_burnout:
        logger.info("P1 バーンアウト中（ドライブ=0）")
    if p2_burnout:
        logger.info("P2 バーンアウト中（ドライブ=0）")
    p1_sa = _sa_stock_count(latest, *_HUD["p1_sa"], label="p1")
    p2_sa = _sa_stock_count(latest, *_HUD["p2_sa"], label="p2")

    # ラウンド番号（引数で明示された場合はそちらを優先、1の場合は自動検出を試みる）
    if round_number == 1:
        detected_round = detect_round_number(latest)
        if detected_round > 1:
            round_number = detected_round

    # ラウンド開始直後（両者 HP ≥ 97%）はフレーム状態推定をスキップ
    if p1_hp_ratio >= 0.97 and p2_hp_ratio >= 0.97:
        p1_state, p1_recovery = FrameState.NEUTRAL, 0
        p2_state, p2_recovery = FrameState.NEUTRAL, 0
    else:
        p1_state, p1_recovery = detect_frame_state(frames, "p1", _HUD["p1_hp"], False)  # P1 左詰め
        p2_state, p2_recovery = detect_frame_state(frames, "p2", _HUD["p2_hp"], True)   # P2 右詰め

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


# ---------------------------------------------------------------------------
# キャラクター自動識別
# ---------------------------------------------------------------------------

def detect_characters_from_frame(
    frame: np.ndarray,
) -> tuple[CharacterName | None, CharacterName | None]:
    """フレームのHUD略称テキストからキャラクターを識別する。

    P1（左端）・P2（右端）のキャラクター名略称を easyocr で読み取り、
    CharacterName に変換する。読み取れない場合は None を返す。

    Args:
        frame: BGR 形式の 1 フレーム（1920×1080 推奨）。

    Returns:
        (p1キャラクター, p2キャラクター) のタプル。未検出は None。
    """
    h, w = frame.shape[:2]
    if (w, h) != (1920, 1080):
        frame = cv2.resize(frame, (1920, 1080))

    try:
        import easyocr
    except ImportError:
        logger.warning("easyocr が未インストールのためキャラクター自動識別をスキップ")
        return None, None

    # Reader はモデルロードが重いのでモジュールレベルでキャッシュする
    if not hasattr(detect_characters_from_frame, "_reader"):
        detect_characters_from_frame._reader = easyocr.Reader(
            ["en"], gpu=False, verbose=False
        )
    reader = detect_characters_from_frame._reader

    results: list[CharacterName | None] = []
    for side in ("p1", "p2"):
        x1, y1, x2, y2 = _CHAR_NAME_ROI[side]
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            results.append(None)
            continue

        # 3倍拡大して認識精度を上げる
        enlarged = cv2.resize(roi, None, fx=3, fy=3, interpolation=cv2.INTER_LINEAR)
        texts = reader.readtext(
            enlarged, detail=0,
            allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ.-_ ",
        )
        raw = " ".join(texts).upper().strip()
        char = _CHAR_ABBR_MAP.get(raw)
        if char is None:
            # 記号除去フォールバック（例: "J.P" → "JP"）
            raw_clean = raw.replace(".", "").replace(" ", "").replace("-", "")
            for abbr, c in _CHAR_ABBR_MAP.items():
                if raw_clean == abbr.replace(".", "").replace(" ", "").replace("-", ""):
                    char = c
                    break
        if char is None and raw:
            # ファジーマッチ（OCR誤読対策: D↔I/v/u など）
            # 編集距離1以内で同じ先頭文字・同じ長さを優先
            best_abbr, best_dist = None, 999
            for abbr in _CHAR_ABBR_MAP:
                if len(abbr) != len(raw):
                    continue
                dist = sum(a != b for a, b in zip(raw, abbr))
                if dist < best_dist:
                    best_dist, best_abbr = dist, abbr
            if best_abbr is not None and best_dist <= 1:
                char = _CHAR_ABBR_MAP[best_abbr]
                logger.debug("ファジーマッチ %s: %r → %r (dist=%d)", side, raw, best_abbr, best_dist)
        logger.debug("キャラクター検出 %s: raw=%r → %s", side, raw, char)
        results.append(char)

    return results[0], results[1]


def detect_characters_from_url(
    url: str,
    scan_interval_sec: float = 30.0,
    max_scan_sec: float = 120.0,
) -> tuple[CharacterName | None, CharacterName | None]:
    """動画の序盤から試合シーンを探してキャラクターを識別する。

    Args:
        url: YouTube / Twitch の動画URL。
        scan_interval_sec: スキャン間隔（秒）。
        max_scan_sec: スキャン上限（秒）。

    Returns:
        (p1キャラクター, p2キャラクター) のタプル。未検出は None。
    """
    if "twitch.tv" in url.lower():
        stream_url = _resolve_twitch_url(url)
    else:
        stream_url = _resolve_youtube_url(url)

    cap = cv2.VideoCapture(stream_url)
    t = 0.0
    p1: CharacterName | None = None
    p2: CharacterName | None = None

    while t <= max_scan_sec:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ret, frame = cap.read()
        if not ret or frame is None:
            break

        if is_match_scene(frame):
            p1, p2 = detect_characters_from_frame(frame)
            if p1 is not None and p2 is not None:
                logger.info("キャラクター識別成功: P1=%s P2=%s @ %.0f秒", p1.value, p2.value, t)
                break

        t += scan_interval_sec

    cap.release()
    return p1, p2


# ---------------------------------------------------------------------------
# 試合シーン自動検出
# ---------------------------------------------------------------------------

# HUD判定の閾値
_HUD_HP_MIN          = 0.05   # 両者のHP比率がこれ以上 → 試合中と判定
_HUD_DRIVE_MIN       = 0.05   # いずれかのDriveがこれ以上 → HUDが存在と判定
_TIMER_ORANGE_MAX    = 0.30   # タイマー領域のオレンジ率がこれ以上 → 非試合画面（ロビー等）

# タイマー領域（試合中は暗い背景に白数字、ロビーでは背景色で埋まる）
_TIMER_ROI = (910, 12, 1010, 58)


def _timer_has_digits(timer_hsv: np.ndarray) -> bool:
    """タイマー領域に白い数字が表示されているかを判定する。

    試合中のタイマーは暗い背景に白い数字（低彩度・高輝度ピクセル）が並ぶ。
    ローディング画面やリプレイ選択画面では数字が表示されない。

    Args:
        timer_hsv: タイマー ROI の HSV 画像。

    Returns:
        白い数字らしきピクセルが 5〜45% の範囲にある場合 True。
    """
    # 白/明るいピクセル: 彩度が低く輝度が高い（数字の白い部分）
    white_mask = (timer_hsv[:, :, 1] < 60) & (timer_hsv[:, :, 2] > 180)
    ratio = white_mask.sum() / max(1, white_mask.size)
    return 0.05 <= ratio <= 0.45


def is_match_scene(frame: np.ndarray) -> bool:
    """フレームが「試合中」かどうかを簡易判定する。

    両プレイヤーのHPバーとDriveゲージが読み取れる場合に True を返す。
    ロビー・ローディング・リプレイ選択画面などでは False を返す。

    判定フロー（早期リターンで高速化）:
      1. タイマー領域がオレンジ → ロビー画面 → False
      2. タイマー領域に白い数字がない → 非試合画面 → False
      3. P1/P2 HPバーが読み取れない → False
      4. P1/P2 Driveゲージが両方ゼロ → False

    Args:
        frame: BGR 形式の 1 フレーム（任意解像度。1920×1080 に正規化して処理）。

    Returns:
        試合中と推定されれば True。
    """
    h, w = frame.shape[:2]
    if (w, h) != (1920, 1080):
        frame = cv2.resize(frame, (1920, 1080))

    # タイマー領域を一度だけ HSV 変換
    tx1, ty1, tx2, ty2 = _TIMER_ROI
    timer_hsv = cv2.cvtColor(frame[ty1:ty2, tx1:tx2], cv2.COLOR_BGR2HSV)

    # 1. オレンジ背景チェック（ロビー誤検知防止）
    orange_mask = (
        (timer_hsv[:, :, 0] >= 15) & (timer_hsv[:, :, 0] <= 55)
        & (timer_hsv[:, :, 1] > 80) & (timer_hsv[:, :, 2] > 80)
    )
    if orange_mask.sum() / orange_mask.size > _TIMER_ORANGE_MAX:
        logger.debug("タイマー領域がオレンジのため非試合画面と判定")
        return False

    # 2. HP バーチェック（両者が最低限のHPを持っていれば試合中と判定）
    # NOTE: SF6はキャラ固有色のHPバー（JP=青, Juri=ピンク等）があるため
    #       hue_range=None で高彩度・高輝度の任意色を検出する
    p1_hp = _bar_ratio(frame, *_HUD["p1_hp"], fill_from_right=True, hue_range=_HUD_HUE["p1_hp"])
    p2_hp = _bar_ratio(frame, *_HUD["p2_hp"], hue_range=_HUD_HUE["p2_hp"])
    if p1_hp < _HUD_HP_MIN or p2_hp < _HUD_HP_MIN:
        return False

    # 4. Drive ゲージチェック
    p1_drive = _bar_ratio(frame, *_HUD["p1_drive"], hue_range=_HUD_HUE["p1_drive"])
    p2_drive = _bar_ratio(frame, *_HUD["p2_drive"], fill_from_right=True, hue_range=_HUD_HUE["p2_drive"])
    if p1_drive < _HUD_DRIVE_MIN and p2_drive < _HUD_DRIVE_MIN:
        return False

    return True


def _open_cap(url: str) -> cv2.VideoCapture:
    """URL を解決して VideoCapture を返すヘルパー。"""
    if "twitch.tv" in url.lower():
        stream_url = _resolve_twitch_url(url)
    else:
        stream_url = _resolve_youtube_url(url)
    return cv2.VideoCapture(stream_url)


def _scan_limit(cap: cv2.VideoCapture, max_duration_sec: float | None) -> float:
    """スキャン上限秒数を決定するヘルパー。"""
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    total_sec = total_frames / fps if total_frames > 0 else None

    if max_duration_sec is not None:
        return max_duration_sec
    if total_sec is not None:
        return total_sec
    return 3600.0  # ライブ配信フォールバック


def scan_video_for_match_scenes(
    url: str,
    scan_interval_sec: float = 15.0,
    max_duration_sec: float | None = None,
) -> list[float]:
    """動画を一定間隔でスキャンして「試合中」の秒数リストを返す。

    Args:
        url: YouTube / Twitch の動画URL。
        scan_interval_sec: スキャン間隔（秒）。デフォルト 15 秒。
        max_duration_sec: スキャンを打ち切る秒数上限（None で最後まで）。

    Returns:
        試合中と判定された秒数のリスト。
    """
    cap = _open_cap(url)
    limit = _scan_limit(cap, max_duration_sec)

    logger.info("スキャン開始: 間隔=%.1f秒, 上限=%.0f秒", scan_interval_sec, limit)

    match_timestamps: list[float] = []
    t = 0.0
    while t <= limit:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ret, frame = cap.read()
        if not ret or frame is None:
            break

        if is_match_scene(frame):
            match_timestamps.append(t)
            logger.info("試合シーン検出: %.1f秒", t)
        else:
            logger.debug("非試合シーン: %.1f秒", t)

        t += scan_interval_sec

    cap.release()
    logger.info("スキャン完了: %d件の試合シーンを検出", len(match_timestamps))
    return match_timestamps


def scan_and_capture_frames(
    url: str,
    n_frames: int = 4,
    scan_interval_sec: float = 15.0,
    max_duration_sec: float | None = None,
) -> list[tuple[float, list[np.ndarray]]]:
    """スキャンと複数フレームキャプチャを1パスで行う。

    最適化ポイント:
      - YouTube の場合はシーン判定用に低解像度（480p）ストリームを使用する。
        セグメントサイズが小さくなりseekが高速化する。
      - 試合シーンが連続している場合は seek をスキップし cap.grab() で
        フレームを前進させる（デコードなし、seek なし）。
      - 非試合→試合の切り替え時のみ seek + 2フレームフラッシュを実施する。

    Args:
        url: YouTube / Twitch の動画URL。
        n_frames: 各試合シーンで取得するフレーム数（デフォルト: 4）。
                  フレーム状態推定には 2 以上必要。8 より少なくて十分。
        scan_interval_sec: スキャン間隔（秒）。デフォルト 15 秒。
        max_duration_sec: スキャン上限秒数（None で動画全体）。

    Returns:
        試合シーンごとの (秒数, フレームリスト) のリスト。
    """
    is_youtube = "youtube.com" in url.lower() or "youtu.be" in url.lower()

    if is_youtube:
        # シーン判定用: 低解像度（480p 以下）で高速スキャン
        scan_url = _resolve_youtube_url(url, low_res=True)
        logger.info("低解像度スキャンURL取得完了（480p）")
    else:
        scan_url = _resolve_twitch_url(url) if "twitch.tv" in url.lower() else url

    cap = cv2.VideoCapture(scan_url)
    limit = _scan_limit(cap, max_duration_sec)
    fps = max(1.0, cap.get(cv2.CAP_PROP_FPS) or 30.0)
    # スキャン間隔に対応するフレーム数（試合継続中の grab スキップ用）
    interval_frames = max(1, int(scan_interval_sec * fps))

    logger.info(
        "スキャン+キャプチャ開始: 間隔=%.1f秒(≈%dframes), 上限=%.0f秒, frames/scene=%d",
        scan_interval_sec, interval_frames, limit, n_frames,
    )

    results: list[tuple[float, list[np.ndarray]]] = []
    t = 0.0
    in_match = False  # 前回のポイントが試合シーンだったか

    while t <= limit:
        if in_match:
            # ── 試合継続: seek なし、grab() でフレームを前進 ──────────────
            # 前回 n_frames 分は読み済み → 残り (interval_frames - n_frames) をスキップ
            skip = max(0, interval_frames - n_frames)
            for _ in range(skip):
                if not cap.grab():
                    break
        else:
            # ── 非試合 or 初回: seek + 2フレームフラッシュ ────────────────
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            cap.grab()
            cap.grab()  # フラッシュ 2フレーム（ステールフレーム除去）

        ret, first = cap.read()
        if not ret or first is None:
            break

        if not is_match_scene(first):
            logger.debug("非試合シーン: %.1f秒", t)
            in_match = False
            t += scan_interval_sec
            continue

        # 試合シーン確定: 残り n_frames-1 フレームを連続取得
        frames = [first]
        for _ in range(n_frames - 1):
            ret, frame = cap.read()
            if ret and frame is not None:
                frames.append(frame)

        results.append((t, frames))
        logger.info("試合シーン検出+キャプチャ: %.1f秒 (%d枚)", t, len(frames))
        in_match = True
        t += scan_interval_sec

    cap.release()
    logger.info("スキャン+キャプチャ完了: %d件", len(results))
    return results
