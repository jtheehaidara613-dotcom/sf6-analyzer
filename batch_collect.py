"""全キャラクター TOP プレイヤーを自動検索・一括解析するスクリプト。

使い方:
    # 全キャラ TOP1 を自動解析・保存
    python batch_collect.py

    # 特定キャラだけ
    python batch_collect.py --chars juri blanka dhalsim

    # 既存データも上書き再収集
    python batch_collect.py --overwrite

    # ドライランで検索URLだけ確認（解析しない）
    python batch_collect.py --dry-run

各キャラクターの TOP1 プレイヤーを character_top_players.json から読み込み、
YouTube で自動検索 → 動画解析 → pro_benchmarks.json に保存します。
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_DATA_DIR       = pathlib.Path(__file__).parent / "data"
_TOP_PLAYERS    = _DATA_DIR / "character_top_players.json"
_BENCHMARKS     = _DATA_DIR / "pro_benchmarks.json"

# キャラIDとCLI引数（--char）のマッピング
_CHAR_CLI_MAP: dict[str, str] = {
    "jp":       "JP",
    "ryu":      "RYU",
    "luke":     "LUKE",
    "ken":      "KEN",
    "cammy":    "CAMMY",
    "guile":    "GUILE",
    "chun_li":  "CHUN",
    "akuma":    "AKUMA",
    "terry":    "TERRY",
    "mai":      "MAI",
    "kimberly": "KIMBERLY",
    "juri":     "JURI",
    "blanka":   "BLANKA",
    "dhalsim":  "DHALSIM",
    "dee_jay":  "DEE_JAY",
    "manon":    "MANON",
    "marisa":   "MARISA",
    "lily":     "LILY",
    "rashid":   "RASHID",
    "ed":       "ED",
    "aki":      "AKI",
    "m_bison":  "M_BISON",
    "zangief":  "ZANGIEF",
    "jamie":    "JAMIE",
    "elena":    "ELENA",
    "sagat":    "SAGAT",
}


def _load_top_players() -> dict[str, list[dict]]:
    raw = json.loads(_TOP_PLAYERS.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if k not in ("_meta", "_todo")}


def _load_benchmarks() -> set[str]:
    """登録済みのキー一覧を返す（例: {"翔:jp", "ときど:jp"}）。"""
    if not _BENCHMARKS.exists():
        return set()
    return set(json.loads(_BENCHMARKS.read_text(encoding="utf-8")).keys())


def _top_player(players: list[dict]) -> dict | None:
    if not players:
        return None
    return players[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="全キャラ TOP プレイヤー一括解析")
    parser.add_argument("--chars", nargs="*", default=None,
                        help="対象キャラID（省略時は全キャラ）例: juri blanka")
    parser.add_argument("--top-n", type=int, default=1,
                        help="各キャラ上位N名まで解析（デフォルト: 1）")
    parser.add_argument("--overwrite", action="store_true",
                        help="既存データがあっても再収集する")
    parser.add_argument("--dry-run", action="store_true",
                        help="YouTube検索URLのみ表示して解析しない")
    parser.add_argument("--interval", type=float, default=15.0,
                        help="スキャン間隔秒（デフォルト: 15）")
    parser.add_argument("--max-sec", type=float, default=600.0,
                        help="動画解析の最大秒数（デフォルト: 600＝10分）")
    parser.add_argument("--side", choices=["p1", "p2"], default="p2",
                        help="プロがどちら側か（大会VODはp2が多い、デフォルト: p2）")
    args = parser.parse_args()

    from collect_pro_data import _CHAR_MAP, _save_to_json, analyze, search_youtube_vod

    top_players  = _load_top_players()
    benchmarks   = _load_benchmarks()

    target_chars = args.chars or list(top_players.keys())
    # _CHAR_CLI_MAP にないキャラは除外（SAGATなど未対応キャラ）
    target_chars = [c for c in target_chars if c in _CHAR_CLI_MAP]

    results_summary: list[dict] = []

    for char_id in target_chars:
        char_cli = _CHAR_CLI_MAP[char_id]
        players  = top_players.get(char_id, [])

        for rank, player in enumerate(players[:args.top_n], start=1):
            player_name = player["name"]
            bench_key   = f"{player_name}:{char_id}"

            # スキップ判定
            if bench_key in benchmarks and not args.overwrite:
                logger.info("[SKIP] %s (%s) - 登録済み", player_name, char_id)
                results_summary.append({"char": char_id, "player": player_name,
                                        "status": "skip"})
                continue

            logger.info("=" * 60)
            logger.info("[%d/%d] %s / %s (rank %d)",
                        target_chars.index(char_id) + 1, len(target_chars),
                        player_name, char_id.upper(), rank)

            # YouTube検索（失敗したら次候補に自動フォールバック）
            url = search_youtube_vod(player_name, char_cli, max_results=10)
            fallback_player = player_name
            if not url:
                remaining = players[rank:]  # rank は 1始まりなので index は rank
                for fb in remaining[:2]:
                    fb_name = fb["name"]
                    logger.info("フォールバック検索: %s", fb_name)
                    url = search_youtube_vod(fb_name, char_cli, max_results=10)
                    if url:
                        fallback_player = fb_name
                        logger.info("フォールバック採用: %s", fb_name)
                        break

            if not url:
                logger.warning("動画が見つかりませんでした: %s", player_name)
                results_summary.append({"char": char_id, "player": player_name,
                                        "status": "no_video"})
                continue

            print(f"  URL: {url}")

            if args.dry_run:
                results_summary.append({"char": char_id, "player": player_name,
                                        "status": "dry_run", "url": url})
                continue

            # 解析
            try:
                pro_char = _CHAR_MAP.get(char_cli.lower())
                if pro_char is None:
                    logger.warning("キャラマップ未対応: %s", char_cli)
                    results_summary.append({"char": char_id, "player": player_name,
                                            "status": "unsupported_char"})
                    continue

                from schemas import CharacterName
                opp_char = CharacterName.RYU  # 対戦相手はデフォルトRYU（解析精度に影響小）

                if args.side == "p1":
                    char_p1, char_p2 = pro_char, opp_char
                else:
                    char_p1, char_p2 = opp_char, pro_char

                data = analyze(url, char_p1, char_p2, args.interval, args.max_sec,
                               side=args.side)

                # deal_ratio 異常値チェック（正常範囲: 20〜88%）
                deal = data.get("deal_ratio_pct", 50.0)
                if deal < 20.0 or deal > 88.0:
                    logger.warning(
                        "deal_ratio=%.1f%% は異常値です（正常範囲: 20〜88%%）。保存をスキップします。"
                        " 別動画を試すか --side を変えてください。",
                        deal,
                    )
                    results_summary.append({"char": char_id, "player": player_name,
                                            "status": "bad_quality",
                                            "deal_ratio": deal, "url": url})
                    continue

                _save_to_json(
                    player_key=player_name,
                    player_name=player_name,
                    char=char_cli,
                    data=data,
                    style_label="",
                    style_note="",
                )
                benchmarks.add(bench_key)  # スキップ判定を更新
                results_summary.append({"char": char_id, "player": player_name,
                                        "status": "ok", "url": url})

            except Exception as e:
                logger.error("解析失敗: %s / %s — %s", player_name, char_id, e)
                results_summary.append({"char": char_id, "player": player_name,
                                        "status": "error", "error": str(e)})

            # レート制限回避のため少し待機
            if not args.dry_run:
                time.sleep(2)

    # サマリー表示
    print("\n" + "=" * 60)
    print("  バッチ完了サマリー")
    print("=" * 60)
    for r in results_summary:
        status = r["status"]
        icon = {"ok": "✓", "skip": "-", "no_video": "✗", "error": "!", "dry_run": "?",
                "unsupported_char": "?", "bad_quality": "⚠"}.get(status, "?")
        print(f"  [{icon}] {r['char']:12s}  {r['player']}")
        if status == "error":
            print(f"          → {r.get('error', '')}")
        if status == "bad_quality":
            print(f"          → deal_ratio={r.get('deal_ratio', '?'):.1f}%  {r.get('url', '')}")
        if status in ("dry_run", "ok") and "url" in r:
            print(f"          → {r['url']}")
    print("=" * 60)

    ok_count = sum(1 for r in results_summary if r["status"] == "ok")
    print(f"  完了: {ok_count} / {len(results_summary)} キャラ")


if __name__ == "__main__":
    main()
