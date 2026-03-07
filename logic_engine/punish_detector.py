"""確定反撃（パニッシュ）判定モジュール。

相手キャラクターがリカバリー状態にある場合、
フレームデータを参照して確定反撃が可能な技を列挙します。

判定ロジック:
    通常パニッシュ:
        相手の残り硬直フレーム数 >= 自分の技の発生フレーム数
        → その技での確定反撃が成立

    ドライブラッシュ経由パニッシュ:
        ドライブゲージが 2500 以上あり、
        相手の残り硬直フレーム数 >= 自分の技の発生フレーム数 + DR発生フレーム（13F）
        → DR経由で距離を詰めてからその技で確定反撃が成立
"""

import json
import logging
from pathlib import Path

from schemas import (
    CharacterName,
    CharacterState,
    FrameState,
    MoveInfo,
    PunishOpportunity,
)

logger = logging.getLogger(__name__)

_FRAME_DATA_PATH = Path(__file__).parent.parent / "data" / "frame_data.json"


def _load_frame_data() -> dict:
    with _FRAME_DATA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def detect_punish_opportunity(
    attacker: CharacterState,
    defender: CharacterState,
) -> PunishOpportunity:
    """確定反撃の機会を判定する。

    defenderが RECOVERY 状態にある場合、attacker が使用可能な技の中から
    確定反撃として間に合うものをすべて列挙します。
    ドライブゲージが十分にある場合はドライブラッシュ経由の追加候補も含みます。

    判定条件（通常）:
        defender.remaining_recovery_frames >= move.startup
        かつ move.sa_cost <= attacker.sa_stock

    判定条件（ドライブラッシュ経由）:
        attacker.drive_gauge >= drive_rush.drive_cost_neutral（2500）
        かつ defender.remaining_recovery_frames >= move.startup + drive_rush.startup_frames（13F）
        かつ move.sa_cost <= attacker.sa_stock

    Args:
        attacker: 反撃を行う側のキャラクター状態（P1視点）。
        defender: リカバリー中の相手キャラクター状態（P2視点）。

    Returns:
        確定反撃の判定結果を含む PunishOpportunity オブジェクト。
    """
    logger.info(
        "確定反撃判定開始 | attacker=%s state=%s, defender=%s state=%s recovery_frames=%d",
        attacker.character.value,
        attacker.frame_state.value,
        defender.character.value,
        defender.frame_state.value,
        defender.remaining_recovery_frames,
    )

    if defender.frame_state != FrameState.RECOVERY:
        logger.info(
            "defender が RECOVERY 状態ではないため反撃チャンスなし (state=%s)",
            defender.frame_state.value,
        )
        return PunishOpportunity(
            is_punishable=False,
            frame_advantage=0,
            punish_moves=[],
            description=(
                f"相手は {defender.frame_state.value} 状態のため確定反撃チャンスはありません。"
            ),
        )

    frame_data = _load_frame_data()
    attacker_key = attacker.character.value
    attacker_moves: dict = frame_data["characters"][attacker_key]["moves"]

    dr_config = frame_data.get("drive_rush", {})
    dr_startup: int = dr_config.get("startup_frames", 13)
    dr_cost: int = dr_config.get("drive_cost_neutral", 2500)
    can_drive_rush = attacker.drive_gauge >= dr_cost and not attacker.is_burnout

    recovery_frames = defender.remaining_recovery_frames
    punish_moves: list[MoveInfo] = []

    for move_id, move in attacker_moves.items():
        startup: int = move["startup"]
        sa_cost: int = move.get("sa_cost", 0)

        if sa_cost > attacker.sa_stock:
            logger.debug(
                "SAゲージ不足でスキップ: %s (必要=%d, 保有=%d)",
                move_id, sa_cost, attacker.sa_stock,
            )
            continue

        # 通常パニッシュ
        if recovery_frames >= startup:
            punish_moves.append(MoveInfo(
                move_id=move_id,
                move_name=move["name"],
                startup=startup,
                damage=move["damage"],
                advantage_on_hit=move["advantage_on_hit"],
                sa_cost=sa_cost,
                drive_cost=0,
            ))
            logger.debug(
                "確定反撃技候補（通常）: %s (startup=%d, damage=%d)",
                move_id, startup, move["damage"],
            )
        # ドライブラッシュ経由パニッシュ
        elif can_drive_rush and recovery_frames >= startup + dr_startup:
            punish_moves.append(MoveInfo(
                move_id=move_id,
                move_name=f"{move['name']}（DR経由）",
                startup=startup + dr_startup,
                damage=move["damage"],
                advantage_on_hit=move["advantage_on_hit"],
                sa_cost=sa_cost,
                drive_cost=dr_cost,
            ))
            logger.debug(
                "確定反撃技候補（DR経由）: %s (startup=%d+%dDR, damage=%d)",
                move_id, startup, dr_startup, move["damage"],
            )

    # ダメージ降順でソート（DR経由は通常より後ろ＝コスト考慮）
    punish_moves.sort(key=lambda m: (m.drive_cost == 0, m.damage), reverse=True)

    if punish_moves:
        best = punish_moves[0]
        dr_note = "（ドライブラッシュ経由含む）" if any(m.drive_cost > 0 for m in punish_moves) else ""
        description = (
            f"相手の硬直 {recovery_frames}F に対し、"
            f"{len(punish_moves)} 技が確定します{dr_note}。"
            f"最大ダメージ技: {best.move_name}（{best.damage}ダメージ）。"
        )
        logger.info("確定反撃あり: %d 技が確定。最大: %s", len(punish_moves), best.move_name)
    else:
        description = (
            f"相手の硬直 {recovery_frames}F ですが、"
            "発生が間に合う技がありません（ゲージ不足含む）。"
        )
        logger.info("確定反撃なし（発生が間に合う技なし）")

    return PunishOpportunity(
        is_punishable=len(punish_moves) > 0,
        frame_advantage=recovery_frames,
        punish_moves=punish_moves,
        description=description,
    )
