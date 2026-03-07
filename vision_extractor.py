"""SF6 AI動画解析システム - vision_extractor モジュール。

本モジュールは動画フレームからキャラクター情報を抽出する責務を持ちます。
現フェーズではYOLO/OpenCVを用いた実装の代わりに、
事前定義されたダミーデータを返すモック関数として実装しています。

実際の実装時は、extract_game_state() 内の _mock_extract() を
実際のCV処理に置き換えてください。
"""

import logging
from enum import Enum

from schemas import CharacterName, CharacterState, FrameState, GameState, Position

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
    """URLからキャラクターを推定するモック関数。

    URLにキャラクター名が含まれていれば対応するキャラクターを返します。
    本番実装ではYOLOによる画面上のキャラクター検出に置き換えてください。

    Args:
        video_url: 解析対象の動画URL文字列。

    Returns:
        (player1キャラクター, player2キャラクター) のタプル。
        検出できなかった側はデフォルト（RYU / CHUN_LI）を返します。
    """
    url_lower = video_url.lower()
    all_chars = list(CharacterName)
    detected: list[CharacterName] = []

    for char in all_chars:
        name = char.value.replace("_", "")  # chun_li → chunli でも検出
        if char.value in url_lower or name in url_lower:
            detected.append(char)
            if len(detected) == 2:
                break

    p1 = detected[0] if len(detected) >= 1 else CharacterName.RYU
    p2 = detected[1] if len(detected) >= 2 else CharacterName.CHUN_LI

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
) -> GameState:
    """動画からゲーム状態を抽出する（モック実装）。

    本番実装では、動画フレームに対してYOLO等のオブジェクト検出モデルと
    OpenCVによる画像処理を組み合わせ、キャラクター座標・体力ゲージ・
    ドライブゲージ等をリアルタイムに抽出します。

    現フェーズでは video_url の内容に応じてあらかじめ定義されたシナリオの
    ダミーデータを返します。URLに "punishable"/"lethal"/"neutral"/"hitstun"
    が含まれる場合、対応するシナリオが選択されます。

    Args:
        video_url: 解析対象の動画URL。シナリオ選択に使用。
        character_p1: プレイヤー1のキャラクター識別子。
        character_p2: プレイヤー2のキャラクター識別子。
        frame_number: 解析するフレーム番号（デフォルト: 1800 = 約30秒時点）。
        round_number: ラウンド番号（デフォルト: 1）。

    Returns:
        抽出されたゲーム状態を表す GameState オブジェクト。

    Raises:
        ValueError: キャラクター識別子が不正な場合。
    """
    if is_stream_url(video_url):
        logger.info("配信URL検出: %s（本番実装では yt-dlp/streamlink でフレームキャプチャ）", video_url)
    logger.info(
        "extract_game_state 開始 | url=%s, p1=%s, p2=%s, frame=%d",
        video_url,
        character_p1.value,
        character_p2.value,
        frame_number,
    )

    scenario = _select_scenario_from_url(video_url)
    scenario_data = _MOCK_SCENARIOS[scenario]

    logger.info(
        "モックシナリオを適用します: %s | %s",
        scenario.value,
        scenario_data["description"],
    )

    player1_state = _build_character_state(character_p1, scenario_data["player1"])
    player2_state = _build_character_state(character_p2, scenario_data["player2"])

    game_state = GameState(
        player1=player1_state,
        player2=player2_state,
        frame_number=frame_number,
        round_number=round_number,
    )

    logger.info(
        "extract_game_state 完了 | P1 HP=%d, P2 HP=%d, P2 state=%s",
        player1_state.hp,
        player2_state.hp,
        player2_state.frame_state.value,
    )

    return game_state
