"""SF6 AI動画解析システム - 試合監視モジュール。

ライブ監視モードとVOD解析モードで使用するイベント検知・ログ管理を担当する。

イベント種別:
  - PUNISH_OPPORTUNITY  : 相手が硬直中で確定反撃チャンスがある
  - LETHAL_CHANCE       : リーサル圏内（現在の体力でとどめを刺せる）
  - TOOK_DAMAGE         : 自分がダメージを受けた
  - OPPONENT_TOOK_DAMAGE: 相手にダメージを与えた
  - LOW_HP              : 自分の体力が30%以下
  - BURNOUT             : 自分のドライブゲージが切れた（バーンアウト）
  - BURNOUT_OPPONENT    : 相手のドライブゲージが切れた（攻撃チャンス）
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from schemas import GameState, PunishOpportunity, LethalResult


# ---------------------------------------------------------------------------
# イベント定義
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    PUNISH_OPPORTUNITY    = "punish_opportunity"
    LETHAL_CHANCE         = "lethal_chance"
    TOOK_DAMAGE           = "took_damage"
    OPPONENT_TOOK_DAMAGE  = "opponent_took_damage"
    LOW_HP                = "low_hp"
    BURNOUT               = "burnout"
    BURNOUT_OPPONENT      = "burnout_opponent"


@dataclass
class MatchEvent:
    """試合中の1イベント。"""
    event_type: EventType
    timestamp: datetime.datetime
    description: str
    detail: str = ""

    @property
    def time_str(self) -> str:
        return self.timestamp.strftime("%H:%M:%S")

    @property
    def icon(self) -> str:
        return {
            EventType.PUNISH_OPPORTUNITY:   "⚡",
            EventType.LETHAL_CHANCE:        "💀",
            EventType.TOOK_DAMAGE:          "💥",
            EventType.OPPONENT_TOOK_DAMAGE: "✅",
            EventType.LOW_HP:               "⚠️",
            EventType.BURNOUT:              "🔥",
            EventType.BURNOUT_OPPONENT:     "🎯",
        }.get(self.event_type, "•")


# ---------------------------------------------------------------------------
# イベントログ
# ---------------------------------------------------------------------------

@dataclass
class MatchLog:
    """試合全体のイベントログ。"""
    events: list[MatchEvent] = field(default_factory=list)
    snapshots: list[GameState] = field(default_factory=list)
    start_time: datetime.datetime = field(default_factory=datetime.datetime.now)

    def append(self, event: MatchEvent) -> None:
        self.events.append(event)

    def recent(self, n: int = 10) -> list[MatchEvent]:
        return self.events[-n:]

    # --- サマリー集計 ---

    @property
    def punish_opportunities(self) -> int:
        return sum(1 for e in self.events if e.event_type == EventType.PUNISH_OPPORTUNITY)

    @property
    def lethal_chances(self) -> int:
        return sum(1 for e in self.events if e.event_type == EventType.LETHAL_CHANCE)

    @property
    def times_took_damage(self) -> int:
        return sum(1 for e in self.events if e.event_type == EventType.TOOK_DAMAGE)

    @property
    def times_dealt_damage(self) -> int:
        return sum(1 for e in self.events if e.event_type == EventType.OPPONENT_TOOK_DAMAGE)

    @property
    def burnout_count(self) -> int:
        return sum(1 for e in self.events if e.event_type == EventType.BURNOUT)

    @property
    def burnout_opponent_count(self) -> int:
        return sum(1 for e in self.events if e.event_type == EventType.BURNOUT_OPPONENT)

    @property
    def elapsed_str(self) -> str:
        delta = datetime.datetime.now() - self.start_time
        m, s = divmod(int(delta.total_seconds()), 60)
        return f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# イベント検知ロジック
# ---------------------------------------------------------------------------

def detect_events(
    current: GameState,
    punish: PunishOpportunity,
    lethal: LethalResult,
    prev_snapshot: Optional[GameState],
    p1_max_hp: int,
) -> list[MatchEvent]:
    """現在のゲーム状態から発生したイベントを検知して返す。

    Args:
        current: 現在のゲーム状態。
        punish: 確定反撃判定結果。
        lethal: リーサル判定結果。
        prev_snapshot: 前回スナップショット（初回は None）。
        p1_max_hp: P1 の最大体力。

    Returns:
        検知されたイベントのリスト。
    """
    events: list[MatchEvent] = []
    now = datetime.datetime.now()

    # 確定反撃チャンス
    if punish.is_punishable:
        events.append(MatchEvent(
            event_type=EventType.PUNISH_OPPORTUNITY,
            timestamp=now,
            description=f"確定反撃チャンス（{punish.frame_advantage}F有利）",
            detail=punish.punish_moves[0].move_name if punish.punish_moves else "",
        ))

    # リーサル圏内
    if lethal.is_lethal:
        events.append(MatchEvent(
            event_type=EventType.LETHAL_CHANCE,
            timestamp=now,
            description=f"リーサル圏内（推定 {lethal.estimated_max_damage:,} ダメージ）",
            detail=f"相手残HP {lethal.target_hp:,}",
        ))

    # 自分の体力30%以下
    hp_ratio = current.player1.hp / p1_max_hp
    if hp_ratio < 0.30:
        events.append(MatchEvent(
            event_type=EventType.LOW_HP,
            timestamp=now,
            description=f"自分の体力が残り {int(hp_ratio * 100)}%",
        ))

    # 前回との差分イベント
    if prev_snapshot is not None:
        p1_hp_diff = current.player1.hp - prev_snapshot.player1.hp
        p2_hp_diff = current.player2.hp - prev_snapshot.player2.hp

        if p1_hp_diff < -300:
            events.append(MatchEvent(
                event_type=EventType.TOOK_DAMAGE,
                timestamp=now,
                description=f"ダメージを受けた（{abs(p1_hp_diff):,}）",
            ))
        if p2_hp_diff < -300:
            events.append(MatchEvent(
                event_type=EventType.OPPONENT_TOOK_DAMAGE,
                timestamp=now,
                description=f"相手にダメージを与えた（{abs(p2_hp_diff):,}）",
            ))

        # バーンアウト検知（ドライブゲージが0に到達した瞬間）
        if prev_snapshot.player1.drive_gauge > 0 and current.player1.drive_gauge == 0:
            events.append(MatchEvent(
                event_type=EventType.BURNOUT,
                timestamp=now,
                description="バーンアウト！ドライブゲージが切れました",
                detail="相手の攻めに注意。防御択を慎重に選んでください",
            ))

        if prev_snapshot.player2.drive_gauge > 0 and current.player2.drive_gauge == 0:
            events.append(MatchEvent(
                event_type=EventType.BURNOUT_OPPONENT,
                timestamp=now,
                description="相手がバーンアウト！絶好の攻撃チャンス",
                detail="ドライブラッシュで一気に攻め込みましょう",
            ))

    return events


# ---------------------------------------------------------------------------
# サマリー・レポート生成
# ---------------------------------------------------------------------------

def build_vod_summary(log: MatchLog) -> dict:
    """イベントログから基本サマリー辞書を生成する。"""
    return {
        "監視時間":         log.elapsed_str,
        "確定反撃チャンス": log.punish_opportunities,
        "リーサル圏内":     log.lethal_chances,
        "被ダメージ回数":   log.times_took_damage,
        "与ダメージ回数":   log.times_dealt_damage,
        "バーンアウト回数": log.burnout_count,
        "総イベント数":     len(log.events),
    }


def build_stats_report(log: MatchLog) -> dict:
    """統計分析型レポートを生成する。

    Returns:
        ラベル → 値 の辞書。
    """
    total = (
        log.punish_opportunities + log.lethal_chances
        + log.times_took_damage + log.times_dealt_damage
    )
    deal_ratio = (
        round(log.times_dealt_damage / (log.times_took_damage + log.times_dealt_damage) * 100)
        if (log.times_took_damage + log.times_dealt_damage) > 0 else 0
    )

    return {
        "確定反撃チャンス数":  log.punish_opportunities,
        "リーサル圏内回数":    log.lethal_chances,
        "被ダメージ回数":      log.times_took_damage,
        "与ダメージ回数":      log.times_dealt_damage,
        "与ダメ率":            f"{deal_ratio}%",
        "自分バーンアウト":    log.burnout_count,
        "相手バーンアウト":    log.burnout_opponent_count,
        "検出イベント総数":    total,
        "監視時間":            log.elapsed_str,
    }


def _max_consecutive(events: list[MatchEvent], event_type: EventType) -> int:
    """指定イベントタイプの最大連続発生数を返す。"""
    max_streak = 0
    current_streak = 0
    for ev in events:
        if ev.event_type == event_type:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
    return max_streak


def build_coaching_report(log: MatchLog) -> list[dict]:
    """コーチング型レポートを生成する。

    イベントパターンを分析して改善アドバイスを返す。

    Returns:
        [{level: "good"|"warn"|"info", title: str, body: str}] のリスト。
    """
    advice: list[dict] = []

    # ── 1. 確定反撃チャンスの評価 ────────────────────────────────────────
    if log.punish_opportunities == 0:
        advice.append({
            "level": "info",
            "title": "確定反撃チャンスなし",
            "body": (
                "この監視期間中に相手が大きな隙を作りませんでした。"
                "引き続き相手の行動パターンを観察し、DP系（昇龍拳・天昇脚・DP）の後隙を狙う意識を持ちましょう。"
            ),
        })
    elif log.punish_opportunities >= 3:
        advice.append({
            "level": "warn",
            "title": f"確定反撃チャンスが {log.punish_opportunities} 回発生",
            "body": (
                f"相手が {log.punish_opportunities} 回も大きな隙をさらしています。"
                "SA技を使った最大パニッシュを取れているか確認してください。"
                "DP（-27F〜-31F）後には屈み弱P → 屈み中P → SA締めが入ります。"
            ),
        })
    else:
        advice.append({
            "level": "good",
            "title": f"確定反撃チャンスを {log.punish_opportunities} 回確認",
            "body": (
                "反撃機会を確認できています。"
                "最大ダメージコンボまで取れているか、実際の映像で確認しましょう。"
            ),
        })

    # ── 2. リーサルの評価 ────────────────────────────────────────────────
    if log.lethal_chances >= 1:
        advice.append({
            "level": "warn",
            "title": f"リーサル圏内に {log.lethal_chances} 回",
            "body": (
                f"相手をKOできる体力差が {log.lethal_chances} 回ありました。"
                "SAゲージを使った締めコンボで確実に仕留めましょう。"
                "リーサル時はSAゲージを出し惜しみしないことがプロの基本です。"
            ),
        })

    # ── 3. バーンアウト評価（SF6固有・プロ向け） ─────────────────────────
    if log.burnout_count >= 2:
        advice.append({
            "level": "warn",
            "title": f"バーンアウト {log.burnout_count} 回",
            "body": (
                f"自分が {log.burnout_count} 回バーンアウトしています。"
                "ドライブゲージ管理はSF6において最重要課題です。"
                "ドライブラッシュの多用・ドライブパリィの連打を見直してください。"
                "ゲージが50%を切ったら攻め方をコントロールすることが上達の近道です。"
            ),
        })
    elif log.burnout_count == 1:
        advice.append({
            "level": "warn",
            "title": "バーンアウト 1 回",
            "body": (
                "バーンアウトが発生しました。"
                "どの場面でゲージを使い切ったか振り返り、"
                "同じパターンを繰り返さないよう意識してください。"
            ),
        })

    if log.burnout_opponent_count >= 1:
        advice.append({
            "level": "good",
            "title": f"相手バーンアウトを {log.burnout_opponent_count} 回引き出した",
            "body": (
                f"相手を {log.burnout_opponent_count} 回バーンアウトさせました。"
                "バーンアウト中の相手にはドライブラッシュで強引にプレッシャーをかけ、"
                "コーナーキャリーを狙うのが上位プレイヤーの定石です。"
            ),
        })

    # ── 4. 被ダメージと与ダメージのバランス ──────────────────────────────
    took = log.times_took_damage
    dealt = log.times_dealt_damage

    if took == 0 and dealt == 0:
        advice.append({
            "level": "info",
            "title": "ダメージ交換なし",
            "body": "監視期間中にダメージ交換が検出されませんでした。監視時間を伸ばすかライブ監視を使ってください。",
        })
    elif took > dealt * 2:
        advice.append({
            "level": "warn",
            "title": "被ダメが与ダメの2倍以上",
            "body": (
                f"被ダメ {took} 回 vs 与ダメ {dealt} 回。"
                "守りの択を見直しましょう。相手の起き攻めには「待つ」を徹底し、"
                "ドライブパリィ（Lv1）でゲージを回復しながら凌ぐのが有効です。"
            ),
        })
    elif dealt >= took:
        advice.append({
            "level": "good",
            "title": "与ダメが被ダメ以上",
            "body": (
                f"与ダメ {dealt} 回 vs 被ダメ {took} 回。"
                "攻めが機能しています。リーサル圏内でのSAゲージ消費タイミングを磨けば更に勝率が上がります。"
            ),
        })
    else:
        advice.append({
            "level": "info",
            "title": f"被ダメ {took} 回 / 与ダメ {dealt} 回",
            "body": "拮抗した展開です。差をつけるにはリーサル圏内でSAゲージを切るタイミングが鍵です。",
        })

    # ── 5. 連続被ダメのストリーク分析 ────────────────────────────────────
    max_streak = _max_consecutive(log.events, EventType.TOOK_DAMAGE)
    if max_streak >= 3:
        advice.append({
            "level": "warn",
            "title": f"連続被ダメ最大 {max_streak} 回",
            "body": (
                f"一度に {max_streak} 回連続でダメージを受けた局面があります。"
                "崩された後の起き上がりで「暴れ」を抑え、"
                "相手の攻め継続に対してはバックジャンプや完全ガードで距離を取りましょう。"
            ),
        })

    # ── 6. 低HP警告回数 ───────────────────────────────────────────────────
    low_hp_count = sum(1 for e in log.events if e.event_type == EventType.LOW_HP)
    if low_hp_count >= 3:
        advice.append({
            "level": "warn",
            "title": f"体力30%以下の場面が {low_hp_count} 回",
            "body": (
                f"ピンチの場面が {low_hp_count} 回。"
                "体力有利なうちにラウンドを決める意識を持ちましょう。"
                "HP有利時はドライブゲージを温存し、SA締めでラウンドを取り切るのが上位の立ち回りです。"
            ),
        })

    return advice
