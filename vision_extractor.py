"""SF6 AI動画解析システム - vision_extractor モジュール。

本モジュールは動画フレームからキャラクター情報を抽出する責務を持ちます。

実CV実装（cv_extractor.py）を試み、失敗した場合はモックにフォールバックします。

CV実装で検出できる項目:
  - 体力（HP）バー
  - ドライブゲージ
  - SAゲージストック数

CV実装では検出できない項目（常に NEUTRAL / 0F）:
  - フレーム状態（RECOVERY/HITSTUN）
  - キャラクター識別（手動選択を使用）
"""

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from enum import Enum

from schemas import CharacterName, CharacterState, FrameState, GameState, Position, ROUND_RESET_RATIO

logger = logging.getLogger(__name__)

# ストリーミングプラットフォームのホスト名
_STREAM_HOSTS = ("twitch.tv", "youtube.com", "youtu.be")


def is_stream_url(url: str) -> bool:
    """URLが配信プラットフォームのものかどうかを判定する。

    Args:
        url: 判定対象のURL文字列。

    Returns:
        配信URLであれば True。
    """
    return any(host in url.lower() for host in _STREAM_HOSTS)


# ---------------------------------------------------------------------------
# モックシナリオ定義
# ---------------------------------------------------------------------------

class MockScenario(str, Enum):
    """モックが返すゲーム状況シナリオ。

    Attributes:
        PUNISHABLE: P2が大きなマイナスフレームの技を使用した直後（反撃チャンス）。
        LETHAL: P2の体力がわずかでリーサル圏内。
        NEUTRAL: 両者が通常状態。
        HITSTUN: P2がヒットスタン中。
    """

    PUNISHABLE = "punishable"
    LETHAL = "lethal"
    NEUTRAL = "neutral"
    HITSTUN = "hitstun"


# ---------------------------------------------------------------------------
# モックデータ定義
# ---------------------------------------------------------------------------

_MOCK_SCENARIOS: dict[MockScenario, dict] = {
    MockScenario.PUNISHABLE: {
        "description": "P2がDP（-27F以上）を外した直後。P1に大きな確定反撃チャンス。",
        "player1": {
            "position": {"x": 400.0, "y": 600.0},
            "hp": 8500,
            "drive_gauge": 10000,
            "sa_stock": 1,
            "frame_state": FrameState.NEUTRAL,
            "last_move": None,
            "remaining_recovery_frames": 0,
        },
        "player2": {
            "position": {"x": 700.0, "y": 600.0},
            "hp": 7200,
            "drive_gauge": 6000,
            "sa_stock": 0,
            "frame_state": FrameState.RECOVERY,
            "last_move": "shoryuken",
            "remaining_recovery_frames": 27,
        },
    },
    MockScenario.LETHAL: {
        "description": "P2の体力が残りわずか（1500）。P1はSAゲージ1本保有。",
        "player1": {
            "position": {"x": 350.0, "y": 600.0},
            "hp": 9000,
            "drive_gauge": 8000,
            "sa_stock": 1,
            "frame_state": FrameState.NEUTRAL,
            "last_move": None,
            "remaining_recovery_frames": 0,
        },
        "player2": {
            "position": {"x": 650.0, "y": 600.0},
            "hp": 1500,
            "drive_gauge": 3000,
            "sa_stock": 0,
            "frame_state": FrameState.NEUTRAL,
            "last_move": None,
            "remaining_recovery_frames": 0,
        },
    },
    MockScenario.NEUTRAL: {
        "description": "両者通常状態。反撃チャンスもリーサル圏内でもない。",
        "player1": {
            "position": {"x": 400.0, "y": 600.0},
            "hp": 8000,
            "drive_gauge": 7000,
            "sa_stock": 0,
            "frame_state": FrameState.NEUTRAL,
            "last_move": None,
            "remaining_recovery_frames": 0,
        },
        "player2": {
            "position": {"x": 700.0, "y": 600.0},
            "hp": 7500,
            "drive_gauge": 5000,
            "sa_stock": 0,
            "frame_state": FrameState.NEUTRAL,
            "last_move": None,
            "remaining_recovery_frames": 0,
        },
    },
    MockScenario.HITSTUN: {
        "description": "P2がヒットスタン中。P1が追撃コンボを狙える状況。",
        "player1": {
            "position": {"x": 450.0, "y": 600.0},
            "hp": 7500,
            "drive_gauge": 9000,
            "sa_stock": 2,
            "frame_state": FrameState.NEUTRAL,
            "last_move": "standing_mp",
            "remaining_recovery_frames": 0,
        },
        "player2": {
            "position": {"x": 680.0, "y": 600.0},
            "hp": 4000,
            "drive_gauge": 4000,
            "sa_stock": 1,
            "frame_state": FrameState.HITSTUN,
            "last_move": None,
            "remaining_recovery_frames": 12,
        },
    },
}


# ---------------------------------------------------------------------------
# モック実装
# ---------------------------------------------------------------------------

def detect_characters_from_url(video_url: str) -> tuple[CharacterName, CharacterName]:
    """動画のHUD略称テキストからキャラクターをCV検出する。

    cv_extractor.detect_characters_from_url で試合シーンを探してキャラクターを識別する。
    CV検出に失敗した場合はデフォルト（RYU / CHUN_LI）にフォールバック。

    Args:
        video_url: 解析対象の動画URL文字列。

    Returns:
        (player1キャラクター, player2キャラクター) のタプル。
        検出できなかった側はデフォルト（RYU / CHUN_LI）を返します。
    """
    try:
        from cv_extractor import detect_characters_from_url as cv_detect
        p1, p2 = cv_detect(video_url)
        p1 = p1 or CharacterName.RYU
        p2 = p2 or CharacterName.CHUN_LI
    except Exception as e:
        logger.warning("CV キャラクター検出に失敗（フォールバック）: %s", e)
        p1, p2 = CharacterName.RYU, CharacterName.CHUN_LI

    logger.info("キャラクター自動検出: P1=%s, P2=%s", p1.value, p2.value)
    return p1, p2


def _select_scenario_from_url(video_url: str) -> MockScenario:
    """URLからモックシナリオを決定するヘルパー関数。

    URLにシナリオ名が含まれていれば対応するシナリオを、
    含まれていなければ PUNISHABLE をデフォルトとして返します。

    Args:
        video_url: 解析対象の動画URL文字列。

    Returns:
        選択されたモックシナリオ。
    """
    url_lower = video_url.lower()
    for scenario in MockScenario:
        if scenario.value in url_lower:
            logger.debug("URLからシナリオを選択しました: %s", scenario.value)
            return scenario
    logger.debug("デフォルトシナリオを使用します: %s", MockScenario.PUNISHABLE.value)
    return MockScenario.PUNISHABLE


def _build_character_state(
    character: CharacterName,
    data: dict,
) -> CharacterState:
    """辞書データからCharacterStateオブジェクトを生成するヘルパー関数。

    Args:
        character: キャラクター識別子。
        data: キャラクター状態の辞書データ。

    Returns:
        生成されたCharacterStateオブジェクト。
    """
    return CharacterState(
        character=character,
        position=Position(**data["position"]),
        hp=data["hp"],
        drive_gauge=data["drive_gauge"],
        sa_stock=data["sa_stock"],
        frame_state=data["frame_state"],
        last_move=data["last_move"],
        remaining_recovery_frames=data["remaining_recovery_frames"],
    )


def extract_game_state(
    video_url: str,
    character_p1: CharacterName,
    character_p2: CharacterName,
    frame_number: int = 1800,
    round_number: int = 1,
    start_sec: float | None = None,
) -> GameState:
    """動画からゲーム状態を抽出する。

    実CV（cv_extractor）でフレームキャプチャ＋HUD解析を試みる。
    失敗した場合はモックシナリオにフォールバックする。

    Args:
        video_url: 解析対象の動画URL。
        character_p1: プレイヤー1のキャラクター識別子。
        character_p2: プレイヤー2のキャラクター識別子。
        frame_number: 解析するフレーム番号（デフォルト: 1800）。
        round_number: ラウンド番号（デフォルト: 1）。
        start_sec: 解析開始秒数。None の場合はライブ最新フレーム。

    Returns:
        抽出されたゲーム状態を表す GameState オブジェクト。
    """
    logger.info(
        "extract_game_state 開始 | url=%s, p1=%s, p2=%s, start_sec=%s",
        video_url, character_p1.value, character_p2.value, start_sec,
    )

    # --- 実CV抽出を試みる ---
    try:
        from cv_extractor import capture_frames_from_url, extract_game_state_from_frames

        logger.info("CV抽出モード: フレームキャプチャ開始")
        frames = capture_frames_from_url(video_url, start_sec=start_sec)
        game_state = extract_game_state_from_frames(
            frames, character_p1, character_p2,
            frame_number=frame_number, round_number=round_number,
        )
        logger.info(
            "CV抽出完了 | P1 HP=%d %s | P2 HP=%d %s(残%dF)",
            game_state.player1.hp, game_state.player1.frame_state.value,
            game_state.player2.hp, game_state.player2.frame_state.value,
            game_state.player2.remaining_recovery_frames,
        )
        return game_state

    except ImportError as e:
        logger.warning("CV依存ライブラリが未インストール（モックにフォールバック）: %s", e)
    except OSError as e:
        logger.warning("動画アクセス失敗（URL無効 or ネットワークエラー）: %s", e)
    except Exception as e:
        logger.warning("CV抽出に失敗しました（モックにフォールバック）: %s", e, exc_info=True)

    # --- モックフォールバック ---
    scenario = _select_scenario_from_url(video_url)
    scenario_data = _MOCK_SCENARIOS[scenario]
    logger.info("モックシナリオを適用: %s | %s", scenario.value, scenario_data["description"])

    player1_state = _build_character_state(character_p1, scenario_data["player1"])
    player2_state = _build_character_state(character_p2, scenario_data["player2"])

    return GameState(
        player1=player1_state,
        player2=player2_state,
        frame_number=frame_number,
        round_number=round_number,
    )


def _smooth_hp_ewma(
    results: list[tuple[float, GameState]],
    scan_interval_sec: float = 15.0,
) -> list[tuple[float, GameState]]:
    """スキャン結果のHP値にEWMAを適用してノイズを除去する。

    HP は試合中に単調減少するため、EWMAで瞬間的な読み取りエラーを除去する。
    ただしラウンドリセット（HP が前フレームより 15% 以上増加）は平滑化しない。

    alpha はスキャン間隔から自動計算する（間隔が短いほど滑らかに）。
    基準: 15秒間隔 → alpha=0.45

    Args:
        results: (秒数, GameState) のリスト（時系列昇順）。
        scan_interval_sec: スキャン間隔（秒）。alpha の自動計算に使用。

    Returns:
        HP 値が平滑化された (秒数, GameState) のリスト。
    """
    # スキャン間隔に応じて alpha を調整（間隔が短いほど小さく = より滑らか）
    alpha = min(0.45, 0.45 * (scan_interval_sec / 15.0))
    if len(results) < 2:
        return results

    smoothed: list[tuple[float, GameState]] = []
    p1_smooth = float(results[0][1].player1.hp)
    p2_smooth = float(results[0][1].player2.hp)

    for t, gs in results:
        p1_raw = float(gs.player1.hp)
        p2_raw = float(gs.player2.hp)

        # ラウンドリセット検出: HP が 15% 以上増加 → スムーズ値をリセット
        if p1_raw > p1_smooth * ROUND_RESET_RATIO:
            p1_smooth = p1_raw
        else:
            p1_smooth = alpha * p1_raw + (1.0 - alpha) * p1_smooth

        if p2_raw > p2_smooth * ROUND_RESET_RATIO:
            p2_smooth = p2_raw
        else:
            p2_smooth = alpha * p2_raw + (1.0 - alpha) * p2_smooth

        new_gs = gs.model_copy(update={
            "player1": gs.player1.model_copy(update={"hp": int(p1_smooth)}),
            "player2": gs.player2.model_copy(update={"hp": int(p2_smooth)}),
        })
        smoothed.append((t, new_gs))

    return smoothed


def _analyze_frame_task(
    t: float,
    frames: list,
    character_p1: "CharacterName",
    character_p2: "CharacterName",
) -> "tuple[float, GameState]":
    """ProcessPoolExecutor から呼び出されるモジュールレベルの解析タスク。

    クロージャではなくモジュールレベル関数にすることで pickle 可能にする。
    """
    from cv_extractor import extract_game_state_from_frames

    game_state = extract_game_state_from_frames(
        frames, character_p1, character_p2,
        frame_number=int(t * 60),
    )
    logger.info("解析完了: %.1f秒", t)
    return t, game_state


def scan_and_analyze(
    video_url: str,
    character_p1: CharacterName,
    character_p2: CharacterName,
    scan_interval_sec: float = 15.0,
    max_duration_sec: float | None = None,
    max_workers: int = 4,
) -> list[tuple[float, GameState]]:
    """動画全体をスキャンして試合シーンのゲーム状態を一括抽出する。

    cv_extractor.scan_and_capture_frames で1接続のままシーン検出とフレーム取得を行い、
    取得済みフレームの解析を ProcessPoolExecutor で並列実行する（GIL回避）。
    URLキャッシュ済みのため並列化しても URL 解決コストは 0。

    Args:
        video_url: スキャン対象の動画URL。
        character_p1: プレイヤー1のキャラクター識別子。
        character_p2: プレイヤー2のキャラクター識別子。
        scan_interval_sec: スキャン間隔（秒）。
        max_duration_sec: スキャン上限秒数。None で動画全体。
        max_workers: 並列ワーカー数（デフォルト: 4）。

    Returns:
        (秒数, GameState) のリスト（秒数昇順）。試合シーンが1件もなければ空リスト。
    """
    try:
        from cv_extractor import scan_and_capture_frames
    except ImportError as e:
        logger.error("cv_extractor のインポートに失敗: %s", e)
        return []

    # スキャンとフレームキャプチャを1パスで実行（VideoCapture 1接続）
    scene_frames = scan_and_capture_frames(
        video_url,
        scan_interval_sec=scan_interval_sec,
        max_duration_sec=max_duration_sec,
    )
    logger.info("試合シーン検出: %d 件 → 並列解析開始 (max_workers=%d)", len(scene_frames), max_workers)

    if not scene_frames:
        return []

    results: list[tuple[float, GameState]] = []
    workers = min(max_workers, len(scene_frames))
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_analyze_frame_task, t, frames, character_p1, character_p2): t
            for t, frames in scene_frames
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                t = futures[future]
                logger.warning("%.1f秒の解析に失敗（スキップ）: %s", t, e)

    results.sort(key=lambda x: x[0])
    results = _smooth_hp_ewma(results, scan_interval_sec=scan_interval_sec)
    return results
