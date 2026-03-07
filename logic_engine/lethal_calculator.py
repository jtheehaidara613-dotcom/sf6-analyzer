"""リーサル（倒し切り）計算モジュール。

現在のゲーム状況（体力・ゲージ）から、攻撃側が1コンボで
相手を倒し切れるかどうかを判定し、推奨コンボを提案します。

ダメージ補正ルール:
    コンボのヒット数が増えるごとにダメージ補正率が低下します。
    補正テーブルは frame_data.json の damage_scaling.scaling_table に定義されています。
    例: 1hit目=100%, 2hit目=90%, 3hit目=80% ...

コンボ戦略:
    1. 通常技のみコンボ（ゲージ不要）
    2. SA技を組み込んだコンボ（SAゲージが必要）
    の2パターンを計算し、最大ダメージのものを推奨します。
"""

import json
import logging
from pathlib import Path

from schemas import (
    CharacterState,
    ComboStep,
    LethalResult,
)

logger = logging.getLogger(__name__)

_FRAME_DATA_PATH = Path(__file__).parent.parent / "data" / "frame_data.json"


def _load_frame_data() -> dict:
    """フレームデータJSONを読み込む。

    Returns:
        フレームデータの辞書。

    Raises:
        FileNotFoundError: frame_data.json が見つからない場合。
    """
    with _FRAME_DATA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def apply_damage_scaling(base_damage: int, hit_count: int, scaling_table: list[float]) -> int:
    """ダメージ補正を適用してスケーリング後のダメージを返す。

    Args:
        base_damage: 補正前の基本ダメージ。
        hit_count: コンボ内でのヒット番号（1始まり）。
        scaling_table: ヒット数に対応する補正率テーブル（0-indexed）。

    Returns:
        ダメージ補正適用後の整数ダメージ値。
    """
    index = min(hit_count - 1, len(scaling_table) - 1)
    rate = scaling_table[index]
    return int(base_damage * rate)


def get_scaling_rate(hit_count: int, scaling_table: list[float]) -> float:
    """指定ヒット番号の補正率を返す。

    Args:
        hit_count: コンボ内でのヒット番号（1始まり）。
        scaling_table: ヒット数に対応する補正率テーブル（0-indexed）。

    Returns:
        適用される補正率（0.0〜1.0）。
    """
    index = min(hit_count - 1, len(scaling_table) - 1)
    return scaling_table[index]


def _build_combo(
    moves_data: dict,
    move_ids: list[str],
    scaling_table: list[float],
) -> tuple[list[ComboStep], int]:
    """指定の技順でコンボを組み立て、合計ダメージを計算する。

    Args:
        moves_data: キャラクターの技データ辞書。
        move_ids: 技識別子のリスト（コンボ順）。
        scaling_table: ダメージ補正テーブル。

    Returns:
        (ComboStep のリスト, 合計ダメージ) のタプル。
    """
    steps: list[ComboStep] = []
    total_damage = 0

    for hit_num, move_id in enumerate(move_ids, start=1):
        move = moves_data[move_id]
        rate = get_scaling_rate(hit_num, scaling_table)
        scaled = apply_damage_scaling(move["damage"], hit_num, scaling_table)
        total_damage += scaled

        steps.append(ComboStep(
            move_id=move_id,
            move_name=move["name"],
            hit_count=hit_num,
            scaled_damage=scaled,
            scaling_rate=rate,
        ))

    return steps, total_damage


def _get_normal_combo(moves_data: dict, scaling_table: list[float]) -> tuple[list[ComboStep], int]:
    """SAゲージを使わない通常コンボを構築する。

    sa_cost == 0 の技のみを使用し、ダメージ上位3技で構成します。

    Args:
        moves_data: キャラクターの技データ辞書。
        scaling_table: ダメージ補正テーブル。

    Returns:
        (ComboStep のリスト, 合計ダメージ) のタプル。
    """
    normal_moves = [
        (mid, m) for mid, m in moves_data.items()
        if m.get("sa_cost", 0) == 0
    ]
    # ダメージ降順で上位3技を選択（コンボ始動技〜締め技）
    top3 = sorted(normal_moves, key=lambda x: x[1]["damage"], reverse=True)[:3]
    # 発生が早い順（始動→締め）に並び替え
    top3.sort(key=lambda x: x[1]["startup"])
    move_ids = [mid for mid, _ in top3]
    return _build_combo(moves_data, move_ids, scaling_table)


def _get_sa_combo(
    moves_data: dict,
    sa_stock: int,
    scaling_table: list[float],
) -> tuple[list[ComboStep], int] | None:
    """SAゲージを使用したコンボを構築する。

    保有SAゲージストック数に見合うSA技を1つ組み込んだコンボを構築します。
    利用可能なSA技がなければ None を返します。

    Args:
        moves_data: キャラクターの技データ辞書。
        sa_stock: 現在のSAゲージストック数。
        scaling_table: ダメージ補正テーブル。

    Returns:
        (ComboStep のリスト, 合計ダメージ) のタプル、またはSA技なしの場合 None。
    """
    sa_moves = [
        (mid, m) for mid, m in moves_data.items()
        if 0 < m.get("sa_cost", 0) <= sa_stock
    ]
    if not sa_moves:
        return None

    # 最もダメージの高いSA技を選択
    best_sa = max(sa_moves, key=lambda x: x[1]["damage"])
    sa_move_id, sa_move_data = best_sa

    # SA技の前に入れるノーマル技（発生最速の通常技1つ）
    normal_moves = [
        (mid, m) for mid, m in moves_data.items()
        if m.get("sa_cost", 0) == 0
    ]
    fastest_normal = min(normal_moves, key=lambda x: x[1]["startup"])
    move_ids = [fastest_normal[0], sa_move_id]

    return _build_combo(moves_data, move_ids, scaling_table)


def calculate_lethal(
    attacker: CharacterState,
    defender: CharacterState,
) -> LethalResult:
    """リーサル可否を計算し、推奨コンボを返す。

    attacker が1コンボで defender の残り体力をゼロにできるかを判定します。
    SAゲージを活用したコンボと通常コンボの両方を計算し、
    最大ダメージの組み合わせを推奨コンボとして返します。

    Args:
        attacker: 攻撃側のキャラクター状態。
        defender: 守備側（ダメージを受ける側）のキャラクター状態。

    Returns:
        リーサル判定結果と推奨コンボを含む LethalResult オブジェクト。

    Raises:
        FileNotFoundError: frame_data.json が存在しない場合。
        KeyError: キャラクターデータがフレームデータに存在しない場合。
    """
    logger.info(
        "リーサル計算開始 | attacker=%s, defender=%s HP=%d, attacker SA=%d",
        attacker.character.value,
        defender.character.value,
        defender.hp,
        attacker.sa_stock,
    )

    frame_data = _load_frame_data()
    attacker_key = attacker.character.value
    moves_data: dict = frame_data["characters"][attacker_key]["moves"]
    scaling_table: list[float] = frame_data["damage_scaling"]["scaling_table"]

    target_hp = defender.hp

    # 通常コンボを計算
    normal_steps, normal_damage = _get_normal_combo(moves_data, scaling_table)
    logger.debug("通常コンボ合計ダメージ: %d", normal_damage)

    # SAコンボを計算（ゲージがあれば）
    sa_result = _get_sa_combo(moves_data, attacker.sa_stock, scaling_table)

    # 最大ダメージのコンボを選択
    if sa_result is not None:
        sa_steps, sa_damage = sa_result
        logger.debug("SAコンボ合計ダメージ: %d", sa_damage)
        if sa_damage >= normal_damage:
            best_steps, best_damage = sa_steps, sa_damage
            sa_cost_used = max(
                (moves_data[s.move_id].get("sa_cost", 0) for s in sa_steps),
                default=0,
            )
        else:
            best_steps, best_damage = normal_steps, normal_damage
            sa_cost_used = 0
    else:
        best_steps, best_damage = normal_steps, normal_damage
        sa_cost_used = 0

    is_lethal = best_damage >= target_hp

    if is_lethal:
        description = (
            f"リーサル確定！推奨コンボで {best_damage} ダメージ → "
            f"相手体力 {target_hp} を超えます。"
        )
        logger.info(
            "リーサル確定: damage=%d >= hp=%d",
            best_damage,
            target_hp,
        )
    else:
        shortage = target_hp - best_damage
        description = (
            f"リーサル不可。最大ダメージ {best_damage} に対し "
            f"相手体力 {target_hp}（あと {shortage} 足りません）。"
        )
        logger.info(
            "リーサル不可: damage=%d < hp=%d (shortage=%d)",
            best_damage,
            target_hp,
            shortage,
        )

    return LethalResult(
        is_lethal=is_lethal,
        target_hp=target_hp,
        estimated_max_damage=best_damage,
        recommended_combo=best_steps,
        drive_cost=0,  # 現フェーズではドライブゲージ消費は0固定
        sa_cost=sa_cost_used,
        description=description,
    )
