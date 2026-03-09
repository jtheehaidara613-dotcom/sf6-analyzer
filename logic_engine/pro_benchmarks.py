"""SF6 JPプロ・高ランクプレイヤーのベンチマークデータ。

収録プレイヤー:
  - 翔      : CAPCOM CUP 11 世界チャンピオン（IBUSHIGIN/ZETA DIVISION）
               世界最強JPとして知られる。ジャストパリィ精度が群を抜く。
               2025年10月、神経機能不調により引退。
  - ときど   : プロ選手（REJECT所属）。EVO殿堂入り。堅実・計算型。
               SFL2025参加。SF6でも第一線。
  - りゅうせい: 国内高ランク JP プレイヤー（ストリーマー）推定値
  - Juicyjoe : スウェーデン人プロ（NIP所属）。CAPCOM CUP Top 8。
               ゾーニング重視の安定型。
  - takepi   : 国内 JP プレイヤー推定値
  - ふぇんりっち: CAG OSAKA所属プロ。SFL参加。
               「押し付けて通して勝つ」積極攻め型。

各指標の意味:
  burnout_rate_pct  : 試合あたりの自分バーンアウト発生率(%) — 低いほど良い
  opp_burnout_pct   : 相手バーンアウトを引き出す率(%) — 高いほど良い
  punish_conv_pct   : 確定反撃チャンスの変換率(%) — 高いほど良い
  lethal_conv_pct   : リーサル圏内での仕留め率(%) — 高いほど良い
  deal_ratio_pct    : 与ダメ率（与ダメ/(与ダメ+被ダメ)×100）
  dr_economy        : "high"=節約型 / "med"=バランス型 / "low"=積極使用型

NOTE: verified=True のデータは公開情報・大会実績から根拠あり。
      verified=False は配信・コミュニティ知識・プレイスタイルから推定。
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field

_JSON_PATH = pathlib.Path(__file__).parent.parent / "data" / "pro_benchmarks.json"


@dataclass(frozen=True)
class PlayerBenchmark:
    display_name: str
    burnout_rate_pct: float     # 低いほど良い（プロ水準: <15%）
    opp_burnout_pct: float      # 高いほど良い（プロ水準: >30%）
    punish_conv_pct: float      # 高いほど良い（プロ水準: >70%）
    lethal_conv_pct: float      # 高いほど良い（プロ水準: >60%）
    deal_ratio_pct: float       # プロ水準: 60〜70%
    dr_economy: str             # "high" | "med" | "low"
    style_label: str
    style_note: str
    character: str = ""         # キャラクター識別子（例: "jp", "ryu"）。空文字=汎用
    verified: bool = False


def _load_from_json() -> dict[str, PlayerBenchmark]:
    """data/pro_benchmarks.json からベンチマークデータを読み込む。"""
    if not _JSON_PATH.exists():
        return {}
    try:
        raw = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
        return {
            k: PlayerBenchmark(**{
                f: v for f, v in entry.items()
                if f in PlayerBenchmark.__dataclass_fields__
            })
            for k, entry in raw.items()
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("pro_benchmarks.json の読み込みに失敗: %s", e)
        return {}


# ---------------------------------------------------------------------------
# プレイヤーデータ（JSON 優先、フォールバックでハードコード値を使用）
# ---------------------------------------------------------------------------

# JSON から読み込んだデータ（存在する場合）
_json_data = _load_from_json()

# ハードコードのフォールバックデータ（JSON にキーがない場合に使用）
_HARDCODED: dict[str, PlayerBenchmark] = {

    "翔": PlayerBenchmark(
        display_name="翔（かける）",
        burnout_rate_pct=5.0,    # 世界最強JP。ほぼバーンアウトしない
        opp_burnout_pct=45.0,   # 固め継続と設置で相手ゲージを削る
        punish_conv_pct=92.0,   # ジャストパリィ後の確定精度が最高水準
        lethal_conv_pct=85.0,   # リーサル圏での仕留め率が非常に高い
        deal_ratio_pct=72.0,    # 一方的に優位なダメージ交換
        dr_economy="high",      # 確定コンボ時のみDRを使用。ゲージを温存
        style_label="世界最強JP / ジャストパリィ型",
        style_note=(
            "CAPCOM CUP 11 世界チャンピオン（賞金1億5000万円）。"
            "JP専一で「世界最強JPユーザー」と称される。"
            "ジャストパリィ精度が群を抜いており、相手の攻めを全て見切って確定反撃につなぐ。"
            "ゲージ管理が完璧で自分バーンアウトは皆無に近い。"
            "2025年10月、神経機能不調により現役引退。"
        ),
        verified=True,
    ),

    "ときど": PlayerBenchmark(
        display_name="ときど",
        burnout_rate_pct=10.0,   # ほぼバーンアウトしない
        opp_burnout_pct=35.0,    # 固め継続でゲージを削る
        punish_conv_pct=88.0,    # 確定反撃の精度が最高水準
        lethal_conv_pct=80.0,    # リーサル圏でSAを惜しまない
        deal_ratio_pct=67.0,
        dr_economy="high",       # DRは確定コンボ時のみ厳選使用
        style_label="堅実・計算型",
        style_note=(
            "EVO殿堂入りプロ（REJECT所属）。SFL2025参加。"
            "SF6では中距離牽制と設置の繰り返しで相手ゲージを削り、"
            "確定機会にのみDRを使うゲームメイクが特徴。"
            "パニッシュ精度・SA使いどころが最高水準。"
            "ゲージ管理の教科書的プレイヤー。"
        ),
        verified=True,
    ),

    "りゅうせい": PlayerBenchmark(
        display_name="りゅうせい",
        burnout_rate_pct=28.0,   # DR多用型のため消費多め
        opp_burnout_pct=22.0,    # 攻め重視なので相手BO誘導はやや低め
        punish_conv_pct=70.0,    # SFL2023 JP使用時 6戦2勝（勝率33%）から推定
        lethal_conv_pct=65.0,
        deal_ratio_pct=55.0,
        dr_economy="low",        # DRからアムネジア多用の積極攻め型
        style_label="DR積極使用・アムネジア多用型",
        style_note=(
            "FAV gaming所属プロ（SFL2023優勝チーム）。EVO 2017ブレイブルー世界王者。"
            "ときどをきっかけにJPを始め、SFL2023でJPを使用。"
            "DRからのアムネジア（コマンド投げ）を多用するアグレッシブなスタイルが特徴。"
            "大胆なプレイで相手を崩しに行くが、ゲージ消費は多め。"
            "JP歴は比較的浅く、まだ開拓途上の部分もある攻め型プレイヤー。"
        ),
        verified=True,
    ),

    "Juicyjoe": PlayerBenchmark(
        display_name="Juicyjoe（Joel Sundell）",
        burnout_rate_pct=15.0,   # ゾーニング型なのでゲージ消費は少なめ
        opp_burnout_pct=38.0,    # 安定した固めで相手BOを誘導
        punish_conv_pct=80.0,
        lethal_conv_pct=72.0,
        deal_ratio_pct=63.0,
        dr_economy="high",       # ゾーニング重視のため節約型
        style_label="ゾーニング安定型",
        style_note=(
            "スウェーデン人プロ（NIP所属）。CAPCOM CUP Top 8入り。"
            "EWC 2025出場資格取得済み。"
            "JPのゾーニング性能を最大限に活かす安定型スタイル。"
            "遠距離での設置・飛び道具で相手を縛り、"
            "BOした相手への変換精度が高い。"
        ),
        verified=True,
    ),

    "takepi": PlayerBenchmark(
        display_name="takepi（旧: taketake-piano）",
        burnout_rate_pct=18.0,
        opp_burnout_pct=28.0,
        punish_conv_pct=74.0,
        lethal_conv_pct=66.0,
        deal_ratio_pct=58.0,
        dr_economy="med",
        style_label="地方強豪・チーム戦型",
        style_note=(
            "広島 TEAM iXA所属。旧名: taketake-piano。"
            "World Warrior 2025 Japan 3参加。地方大会で実績を積む強豪プレイヤー。"
            "DRをほぼ確定コンボ時のみ使用しゲージを温存する。"
            "※ 数値は推定値。詳細データは今後更新予定"
        ),
        verified=False,
    ),

    "ふぇんりっち": PlayerBenchmark(
        display_name="ふぇんりっち（Fenritti）",
        burnout_rate_pct=25.0,   # 攻め型なのでゲージ消費多め
        opp_burnout_pct=30.0,
        punish_conv_pct=76.0,
        lethal_conv_pct=70.0,
        deal_ratio_pct=60.0,
        dr_economy="low",        # DR積極使用型
        style_label="押し付け・積極攻め型",
        style_note=(
            "CAG OSAKA所属プロ。SFL2023・2024参加。"
            "「自分のプレイスタイルが押し付けて通して勝つスタイル」と本人が語る。"
            "積極的なDR圧力と崩し択で相手を圧倒するアグレッシブなスタイル。"
            "ゲージ消費は多めだが、その分高い攻め継続力で試合を支配する。"
        ),
        verified=True,
    ),
}

# 最終的な BENCHMARKS: JSON データ優先、なければハードコード
# JSON キーは "player:char" 形式、ハードコードは後方互換のため旧キー形式を維持
BENCHMARKS: dict[str, PlayerBenchmark] = {**_HARDCODED, **_json_data}


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def get_all_players() -> list[str]:
    """登録プレイヤー名リストを返す（重複排除済みのプレイヤー名）。"""
    seen: set[str] = set()
    result: list[str] = []
    for key in BENCHMARKS:
        player = key.split(":")[0]
        if player not in seen:
            seen.add(player)
            result.append(player)
    return result


def get_all_keys() -> list[str]:
    """全キー（"player:char" 形式）を返す。"""
    return list(BENCHMARKS.keys())


def get_benchmark(player_key: str, character: str = "") -> PlayerBenchmark | None:
    """ベンチマークを返す。

    検索順:
      1. "player_key:character" の完全一致（character 指定あり）
      2. "player_key" の完全一致（後方互換）
      3. "player_key:" で始まるキーの最初のヒット
    """
    if character:
        hit = BENCHMARKS.get(f"{player_key}:{character.lower()}")
        if hit:
            return hit
    # 完全一致（ハードコードの旧キーや引数そのまま）
    direct = BENCHMARKS.get(player_key)
    if direct:
        return direct
    # プレフィックスマッチ（キャラ問わず最初のデータ）
    prefix = f"{player_key}:"
    for k, v in BENCHMARKS.items():
        if k.startswith(prefix):
            return v
    return None


def save_benchmark(
    player_key: str,
    character: str,
    benchmark: PlayerBenchmark,
) -> None:
    """ベンチマークデータを JSON に保存する。

    既存のキーがあれば上書き、なければ追加。

    Args:
        player_key: プレイヤー識別子（例: "ときど"）。
        character: キャラクター識別子（例: "jp"）。
        benchmark: 保存する PlayerBenchmark。
    """
    key = f"{player_key}:{character.lower()}"
    existing: dict = {}
    if _JSON_PATH.exists():
        try:
            existing = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    entry = {
        "display_name": benchmark.display_name,
        "character": character.lower(),
        "burnout_rate_pct": benchmark.burnout_rate_pct,
        "opp_burnout_pct": benchmark.opp_burnout_pct,
        "punish_conv_pct": benchmark.punish_conv_pct,
        "lethal_conv_pct": benchmark.lethal_conv_pct,
        "deal_ratio_pct": benchmark.deal_ratio_pct,
        "dr_economy": benchmark.dr_economy,
        "style_label": benchmark.style_label,
        "style_note": benchmark.style_note,
        "verified": benchmark.verified,
    }
    existing[key] = entry
    _JSON_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    # メモリ上の BENCHMARKS も即時更新
    BENCHMARKS[key] = benchmark


def composite_benchmark(character: str = "") -> PlayerBenchmark:
    """登録済みプレイヤーの平均ベンチマーク（総合プロ水準）。

    Args:
        character: 絞り込むキャラクター（例: "jp"）。空文字で全キャラ平均。
    """
    if character:
        vals = [v for k, v in BENCHMARKS.items() if k.endswith(f":{character.lower()}")]
    else:
        vals = list(BENCHMARKS.values())

    if not vals:
        vals = list(BENCHMARKS.values())

    n = len(vals)
    player_names = "・".join(
        k.split(":")[0] for k in list(BENCHMARKS)[:6]
    )
    return PlayerBenchmark(
        display_name=f"プロ{n}名平均" + (f"（{character.upper()}）" if character else ""),
        burnout_rate_pct=round(sum(v.burnout_rate_pct for v in vals) / n, 1),
        opp_burnout_pct=round(sum(v.opp_burnout_pct for v in vals) / n, 1),
        punish_conv_pct=round(sum(v.punish_conv_pct for v in vals) / n, 1),
        lethal_conv_pct=round(sum(v.lethal_conv_pct for v in vals) / n, 1),
        deal_ratio_pct=round(sum(v.deal_ratio_pct for v in vals) / n, 1),
        dr_economy="med",
        style_label=f"{player_names} の平均",
        style_note="登録済みプロの平均ベンチマーク値",
        verified=False,
    )
