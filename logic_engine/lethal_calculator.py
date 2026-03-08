"""リーサル（倒し切り）計算モジュール。

現在のゲーム状況（体力・ゲージ）から、攻撃側が1コンボで
相手を倒し切れるかどうかを判定し、推奨コンボを提案します。

コンボ選択の優先順位:
    1. frame_data.json の "combos" に定義された実践的なコンボルートを試みる
    2. SAゲージとドライブゲージの条件を満たすコンボを列挙し、最大ダメージを選択
    3. 定義済みコンボがない場合は従来のナイーブな上位技選択にフォールバック

ダメージ補正ルール:
    コンボのヒット数が増えるごとにダメージ補正率が低下します。
    補正テーブルは frame_data.json の damage_scaling.scaling_table に定義されています。
    例: 1hit目=100%, 2hit目=90%, 3hit目=80% ...
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
    with _FRAME_DATA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def apply_damage_scaling(base_damage: int, hit_count: int, scaling_table: list[float]) -> int:
    """ダメージ補正を適用してスケーリング後のダメージを返す。"""
    index = min(hit_count - 1, len(scaling_table) - 1)
    rate = scaling_table[index]
    return int(base_damage * rate)


def get_scaling_rate(hit_count: int, scaling_table: list[float]) -> float:
    """指定ヒット番号の補正率を返す。"""
    index = min(hit_count - 1, len(scaling_table) - 1)
    return scaling_table[index]


def _build_combo(
    moves_data: dict,
    move_ids: list[str],
    scaling_table: list[float],
) -> tuple[list[ComboStep], int]:
    """指定の技順でコンボを組み立て、合計ダメージを計算する。

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


def _combo_drive_cost(moves_data: dict, move_ids: list[str]) -> int:
    """コンボ全体のドライブゲージ消費量を計算する。"""
    return sum(moves_data[mid].get("drive_cost", 0) for mid in move_ids if mid in moves_data)


def _get_preset_combo(
    moves_data: dict,
    combo_presets: list[dict],
    sa_stock: int,
    drive_gauge: int,
    is_burnout: bool,
    scaling_table: list[float],
) -> tuple[list[ComboStep], int, str, int, int] | None:
    """frame_data.json のプリセットコンボから最大ダメージのものを選択する。

    SA・ドライブゲージの両方の条件を満たすコンボのみを対象とする。
    バーンアウト中はドライブゲージを消費するOD技・DR技を含むコンボを除外する。

    Args:
        moves_data: キャラクターの技データ辞書。
        combo_presets: キャラクターのコンボプリセットリスト。
        sa_stock: 現在のSAゲージストック数。
        drive_gauge: 現在のドライブゲージ量（0〜10000）。
        is_burnout: バーンアウト状態かどうか。
        scaling_table: ダメージ補正テーブル。

    Returns:
        (ComboStep リスト, 合計ダメージ, コンボ名, SAコスト, ドライブコスト) または None。
    """
    best_steps: list[ComboStep] | None = None
    best_damage = 0
    best_name = ""
    best_sa_cost = 0
    best_drive_cost = 0

    for preset in combo_presets:
        sa_cost = preset.get("sa_cost", 0)
        if sa_cost > sa_stock:
            continue

        move_ids = preset["move_ids"]
        if not all(mid in moves_data for mid in move_ids):
            continue

        # ドライブゲージ制約チェック
        drive_cost = preset.get("drive_cost") or _combo_drive_cost(moves_data, move_ids)
        if drive_cost > drive_gauge:
            logger.debug("ドライブゲージ不足でスキップ: %s (必要=%d, 保有=%d)", preset["name"], drive_cost, drive_gauge)
            continue

        # バーンアウト中はドライブゲージを消費する技を含むコンボを除外
        if is_burnout and drive_cost > 0:
            logger.debug("バーンアウト中のためOD/DR含みコンボをスキップ: %s", preset["name"])
            continue

        steps, damage = _build_combo(moves_data, move_ids, scaling_table)
        if damage > best_damage:
            best_damage = damage
            best_steps = steps
            best_name = preset["name"]
            best_sa_cost = sa_cost
            best_drive_cost = drive_cost

    if best_steps is None:
        return None
    return best_steps, best_damage, best_name, best_sa_cost, best_drive_cost


def _get_normal_combo(
    moves_data: dict,
    drive_gauge: int,
    is_burnout: bool,
    scaling_table: list[float],
) -> tuple[list[ComboStep], int, int]:
    """SAゲージを使わない通常コンボを構築する（フォールバック用）。

    sa_cost == 0 かつドライブゲージ条件を満たす技のみを使用し、
    ダメージ上位3技で構成します。

    Returns:
        (ComboStep リスト, 合計ダメージ, ドライブコスト) のタプル。
    """
    normal_moves = [
        (mid, m) for mid, m in moves_data.items()
        if m.get("sa_cost", 0) == 0
        and (not is_burnout or m.get("drive_cost", 0) == 0)
        and m.get("drive_cost", 0) <= drive_gauge
    ]
    top3 = sorted(normal_moves, key=lambda x: x[1]["damage"], reverse=True)[:3]
    top3.sort(key=lambda x: x[1]["startup"])
    move_ids = [mid for mid, _ in top3]
    steps, damage = _build_combo(moves_data, move_ids, scaling_table)
    drive_cost = _combo_drive_cost(moves_data, move_ids)
    return steps, damage, drive_cost


def _get_sa_combo(
    moves_data: dict,
    sa_stock: int,
    drive_gauge: int,
    is_burnout: bool,
    scaling_table: list[float],
) -> tuple[list[ComboStep], int, int] | None:
    """SAゲージを使用したコンボを構築する（フォールバック用）。

    Returns:
        (ComboStep リスト, 合計ダメージ, ドライブコスト) または None。
    """
    sa_moves = [
        (mid, m) for mid, m in moves_data.items()
        if 0 < m.get("sa_cost", 0) <= sa_stock
        and m.get("drive_cost", 0) <= drive_gauge
    ]
    if not sa_moves:
        return None

    best_sa = max(sa_moves, key=lambda x: x[1]["damage"])
    sa_move_id, _ = best_sa

    normal_moves = [
        (mid, m) for mid, m in moves_data.items()
        if m.get("sa_cost", 0) == 0
        and (not is_burnout or m.get("drive_cost", 0) == 0)
        and m.get("drive_cost", 0) <= drive_gauge
    ]
    if not normal_moves:
        return None
    fastest_normal = min(normal_moves, key=lambda x: x[1]["startup"])
    move_ids = [fastest_normal[0], sa_move_id]
    steps, damage = _build_combo(moves_data, move_ids, scaling_table)
    drive_cost = _combo_drive_cost(moves_data, move_ids)
    return steps, damage, drive_cost


def calculate_lethal(
    attacker: CharacterState,
    defender: CharacterState,
) -> LethalResult:
    """リーサル可否を計算し、推奨コンボを返す。

    attacker が1コンボで defender の残り体力をゼロにできるかを判定します。
    frame_data.json に定義されたプリセットコンボを優先的に試み、
    最大ダメージの組み合わせを推奨コンボとして返します。

    Args:
        attacker: 攻撃側のキャラクター状態。
        defender: 守備側（ダメージを受ける側）のキャラクター状態。

    Returns:
        リーサル判定結果と推奨コンボを含む LethalResult オブジェクト。
    """
    logger.info(
        "リーサル計算開始 | attacker=%s, defender=%s HP=%d, SA=%d",
        attacker.character.value,
        defender.character.value,
        defender.hp,
        attacker.sa_stock,
    )

    frame_data = _load_frame_data()
    attacker_key = attacker.character.value
    char_data: dict = frame_data["characters"][attacker_key]
    moves_data: dict = char_data["moves"]
    scaling_table: list[float] = frame_data["damage_scaling"]["scaling_table"]
    combo_presets: list[dict] = char_data.get("combos", [])

    target_hp = defender.hp
    combo_name = ""
    sa_cost_used = 0
    drive_cost_used = 0
    is_burnout = attacker.is_burnout

    logger.debug(
        "ゲージ状態: drive=%d burnout=%s sa=%d",
        attacker.drive_gauge, is_burnout, attacker.sa_stock,
    )

    # ── プリセットコンボを優先 ──────────────────────────────────────────
    preset_result = _get_preset_combo(
        moves_data, combo_presets,
        attacker.sa_stock, attacker.drive_gauge, is_burnout,
        scaling_table,
    )

    if preset_result is not None:
        best_steps, best_damage, combo_name, sa_cost_used, drive_cost_used = preset_result
        logger.debug("プリセットコンボ選択: %s / ダメージ=%d drive_cost=%d", combo_name, best_damage, drive_cost_used)
    else:
        # フォールバック: ナイーブな上位技選択
        logger.debug("プリセットコンボなし。フォールバックを使用。")
        normal_steps, normal_damage, normal_drive = _get_normal_combo(
            moves_data, attacker.drive_gauge, is_burnout, scaling_table,
        )
        sa_result = _get_sa_combo(
            moves_data, attacker.sa_stock, attacker.drive_gauge, is_burnout, scaling_table,
        )

        if sa_result is not None:
            sa_steps, sa_damage, sa_drive = sa_result
            if sa_damage >= normal_damage:
                best_steps, best_damage, drive_cost_used = sa_steps, sa_damage, sa_drive
                sa_cost_used = max(
                    (moves_data[s.move_id].get("sa_cost", 0) for s in sa_steps),
                    default=0,
                )
            else:
                best_steps, best_damage, drive_cost_used = normal_steps, normal_damage, normal_drive
        else:
            best_steps, best_damage, drive_cost_used = normal_steps, normal_damage, normal_drive

    is_lethal = best_damage >= target_hp

    if is_lethal:
        combo_label = f"「{combo_name}」で" if combo_name else ""
        description = (
            f"リーサル確定！{combo_label}{best_damage:,} ダメージ → "
            f"相手体力 {target_hp:,} を超えます。"
        )
        logger.info("リーサル確定: damage=%d >= hp=%d", best_damage, target_hp)
    else:
        shortage = target_hp - best_damage
        combo_label = f"「{combo_name}」の" if combo_name else ""
        description = (
            f"リーサル不可。{combo_label}最大ダメージ {best_damage:,} に対し "
            f"相手体力 {target_hp:,}（あと {shortage:,} 足りません）。"
        )
        logger.info("リーサル不可: damage=%d < hp=%d (shortage=%d)", best_damage, target_hp, shortage)

    return LethalResult(
        is_lethal=is_lethal,
        target_hp=target_hp,
        estimated_max_damage=best_damage,
        recommended_combo=best_steps,
        drive_cost=drive_cost_used,
        sa_cost=sa_cost_used,
        description=description,
    )
