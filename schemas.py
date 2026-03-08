"""SF6 AI動画解析システム 共通スキーマ定義。

システム全体で共通利用するPydanticモデルを定義します。
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class FrameState(str, Enum):
    """キャラクターのフレーム状態。

    Attributes:
        NEUTRAL: 通常（行動可能）状態。
        HITSTUN: ヒットスタン（被ヒット硬直）状態。
        BLOCKSTUN: ガードスタン（ガード硬直）状態。
        RECOVERY: 技後の硬直（リカバリー）状態。
        STARTUP: 技の発生中状態。
    """

    NEUTRAL = "neutral"
    HITSTUN = "hitstun"
    BLOCKSTUN = "blockstun"
    RECOVERY = "recovery"
    STARTUP = "startup"


class CharacterName(str, Enum):
    """システムがサポートするキャラクター名。"""

    RYU = "ryu"
    CHUN_LI = "chun_li"
    JAMIE = "jamie"
    LUKE = "luke"
    KEN = "ken"
    CAMMY = "cammy"
    JP = "jp"
    GUILE = "guile"
    ZANGIEF = "zangief"
    BLANKA = "blanka"
    DHALSIM = "dhalsim"
    DEE_JAY = "dee_jay"
    KIMBERLY = "kimberly"
    JURI = "juri"
    MANON = "manon"
    MARISA = "marisa"
    LILY = "lily"
    RASHID = "rashid"
    ED = "ed"
    AKI = "aki"
    AKUMA = "akuma"
    M_BISON = "m_bison"
    TERRY = "terry"
    MAI = "mai"
    ELENA = "elena"


# ---------------------------------------------------------------------------
# 入力スキーマ
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    """動画解析リクエストのスキーマ。

    Attributes:
        video_url: 解析対象の動画URL。HTTP/HTTPSプロトコルのみ許可。
        character_p1: プレイヤー1のキャラクター識別子。
        character_p2: プレイヤー2のキャラクター識別子。
    """

    video_url: HttpUrl = Field(
        ...,
        description="解析対象の動画URL（HTTP/HTTPS）",
        examples=["https://example.com/match.mp4"],
    )
    character_p1: CharacterName = Field(
        ...,
        description="プレイヤー1のキャラクター",
        examples=["ryu"],
    )
    character_p2: CharacterName = Field(
        ...,
        description="プレイヤー2のキャラクター",
        examples=["chun_li"],
    )


class ScanRequest(BaseModel):
    """動画全体スキャンリクエストのスキーマ。

    Attributes:
        video_url: スキャン対象の動画URL。
        character_p1: プレイヤー1のキャラクター識別子。
        character_p2: プレイヤー2のキャラクター識別子。
        scan_interval_sec: スキャン間隔（秒）。デフォルト 15 秒。
        max_duration_sec: スキャン上限秒数。None で動画全体をスキャン。
        max_workers: 並列解析ワーカー数。デフォルト 4。
    """

    video_url: HttpUrl = Field(
        ...,
        description="スキャン対象の動画URL（HTTP/HTTPS）",
    )
    character_p1: CharacterName = Field(..., description="プレイヤー1のキャラクター")
    character_p2: CharacterName = Field(..., description="プレイヤー2のキャラクター")
    scan_interval_sec: float = Field(
        15.0, gt=0, le=300, description="スキャン間隔（秒）"
    )
    max_duration_sec: Optional[float] = Field(
        None, gt=0, description="スキャン上限秒数（None で動画全体）"
    )
    max_workers: int = Field(
        4, ge=1, le=16, description="並列解析ワーカー数（1=直列）"
    )


# ---------------------------------------------------------------------------
# 中間スキーマ（解析パイプライン内部）
# ---------------------------------------------------------------------------

class Position(BaseModel):
    """画面上のキャラクター座標。

    Attributes:
        x: 横座標（ピクセル）。
        y: 縦座標（ピクセル）。
    """

    x: float = Field(..., ge=0, description="横座標（ピクセル）")
    y: float = Field(..., ge=0, description="縦座標（ピクセル）")


class CharacterState(BaseModel):
    """単一フレームにおけるキャラクターの状態。

    Attributes:
        character: キャラクター識別子。
        position: 画面上の座標。
        hp: 現在体力（0〜max_hp）。
        drive_gauge: ドライブゲージ量（0〜10000）。
        sa_stock: 保有しているSAゲージストック数（0〜3）。
        frame_state: 現在のフレーム状態。
        last_move: 直前に使用した技の識別子。Noneは技未使用。
        remaining_recovery_frames: 残り硬直フレーム数（recovery状態時のみ有効）。
    """

    character: CharacterName
    position: Position
    hp: int = Field(..., ge=0, description="現在体力")
    drive_gauge: int = Field(..., ge=0, le=10000, description="ドライブゲージ（0〜10000）")
    sa_stock: int = Field(..., ge=0, le=3, description="SAゲージストック数（0〜3）")
    frame_state: FrameState
    last_move: Optional[str] = Field(None, description="直前の技識別子")
    remaining_recovery_frames: int = Field(
        0, ge=0, description="残り硬直フレーム数"
    )

    @property
    def is_burnout(self) -> bool:
        """ドライブゲージが0でバーンアウト状態かどうか。"""
        return self.drive_gauge == 0


class GameState(BaseModel):
    """1フレーム時点のゲーム全体の状態。

    Attributes:
        player1: プレイヤー1の状態。
        player2: プレイヤー2の状態。
        frame_number: 動画上のフレーム番号。
        round_number: 現在のラウンド番号（1〜5）。
    """

    player1: CharacterState
    player2: CharacterState
    frame_number: int = Field(..., ge=0, description="動画フレーム番号")
    round_number: int = Field(1, ge=1, le=5, description="ラウンド番号")


# ---------------------------------------------------------------------------
# 出力スキーマ（解析結果）
# ---------------------------------------------------------------------------

class MoveInfo(BaseModel):
    """反撃候補技の情報。

    Attributes:
        move_id: 技の識別子。
        move_name: 技の表示名。
        startup: 発生フレーム数。
        damage: 基本ダメージ量。
        advantage_on_hit: ヒット時フレーム有利量。
        sa_cost: 必要なSAゲージストック数。
        drive_cost: 必要なドライブゲージ量（DR経由の場合は2500）。
    """

    move_id: str
    move_name: str
    startup: int
    damage: int
    advantage_on_hit: int
    sa_cost: int
    drive_cost: int = 0


class PunishOpportunity(BaseModel):
    """確定反撃の解析結果。

    Attributes:
        is_punishable: 確定反撃が可能かどうか。
        frame_advantage: P1側のフレーム有利量（正値が有利）。
        punish_moves: 確定反撃として使用可能な技のリスト（有利フレーム順）。
        description: 判定の説明文。
    """

    is_punishable: bool
    frame_advantage: int
    punish_moves: list[MoveInfo] = Field(default_factory=list)
    description: str


class ComboStep(BaseModel):
    """コンボの1ステップ情報。

    Attributes:
        move_id: 技の識別子。
        move_name: 技の表示名。
        hit_count: このステップのヒット番号（1始まり）。
        scaled_damage: 補正後ダメージ量。
        scaling_rate: 適用されたダメージ補正率。
    """

    move_id: str
    move_name: str
    hit_count: int
    scaled_damage: int
    scaling_rate: float


class LethalResult(BaseModel):
    """リーサル（倒し切り）判定の解析結果。

    Attributes:
        is_lethal: 現在の状況でリーサルが可能かどうか。
        target_hp: 相手の現在体力。
        estimated_max_damage: 推定最大コンボダメージ（補正込み）。
        recommended_combo: 推奨コンボのステップリスト。
        drive_cost: 推奨コンボに必要なドライブゲージ量。
        sa_cost: 推奨コンボに必要なSAゲージストック数。
        description: 判定の説明文。
    """

    is_lethal: bool
    target_hp: int
    estimated_max_damage: int
    recommended_combo: list[ComboStep] = Field(default_factory=list)
    drive_cost: int = Field(0, ge=0)
    sa_cost: int = Field(0, ge=0, le=3)
    description: str


class AnalyzeResponse(BaseModel):
    """動画解析APIのレスポンス全体スキーマ。

    Attributes:
        video_url: 解析した動画のURL。
        frame_number: 解析対象フレーム番号。
        round_number: ラウンド番号。
        player1_state: P1の状態スナップショット。
        player2_state: P2の状態スナップショット。
        punish_opportunity: P1視点での確定反撃判定結果。
        lethal_result: P1視点でのリーサル判定結果。
    """

    video_url: str
    frame_number: int
    round_number: int
    player1_state: CharacterState
    player2_state: CharacterState
    punish_opportunity: PunishOpportunity
    lethal_result: LethalResult


# ---------------------------------------------------------------------------
# エラースキーマ
# ---------------------------------------------------------------------------

class ScanResponse(BaseModel):
    """動画全体スキャンAPIのレスポンス全体スキーマ。

    Attributes:
        video_url: スキャンした動画のURL。
        total_scenes: 検出された試合シーンの総数。
        scenes: 各試合シーンの解析結果リスト。
    """

    video_url: str
    total_scenes: int
    scenes: list[AnalyzeResponse]


class LiveStartRequest(BaseModel):
    """ライブ解析セッション開始リクエスト。

    Attributes:
        video_url: Twitch/YouTubeLive の配信URL。
        character_p1: プレイヤー1のキャラクター識別子。
        character_p2: プレイヤー2のキャラクター識別子。
        interval_sec: 解析間隔（秒）。デフォルト 2 秒。
    """

    video_url: HttpUrl = Field(..., description="ライブ配信URL（Twitch/YouTubeLive）")
    character_p1: CharacterName = Field(..., description="プレイヤー1のキャラクター")
    character_p2: CharacterName = Field(..., description="プレイヤー2のキャラクター")
    interval_sec: float = Field(2.0, ge=0.5, le=30.0, description="解析間隔（秒）")


class LiveStartResponse(BaseModel):
    """ライブ解析セッション開始レスポンス。

    Attributes:
        session_id: セッション識別子（UUID）。
        status: セッションの現在状態。
    """

    session_id: str
    status: str


class LiveStatusResponse(BaseModel):
    """ライブ解析セッションの状態レスポンス。

    Attributes:
        session_id: セッション識別子。
        status: セッションの現在状態。
        latest_result: 最新の解析結果（まだなければ None）。
        error_message: エラー発生時のメッセージ（任意）。
    """

    session_id: str
    status: str
    latest_result: Optional[AnalyzeResponse] = None
    error_message: Optional[str] = None


class HistoryItem(BaseModel):
    """履歴一覧の1件分のメタデータ。"""

    id: int
    created_at: str
    video_url: str
    character_p1: str
    character_p2: str
    round_number: int
    frame_number: int
    p1_hp: int
    p2_hp: int
    is_punishable: bool
    is_lethal: bool
    estimated_max_damage: int


class HistoryResponse(BaseModel):
    """解析履歴リストのレスポンス。"""

    total_returned: int
    offset: int
    items: list[HistoryItem]


class StatsResponse(BaseModel):
    """集計統計レスポンス。"""

    total: int
    punishable_rate: float
    lethal_rate: float
    avg_p1_hp: int
    avg_p2_hp: int
    avg_max_damage: int


class ErrorResponse(BaseModel):
    """APIエラーレスポンスのスキーマ。

    Attributes:
        error_code: エラーコード文字列。
        message: エラーの説明メッセージ。
        detail: 追加の詳細情報（任意）。
    """

    error_code: str
    message: str
    detail: Optional[str] = None
