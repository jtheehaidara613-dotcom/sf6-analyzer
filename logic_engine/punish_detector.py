"""確定反撃（パニッシュ）判定モジュール。

相手キャラクターがリカバリー状態にある場合、
フレームデータを参照して確定反撃が可能な技を列挙します。

判定ロジック:
    相手の残り硬直フレーム数 >= 自分の技の発生フレーム数
    → その技での確定反撃が成立
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

# frame_data.json のパス（このファイルからの相対位置で解決）
_FRAME_DATA_PATH = Path(__file__).parent.parent / "data" / "frame_data.json"


def _load_frame_data() -> dict:
    """フレームデータJSONを読み込む。

    Returns:
        フレームデータの辞書。

    Raises:
        FileNotFoundError: frame_data.json が見つからない場合。
        json.JSONDecodeError: JSONのパースに失敗した場合。
    """
    with _FRAME_DATA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def detect_punish_opportunity(
    attacker: CharacterState,
    defender: CharacterState,
) -> PunishOpportunity:
    """確定反撃の機会を判定する。

    defenderが RECOVERY 状態にある場合、attacker が使用可能な技の中から
    確定反撃として間に合うものをすべて列挙します。

    判定条件:
        defender.remaining_recovery_frames >= move.startup
        かつ move.sa_cost <= attacker.sa_stock

    Args:
        attacker: 反撃を行う側のキャラクター状態（P1視点）。
        defender: リカバリー中の相手キャラクター状態（P2視点）。

    Returns:
        確定反撃の判定結果を含む PunishOpportunity オブジェクト。

    Raises:
        FileNotFoundError: frame_data.json が存在しない場合。
        KeyError: キャラクターデータがフレームデータに存在しない場合。
    """
    logger.info(
        "確定反撃判定開始 | attacker=%s state=%s, defender=%s state=%s recovery_frames=%d",
        attacker.character.value,
        attacker.frame_state.value,
        defender.character.value,
        defender.frame_state.value,
        defender.remaining_recovery_frames,
    )

    # defender がリカバリー状態でなければ反撃チャンスなし
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

    recovery_frames = defender.remaining_recovery_frames
    punish_moves: list[MoveInfo] = []

    for move_id, move in attacker_moves.items():
        startup: int = move["startup"]
        sa_cost: int = move.get("sa_cost", 0)

        # SAゲージが足りない技は除外
        if sa_cost > attacker.sa_stock:
            logger.debug(
                "SAゲージ不足でスキップ: %s (必要=%d, 保有=%d)",
                move_id,
                sa_cost,
                attacker.sa_stock,
            )
            continue

        # 確定判定: 残り硬直 >= 技の発生
        if recovery_frames >= startup:
            punish_moves.append(
                MoveInfo(
                    move_id=move_id,
                    move_name=move["name"],
                    startup=startup,
                    damage=move["damage"],
                    advantage_on_hit=move["advantage_on_hit"],
                    sa_cost=sa_cost,
                )
            )
            logger.debug(
                "確定反撃技候補: %s (startup=%d, damage=%d)",
                move_id,
                startup,
                move["damage"],
            )

    # ダメージ降順でソート（最も有効な技を先頭に）
    punish_moves.sort(key=lambda m: m.damage, reverse=True)

    if punish_moves:
        best = punish_moves[0]
        description = (
            f"相手の硬直 {recovery_frames}F に対し、"
            f"{len(punish_moves)} 技が確定します。"
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
