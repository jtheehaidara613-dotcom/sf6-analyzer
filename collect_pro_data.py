"""プロプレイヤーのVODからベンチマークデータを自動収集するスクリプト。

使い方:
    python collect_pro_data.py <youtube_url> --player ときど --char JP
    python collect_pro_data.py <youtube_url> --player りゅうせい --char JP --interval 10

出力:
    - ターミナルに各指標の値を表示
    - --output を指定すると pro_benchmarks.py に追記できるコードを生成

指標の導出方法:
    deal_ratio_pct   : ラウンドごとの与ダメ / (与ダメ + 被ダメ) の平均
    burnout_rate_pct : スナップショット中 P1 ドライブ ≤ 300 の割合
    opp_burnout_pct  : スナップショット中 P2 ドライブ ≤ 300 の割合
    dr_economy       : P1 平均ドライブゲージ比率から分類（high/med/low）
    punish_conv_pct  : P2 が RECOVERY 状態の次フレームで P1 HP が減っていないか
                       ＆ P2 HP が減ったケース / RECOVERY 総数
    lethal_conv_pct  : P2 HP が 20% 未満になった後にラウンドが終わった割合
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass

from schemas import char_to_enum, CharacterName, FrameState, GameState

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_MAX_DRIVE = 10000
_BURNOUT_THRESH = 300       # ドライブ ≤ この値 → バーンアウト圏
_LETHAL_HP_RATIO = 0.20     # HP比率がこれ未満 → リーサル圏
_ROUND_RESET_RATIO = 1.15   # 前スナップ比でこれ以上HP増加 → ラウンドリセット


@dataclass
class _RoundSegment:
    """1ラウンド分のスナップショット列。"""
    snapshots: list[GameState]


def _split_into_rounds_sided(
    results: list[tuple[float, GameState]],
    side: str,
) -> list[_RoundSegment]:
    """いずれかのプレイヤーの HP 急回復をラウンド区切りとして分割する。"""
    if not results:
        return []

    segments: list[_RoundSegment] = []
    current: list[GameState] = [results[0][1]]

    for i in range(1, len(results)):
        gs   = results[i][1]
        prev = results[i - 1][1]
        # どちらかが大幅 HP 回復 → ラウンドリセット
        p1_reset = gs.player1.hp > prev.player1.hp * _ROUND_RESET_RATIO
        p2_reset = gs.player2.hp > prev.player2.hp * _ROUND_RESET_RATIO
        if p1_reset or p2_reset:
            segments.append(_RoundSegment(current))
            current = []
        current.append(gs)

    if current:
        segments.append(_RoundSegment(current))
    return segments



def _get_pro_state(gs: GameState, side: str):
    """プロサイド（p1/p2）の CharacterState を返す。"""
    return gs.player1 if side == "p1" else gs.player2


def _get_opp_state(gs: GameState, side: str):
    """相手サイドの CharacterState を返す。"""
    return gs.player2 if side == "p1" else gs.player1


def _burnout_pct(results: list[tuple[float, GameState]], side: str) -> float:
    """プロ側バーンアウト圏（ドライブ ≤ 300）にいたスナップショット割合。"""
    if not results:
        return 0.0
    count = sum(
        1 for _, gs in results
        if _get_pro_state(gs, side).drive_gauge <= _BURNOUT_THRESH
    )
    return count / len(results) * 100


def _opp_burnout_pct(results: list[tuple[float, GameState]], side: str) -> float:
    """相手バーンアウト圏にいたスナップショット割合。"""
    if not results:
        return 0.0
    count = sum(
        1 for _, gs in results
        if _get_opp_state(gs, side).drive_gauge <= _BURNOUT_THRESH
    )
    return count / len(results) * 100


def _dr_economy(results: list[tuple[float, GameState]], side: str) -> str:
    """プロ側の平均ドライブゲージ比率から economy を分類。"""
    if not results:
        return "med"
    avg = sum(_get_pro_state(gs, side).drive_gauge for _, gs in results) / len(results) / _MAX_DRIVE
    if avg >= 0.65:
        return "high"
    elif avg >= 0.40:
        return "med"
    return "low"


def _punish_conv_pct(results: list[tuple[float, GameState]], side: str) -> float | None:
    """相手 RECOVERY 直後にプロがダメージを与えた割合。

    スキャン間隔が粗いため精度は低め。サンプル不足の場合は None を返す。
    """
    recovery_chances = 0
    converted = 0
    for i in range(len(results) - 1):
        _, gs_cur  = results[i]
        _, gs_next = results[i + 1]
        opp_cur  = _get_opp_state(gs_cur, side)
        opp_next = _get_opp_state(gs_next, side)
        if opp_cur.frame_state == FrameState.RECOVERY:
            recovery_chances += 1
            if opp_next.hp < opp_cur.hp * 0.98:
                converted += 1

    if recovery_chances < 5:
        return None  # サンプル不足
    return converted / recovery_chances * 100


def _lethal_conv_pct(segments: list[_RoundSegment], side: str) -> float | None:
    """相手がリーサル圏（HP < 20%）に入った後、ラウンドを取れた割合。"""
    lethal_rounds = 0
    closed_rounds = 0

    for seg in segments:
        snaps = seg.snapshots
        if not snaps:
            continue
        opp_max = _get_opp_state(snaps[0], side).hp
        if opp_max == 0:
            continue

        lethal_seen = False
        for snap in snaps:
            if _get_opp_state(snap, side).hp / opp_max < _LETHAL_HP_RATIO:
                lethal_seen = True
        if lethal_seen:
            lethal_rounds += 1
            final_ratio = _get_opp_state(snaps[-1], side).hp / opp_max
            if final_ratio < 0.05:
                closed_rounds += 1

    if lethal_rounds < 3:
        return None
    return closed_rounds / lethal_rounds * 100


def _deal_ratio_sided(segments: list[_RoundSegment], side: str) -> float:
    """プロ側の与ダメ率。"""
    total_deal, total_recv = 0, 0
    for seg in segments:
        snaps = seg.snapshots
        if len(snaps) < 2:
            continue
        for i in range(len(snaps) - 1):
            pro_dmg = max(0, _get_pro_state(snaps[i], side).hp
                             - _get_pro_state(snaps[i + 1], side).hp)
            opp_dmg = max(0, _get_opp_state(snaps[i], side).hp
                             - _get_opp_state(snaps[i + 1], side).hp)
            total_recv += pro_dmg
            total_deal  += opp_dmg
    denom = total_deal + total_recv
    return (total_deal / denom * 100) if denom > 0 else 50.0


def analyze(
    url: str,
    char_p1: CharacterName,
    char_p2: CharacterName,
    scan_interval: float,
    max_duration: float | None,
    side: str = "p1",
) -> dict:
    """VODを解析してベンチマーク指標の辞書を返す。

    Args:
        side: プロプレイヤーが P1 か P2 か（"p1" or "p2"）。
    """
    from vision_extractor import scan_and_analyze

    logger.info("解析開始: %s (プロサイド: %s)", url[:80], side.upper())
    results = scan_and_analyze(
        url,
        character_p1=char_p1,
        character_p2=char_p2,
        scan_interval_sec=scan_interval,
        max_duration_sec=max_duration,
    )

    if not results:
        logger.error("試合シーンが1件も検出されませんでした")
        raise RuntimeError("試合シーンが1件も検出されませんでした")

    logger.info("スナップショット取得: %d件", len(results))

    segments = _split_into_rounds_sided(results, side)
    logger.info("ラウンド推定: %d ラウンド", len(segments))

    bo_rate  = _burnout_pct(results, side)
    opp_bo   = _opp_burnout_pct(results, side)
    deal     = _deal_ratio_sided(segments, side)
    economy  = _dr_economy(results, side)
    punish   = _punish_conv_pct(results, side)
    lethal   = _lethal_conv_pct(segments, side)

    return {
        "burnout_rate_pct": round(bo_rate, 1),
        "opp_burnout_pct":  round(opp_bo, 1),
        "deal_ratio_pct":   round(deal, 1),
        "dr_economy":       economy,
        "punish_conv_pct":  round(punish, 1) if punish is not None else None,
        "lethal_conv_pct":  round(lethal, 1) if lethal is not None else None,
        "snapshots":        len(results),
        "rounds":           len(segments),
        "side":             side,
    }


def _print_report(player_name: str, data: dict) -> None:
    print(f"\n{'='*60}")
    print(f"  プレイヤー: {player_name}")
    print(f"  スナップショット: {data['snapshots']}件 / ラウンド推定: {data['rounds']}件")
    print("="*60)
    print(f"  burnout_rate_pct : {data['burnout_rate_pct']:.1f}%  (低いほど良い, プロ水準 <15%)")
    print(f"  opp_burnout_pct  : {data['opp_burnout_pct']:.1f}%  (高いほど良い, プロ水準 >30%)")
    print(f"  deal_ratio_pct   : {data['deal_ratio_pct']:.1f}%  (プロ水準 60〜70%)")
    print(f"  dr_economy       : {data['dr_economy']}")
    if data["punish_conv_pct"] is not None:
        print(f"  punish_conv_pct  : {data['punish_conv_pct']:.1f}%  (プロ水準 >70%)")
    else:
        print(f"  punish_conv_pct  : ※ サンプル不足（RECOVERYシーンが5件未満）")
    if data["lethal_conv_pct"] is not None:
        print(f"  lethal_conv_pct  : {data['lethal_conv_pct']:.1f}%  (プロ水準 >60%)")
    else:
        print(f"  lethal_conv_pct  : ※ サンプル不足（リーサルシーンが3件未満）")
    print("="*60)


def _print_benchmark_code(player_key: str, player_name: str, data: dict) -> None:
    punish = data["punish_conv_pct"] if data["punish_conv_pct"] is not None else 75.0
    lethal = data["lethal_conv_pct"] if data["lethal_conv_pct"] is not None else 65.0
    print(f"""
# ── pro_benchmarks.py に追加するコード ──────────────────────
    "{player_key}": PlayerBenchmark(
        display_name="{player_name}",
        burnout_rate_pct={data['burnout_rate_pct']},
        opp_burnout_pct={data['opp_burnout_pct']},
        punish_conv_pct={punish},{'  # ※ 推定値' if data['punish_conv_pct'] is None else ''}
        lethal_conv_pct={lethal},{'  # ※ 推定値' if data['lethal_conv_pct'] is None else ''}
        deal_ratio_pct={data['deal_ratio_pct']},
        dr_economy="{data['dr_economy']}",
        style_label="",  # 手動で追記してください
        style_note="",   # 手動で追記してください
        verified=False,
    ),
# ────────────────────────────────────────────────────────────""")


def _save_to_json(
    player_key: str,
    player_name: str,
    char: str,
    data: dict,
    style_label: str,
    style_note: str,
) -> None:
    """解析結果を data/pro_benchmarks.json に保存する。"""
    from logic_engine.pro_benchmarks import PlayerBenchmark, save_benchmark

    punish = data["punish_conv_pct"] if data["punish_conv_pct"] is not None else 75.0
    lethal = data["lethal_conv_pct"] if data["lethal_conv_pct"] is not None else 65.0

    benchmark = PlayerBenchmark(
        display_name=player_name,
        character=char.lower(),
        burnout_rate_pct=data["burnout_rate_pct"],
        opp_burnout_pct=data["opp_burnout_pct"],
        punish_conv_pct=punish,
        lethal_conv_pct=lethal,
        deal_ratio_pct=data["deal_ratio_pct"],
        dr_economy=data["dr_economy"],
        style_label=style_label,
        style_note=style_note,
        verified=False,
    )
    save_benchmark(player_key, char, benchmark)
    key = f"{player_key}:{char.lower()}"
    logger.info("保存完了: data/pro_benchmarks.json [%s]", key)
    print(f"\n  → data/pro_benchmarks.json に保存しました: [{key}]")


# 後方互換: batch_collect.py が _CHAR_MAP をインポートしているため残す
# 新規コードは char_to_enum() を直接使うこと
def _CHAR_MAP_get(key: str) -> "CharacterName | None":
    return char_to_enum(key)

_CHAR_MAP = {c.value: c for c in CharacterName}  # batch_collect.py の import 用最小互換マップ


def _sanitize_name(name: str) -> str:
    """検索クエリ用に特殊文字・絵文字を除去する。"""
    import re
    # 絵文字・記号除去、英数・日本語・スペースのみ残す
    name = re.sub(r"[^\w\s\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def search_youtube_vod(player_name: str, char: str, max_results: int = 5) -> str | None:
    """yt-dlp の ytsearch でプレイヤーのSF6 VODを検索して最初のURLを返す。

    特殊文字・絵文字は自動除去してから検索する。

    Args:
        player_name: プレイヤー名
        char: キャラクター名（例: "JP", "RYU"）
        max_results: 検索結果の上限数

    Returns:
        見つかった動画のURL、見つからない場合は None
    """
    import yt_dlp

    sanitized = _sanitize_name(player_name)
    query = f"{sanitized} SF6 Street Fighter 6 {char}"
    search_url = f"ytsearch{max_results}:{query}"
    logger.info("YouTube検索: %s", query)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,  # メタデータのみ取得（ダウンロードしない）
        "skip_download": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_url, download=False)
            entries = info.get("entries", [])
            if not entries:
                logger.warning("検索結果なし: %s", query)
                return None

            # カスタムオーバーレイ系チャンネル（非標準HUD）を除外するキーワード
            _SKIP_TITLE_KEYWORDS = (
                "best replays",
                "high level gameplay",   # オーバーレイ系チャンネルに多い
                "▰",                     # "SF6 ▰ Player ▰" 系チャンネル
                "💥",                     # "player 💥 messatsu 💥" 系オーバーレイチャンネル
                "🔥sf6",
                "🔥 sf6",
                " guide",                # チュートリアル・ガイド動画
                "tutorial",
                "combo guide",
                "how to play",
                "character guide",
                "reacts to",             # リアクション動画
                "reaction",
                "gameplay trailer",
                "reveal trailer",
                "trailer",
            )

            for entry in entries:
                video_id = entry.get("id")
                title    = entry.get("title", "").lower()
                duration = entry.get("duration") or 0
                # 短すぎる動画（2分未満）はスキップ
                if duration and duration < 120:
                    continue
                # オーバーレイ系チャンネルはスキップ（非標準HUDのため検出失敗する）
                if any(kw in title for kw in _SKIP_TITLE_KEYWORDS):
                    logger.debug("オーバーレイ系チャンネルのためスキップ: %s", title)
                    continue
                url = f"https://www.youtube.com/watch?v={video_id}"
                logger.info("採用: [%s] %s", url, title)
                return url

            # 全て条件を満たさなかった場合はNoneを返す（呼び出し側でフォールバック）
            logger.warning("条件を満たす動画が見つかりませんでした（フォールバック候補を試します）")
            return None

    except Exception as e:
        logger.error("YouTube検索失敗: %s", e)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="プロVODからベンチマークデータを自動収集"
    )
    parser.add_argument("url",  nargs="?", default=None,
                        help="YouTube の動画URL（省略時は --auto-search で自動検索）")
    parser.add_argument("--player",   required=True, help="プレイヤー名（例: ときど）")
    parser.add_argument("--auto-search", action="store_true",
                        help="YouTube を自動検索してVODを取得する（url 引数を省略可能にする）")
    parser.add_argument("--key",      default=None,  help="辞書キー（省略時は --player と同じ）")
    parser.add_argument("--char",     default="JP",  help="P1 キャラクター（デフォルト: JP）")
    parser.add_argument("--opp",      default="RYU", help="P2 デフォルトキャラ（デフォルト: RYU）")
    parser.add_argument("--interval", type=float, default=15.0,
                        help="スキャン間隔（秒）。密にするほど精度↑処理時間↑（デフォルト: 15）")
    parser.add_argument("--max-sec",  type=float, default=None,
                        help="解析する最大秒数（省略時は動画全体）")
    parser.add_argument("--side", choices=["p1", "p2"], default="p1",
                        help="プロがどちら側か（デフォルト: p1）。"
                             "大会VODではプロが p2 になることが多い")
    parser.add_argument("--save", action="store_true",
                        help="解析結果を data/pro_benchmarks.json に自動保存する")
    parser.add_argument("--style-label", default="",
                        help="--save 時のスタイルラベル（例: 'ゾーニング安定型'）")
    parser.add_argument("--style-note",  default="",
                        help="--save 時のスタイル詳細メモ")
    args = parser.parse_args()

    # URL の解決（直接指定 or 自動検索）
    url = args.url
    if not url:
        if not args.auto_search:
            parser.error("url か --auto-search のどちらかを指定してください")
        url = search_youtube_vod(args.player, args.char)
        if not url:
            logger.error("YouTube で動画が見つかりませんでした: %s / %s", args.player, args.char)
            sys.exit(1)
        print(f"\n  → 自動検索で採用したURL: {url}\n")

    # プロ側のキャラを「--char」、相手を「--opp」で指定
    # side=p2 の場合は自動的に入れ替えて scan_and_analyze に渡す
    pro_char = char_to_enum(args.char)
    if pro_char is None:
        valid = ", ".join(c.value for c in CharacterName)
        logger.error("不明なキャラクター: %s  (使用可能: %s)", args.char, valid)
        sys.exit(1)
    opp_char = char_to_enum(args.opp) or CharacterName.RYU

    # scan_and_analyze には常に (p1_char, p2_char) を渡す
    if args.side == "p1":
        char_p1, char_p2 = pro_char, opp_char
    else:
        char_p1, char_p2 = opp_char, pro_char

    data = analyze(url, char_p1, char_p2, args.interval, args.max_sec, side=args.side)
    _print_report(args.player, data)

    if args.save:
        _save_to_json(args.key or args.player, args.player, args.char, data,
                      args.style_label, args.style_note)
    else:
        _print_benchmark_code(args.key or args.player, args.player, data)


if __name__ == "__main__":
    main()
