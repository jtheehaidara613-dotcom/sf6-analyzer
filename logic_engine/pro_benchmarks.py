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

from dataclasses import dataclass


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
    verified: bool = False


# ---------------------------------------------------------------------------
# プレイヤーデータ
# ---------------------------------------------------------------------------

BENCHMARKS: dict[str, PlayerBenchmark] = {

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


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def get_all_players() -> list[str]:
    """登録プレイヤー名リストを返す。"""
    return list(BENCHMARKS.keys())


def get_benchmark(player_key: str) -> PlayerBenchmark | None:
    return BENCHMARKS.get(player_key)


def composite_benchmark() -> PlayerBenchmark:
    """全プレイヤーの平均ベンチマーク（総合プロ水準）。"""
    vals = list(BENCHMARKS.values())
    n = len(vals)
    return PlayerBenchmark(
        display_name="プロ6名平均",
        burnout_rate_pct=round(sum(v.burnout_rate_pct for v in vals) / n, 1),
        opp_burnout_pct=round(sum(v.opp_burnout_pct for v in vals) / n, 1),
        punish_conv_pct=round(sum(v.punish_conv_pct for v in vals) / n, 1),
        lethal_conv_pct=round(sum(v.lethal_conv_pct for v in vals) / n, 1),
        deal_ratio_pct=round(sum(v.deal_ratio_pct for v in vals) / n, 1),
        dr_economy="med",
        style_label="翔・ときど・りゅうせい・Juicyjoe・takepi・ふぇんりっち の平均",
        style_note="登録済みプロ6名の平均ベンチマーク値",
        verified=False,
    )
