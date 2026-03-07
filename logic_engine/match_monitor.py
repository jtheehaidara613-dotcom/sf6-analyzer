"""SF6 AI動画解析システム - 試合監視モジュール。

ライブ監視モードとVOD解析モードで使用するイベント検知・ログ管理を担当する。

イベント種別:
  - PUNISH_OPPORTUNITY : 相手が硬直中で確定反撃チャンスがある
  - LETHAL_CHANCE      : リーサル圏内（現在の体力でとどめを刺せる）
  - TOOK_DAMAGE        : 自分がダメージを受けた
  - OPPONENT_TOOK_DAMAGE: 相手にダメージを与えた
  - LOW_HP             : 自分の体力が30%以下
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

    前回のスナップショットとの差分も考慮する。

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
        "総イベント数":     len(log.events),
    }


def build_stats_report(log: MatchLog) -> dict:
    """B）統計分析型レポートを生成する。

    Returns:
        ラベル → (値, デルタ説明) の辞書。
    """
    total = log.punish_opportunities + log.lethal_chances + log.times_took_damage + log.times_dealt_damage
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
        "検出イベント総数":    total,
        "監視時間":            log.elapsed_str,
    }


def build_coaching_report(log: MatchLog) -> list[dict]:
    """A）コーチング型レポートを生成する。

    イベントパターンを分析して改善アドバイスを返す。

    Returns:
        [{level: "good"|"warn"|"info", title: str, body: str}] のリスト。
    """
    advice: list[dict] = []

    # 確定反撃チャンスの評価
    if log.punish_opportunities == 0:
        advice.append({
            "level": "info",
            "title": "確定反撃チャンスなし",
            "body": "この監視期間中に相手が大きな隙を作りませんでした。引き続き相手の行動パターンを観察してください。",
        })
    elif log.punish_opportunities >= 3:
        advice.append({
            "level": "warn",
            "title": f"確定反撃チャンスが {log.punish_opportunities} 回ありました",
            "body": "相手が大きな隙を複数回さらしています。コンシュームSA1などの高ダメージ技を素早く差し込む練習をしましょう。",
        })
    else:
        advice.append({
            "level": "good",
            "title": f"確定反撃チャンスを {log.punish_opportunities} 回確認",
            "body": "反撃機会を確認できています。実際に取れているか確認してください。",
        })

    # リーサルの評価
    if log.lethal_chances >= 1:
        advice.append({
            "level": "warn",
            "title": f"リーサル圏内に {log.lethal_chances} 回入れました",
            "body": "相手をとどめを刺せる場面がありました。SAゲージの管理とコンボの締めを意識して確実に仕留めましょう。",
        })

    # 被ダメージと与ダメージのバランス評価
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
            "body": f"被ダメ {took} 回 vs 与ダメ {dealt} 回。守りの択を見直し、無理な攻めを減らしましょう。ドライブゲージのPerfect Parryを活用してください。",
        })
    elif dealt >= took:
        advice.append({
            "level": "good",
            "title": "与ダメが被ダメ以上",
            "body": f"与ダメ {dealt} 回 vs 被ダメ {took} 回。攻めが機能しています。このペースを維持しましょう。",
        })
    else:
        advice.append({
            "level": "info",
            "title": f"被ダメ {took} 回 / 与ダメ {dealt} 回",
            "body": "拮抗した展開です。リーサル圏内での締めコンボを磨くことで差が生まれます。",
        })

    # 低HP警告回数
    low_hp_count = sum(1 for e in log.events if e.event_type == EventType.LOW_HP)
    if low_hp_count >= 3:
        advice.append({
            "level": "warn",
            "title": f"体力30%以下の場面が {low_hp_count} 回",
            "body": "ピンチの場面が多くなっています。体力有利なうちにラウンドを決める意識を持ちましょう。",
        })

    return advice
