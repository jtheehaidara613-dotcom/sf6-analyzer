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


def _conversion_rate(
    events: list[MatchEvent],
    trigger: EventType,
    result: EventType,
    window: int = 5,
) -> tuple[int, int]:
    """trigger イベントの後 window 件以内に result が来た回数を返す。

    Returns:
        (変換できた回数, trigger の総回数)
    """
    converted = 0
    total = 0
    for i, ev in enumerate(events):
        if ev.event_type == trigger:
            total += 1
            if any(e.event_type == result for e in events[i + 1: i + 1 + window]):
                converted += 1
    return converted, total


def build_strategic_report(log: MatchLog) -> list[dict]:
    """戦略レポートを生成する。

    個別イベントではなく試合全体のパターン・因果関係を分析し、
    「最も勝率に影響する1〜2の優先課題」を特定する。

    分析軸:
      - チャンス変換率（パニッシュ/リーサル → 実ダメージ）
      - バーンアウト → ピンチ連鎖率
      - 相手バーンアウトの活用度
      - 攻守バランスと被ダメの偏り
      - 最優先課題の断言

    Returns:
        [{level, title, body}] のリスト。最後の要素が総合診断。
    """
    advice: list[dict] = []
    events = log.events

    if len(events) < 3:
        return [{
            "level": "info",
            "title": "データ不足",
            "body": "戦略分析には最低でも3分以上の監視データが必要です。監視時間を伸ばしてください。",
        }]

    # ── 1. パニッシュ変換率 ──────────────────────────────────────────────
    punish_hit, punish_total = _conversion_rate(
        events, EventType.PUNISH_OPPORTUNITY, EventType.OPPONENT_TOOK_DAMAGE
    )
    if punish_total > 0:
        p_rate = punish_hit / punish_total * 100
        if p_rate < 40:
            advice.append({
                "level": "warn",
                "title": f"確定反撃の変換率 {p_rate:.0f}% （{punish_hit}/{punish_total}）",
                "body": (
                    "パニッシュチャンスを取れていない回が多いです。"
                    "反射的に出せる最大コンボ（主にcMP→SA）をトレーニングモードで体に染み込ませましょう。"
                    "判断ではなく「反射」で動けるようになるのがプロの水準です。"
                ),
            })
        elif p_rate < 70:
            advice.append({
                "level": "info",
                "title": f"確定反撃の変換率 {p_rate:.0f}% （{punish_hit}/{punish_total}）",
                "body": (
                    "半数以上は取れています。取れなかった場面を映像で振り返り、"
                    "状況別（画面端/中央、ゲージあり/なし）で最適コンボを整理しましょう。"
                ),
            })
        else:
            advice.append({
                "level": "good",
                "title": f"確定反撃の変換率 {p_rate:.0f}% （{punish_hit}/{punish_total}）",
                "body": "高い変換率です。次のステップはパニッシュ後の起き攻め継続まで含めたダメージ効率の最大化です。",
            })

    # ── 2. リーサル変換率 ────────────────────────────────────────────────
    lethal_hit, lethal_total = _conversion_rate(
        events, EventType.LETHAL_CHANCE, EventType.OPPONENT_TOOK_DAMAGE, window=4
    )
    if lethal_total > 0:
        l_rate = lethal_hit / lethal_total * 100
        if l_rate < 50:
            advice.append({
                "level": "warn",
                "title": f"リーサル圏内の仕留め率 {l_rate:.0f}% （{lethal_hit}/{lethal_total}）",
                "body": (
                    f"リーサルチャンスが {lethal_total} 回あったのに半数以上を取り逃しています。"
                    "「仕留めに行く」局面ではSAゲージを出し惜しみしないことが大前提です。"
                    "コンボ途中でゲージ残量を確認する習慣をつけ、SA締めを必ず組み込みましょう。"
                ),
            })
        else:
            advice.append({
                "level": "good",
                "title": f"リーサル圏内の仕留め率 {l_rate:.0f}% （{lethal_hit}/{lethal_total}）",
                "body": "リーサル圏内でしっかり仕留められています。この精度を維持してください。",
            })

    # ── 3. バーンアウト → ピンチ連鎖率 ──────────────────────────────────
    cascade_count = 0
    for i, ev in enumerate(events):
        if ev.event_type == EventType.BURNOUT:
            window = events[i + 1: i + 8]
            if any(e.event_type == EventType.LOW_HP for e in window):
                cascade_count += 1

    if log.burnout_count > 0:
        cascade_rate = cascade_count / log.burnout_count * 100
        if cascade_rate >= 50:
            advice.append({
                "level": "warn",
                "title": f"バーンアウト→ピンチ連鎖率 {cascade_rate:.0f}%",
                "body": (
                    "バーンアウト後に高確率で体力30%以下まで追い込まれています。"
                    "これは試合の構造的敗因です。バーンアウト中はドライブパリィ不可・DR不可で"
                    "防御択が激減するため、バーンアウト自体を避けることが最優先です。"
                    "ゲージ残量50%を『警告ライン』として常に意識してください。"
                ),
            })
        elif log.burnout_count >= 2:
            advice.append({
                "level": "info",
                "title": f"バーンアウト {log.burnout_count} 回（連鎖率 {cascade_rate:.0f}%）",
                "body": (
                    "バーンアウト後の立て直しは比較的できていますが、"
                    "バーンアウト頻度自体を下げることで試合をより安定させられます。"
                ),
            })

    # ── 4. 相手バーンアウトの活用度 ─────────────────────────────────────
    opp_burnout_hit, opp_burnout_total = _conversion_rate(
        events, EventType.BURNOUT_OPPONENT, EventType.OPPONENT_TOOK_DAMAGE, window=6
    )
    if opp_burnout_total > 0:
        ob_rate = opp_burnout_hit / opp_burnout_total * 100
        if ob_rate < 50:
            advice.append({
                "level": "warn",
                "title": f"相手バーンアウト後の攻め変換率 {ob_rate:.0f}%",
                "body": (
                    "相手のバーンアウトという絶好のチャンスを活かしきれていません。"
                    "バーンアウト確認後は即DRで距離を詰め、崩し択（投げ/打撃）を重ねるのが定石です。"
                    "相手はガード固めしか選択肢がなくなるため、表裏の2択が通りやすくなります。"
                ),
            })
        else:
            advice.append({
                "level": "good",
                "title": f"相手バーンアウトを {ob_rate:.0f}% の確率で攻め込めている",
                "body": "バーンアウトへの圧力がうまく機能しています。コーナーキャリーまで繋げられるとさらに有効です。",
            })

    # ── 5. 攻守バランスの偏り ────────────────────────────────────────────
    took = log.times_took_damage
    dealt = log.times_dealt_damage
    total_dmg = took + dealt
    if total_dmg >= 4:
        deal_ratio = dealt / total_dmg * 100
        if deal_ratio < 35:
            advice.append({
                "level": "warn",
                "title": f"ダメージ交換効率 {deal_ratio:.0f}%（受けすぎ傾向）",
                "body": (
                    "与ダメよりも被ダメが大きく上回っています。"
                    "攻め込む場面の選択（どこで攻めるか）を見直す必要があります。"
                    "相手の確定反撃がない技を軸に、リターンとリスクのバランスを再計算しましょう。"
                ),
            })
        elif deal_ratio > 65:
            advice.append({
                "level": "good",
                "title": f"ダメージ交換効率 {deal_ratio:.0f}%（優勢）",
                "body": (
                    "攻めが機能しており有利なダメージ交換ができています。"
                    "この効率を維持しながらリーサル圏内での締めを徹底すれば勝率がさらに上がります。"
                ),
            })

    # ── 6. 総合診断（最優先課題の断言） ─────────────────────────────────
    # スコアリングして最も深刻な問題を特定
    issues: list[tuple[int, str, str]] = []  # (priority, title, body)

    if punish_total > 0 and punish_hit / punish_total < 0.4:
        issues.append((3, "確定反撃の取りこぼし", "最大ダメージを取れる場面で取れていない。コンボ精度の向上が最優先。"))

    cascade_severe = log.burnout_count >= 2 and cascade_count / max(log.burnout_count, 1) >= 0.5
    if cascade_severe:
        issues.append((3, "バーンアウト管理", "ゲージ切れ→ピンチの連鎖が試合を壊している。ゲージ50%管理が急務。"))

    if lethal_total >= 2 and lethal_hit / lethal_total < 0.5:
        issues.append((2, "リーサルの取りこぼし", "仕留め切れない場面が多い。SAゲージの使いどころを固定化する。"))

    if opp_burnout_total >= 1 and opp_burnout_hit / opp_burnout_total < 0.5:
        issues.append((1, "相手バーンアウトの活用不足", "絶好のチャンスを逃している。DR+崩し2択を練習する。"))

    if total_dmg >= 4 and dealt / max(total_dmg, 1) < 0.35:
        issues.append((2, "攻め択の精度不足", "不利な場面での攻めが多い。技選択を見直す。"))

    if issues:
        issues.sort(key=lambda x: -x[0])
        top3 = issues[:3]
        ranks = ["① 最優先", "② 次点", "③ 改善余地"]
        lines = []
        for rank, (_, title, body) in zip(ranks, top3):
            lines.append(f"**{rank}: {title}**\n{body}")
        advice.append({
            "level": "warn",
            "title": f"総合診断（課題 TOP{len(top3)}）",
            "body": "\n\n".join(lines),
        })
    else:
        advice.append({
            "level": "good",
            "title": "総合診断",
            "body": "全指標で大きな問題が見当たりません。より高いダメージ効率とゲージ運用の最適化がさらなる上達の鍵です。",
        })

    return advice


def build_coaching_report(log: MatchLog) -> list[dict]:
    """基本コーチング型レポートを生成する（初心者〜中級者向け）。

    専門用語を避けたシンプルなアドバイスを返す。

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
            "body": "相手が大きな隙を複数回さらしています。高ダメージ技を素早く差し込む練習をしましょう。",
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
            "body": f"被ダメ {took} 回 vs 与ダメ {dealt} 回。守りの択を見直し、無理な攻めを減らしましょう。",
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


def build_pro_coaching_report(log: MatchLog) -> list[dict]:
    """プロ向けコーチング型レポートを生成する。

    バーンアウト・ドライブゲージ管理・連続被ダメストリーク等、
    SF6 固有のシステムを踏まえた詳細アドバイスを返す。

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

    # ── 3. バーンアウト評価（SF6固有） ───────────────────────────────────
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
