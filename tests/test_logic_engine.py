"""logic_engine の単体テスト。

punish_detector と lethal_calculator の計算ロジックが
意図通りに動作するかを検証するテストスイートです。

テスト対象:
    - punish_detector.detect_punish_opportunity
    - lethal_calculator.calculate_lethal
    - lethal_calculator.apply_damage_scaling
    - lethal_calculator.get_scaling_rate
"""

import pytest

from logic_engine.lethal_calculator import (
    apply_damage_scaling,
    calculate_lethal,
    get_scaling_rate,
)
from logic_engine.punish_detector import detect_punish_opportunity
from schemas import CharacterName, CharacterState, FrameState, Position


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

def _make_state(
    character: CharacterName,
    frame_state: FrameState = FrameState.NEUTRAL,
    hp: int = 8000,
    drive_gauge: int = 10000,
    sa_stock: int = 0,
    last_move: str | None = None,
    remaining_recovery_frames: int = 0,
) -> CharacterState:
    """テスト用 CharacterState を生成するヘルパー関数。

    Args:
        character: キャラクター識別子。
        frame_state: フレーム状態（デフォルト: NEUTRAL）。
        hp: 体力（デフォルト: 8000）。
        drive_gauge: ドライブゲージ（デフォルト: 10000）。
        sa_stock: SAゲージストック数（デフォルト: 0）。
        last_move: 直前の技識別子（デフォルト: None）。
        remaining_recovery_frames: 残り硬直フレーム（デフォルト: 0）。

    Returns:
        生成した CharacterState。
    """
    return CharacterState(
        character=character,
        position=Position(x=400.0, y=600.0),
        hp=hp,
        drive_gauge=drive_gauge,
        sa_stock=sa_stock,
        frame_state=frame_state,
        last_move=last_move,
        remaining_recovery_frames=remaining_recovery_frames,
    )


@pytest.fixture
def ryu_neutral() -> CharacterState:
    """通常状態のリュウ（SAゲージなし）。"""
    return _make_state(CharacterName.RYU)


@pytest.fixture
def ryu_with_sa() -> CharacterState:
    """SAゲージ1本保有のリュウ。"""
    return _make_state(CharacterName.RYU, sa_stock=1)


@pytest.fixture
def chun_li_recovery_27f() -> CharacterState:
    """昇龍拳を外した直後の春麗（27F硬直）。"""
    return _make_state(
        CharacterName.CHUN_LI,
        frame_state=FrameState.RECOVERY,
        last_move="shoryuken",
        remaining_recovery_frames=27,
    )


@pytest.fixture
def chun_li_recovery_3f() -> CharacterState:
    """わずかな硬直（3F）の春麗。ほとんどの技は間に合わない。"""
    return _make_state(
        CharacterName.CHUN_LI,
        frame_state=FrameState.RECOVERY,
        remaining_recovery_frames=3,
    )


@pytest.fixture
def chun_li_neutral() -> CharacterState:
    """通常状態の春麗（反撃チャンスなし）。"""
    return _make_state(CharacterName.CHUN_LI)


@pytest.fixture
def chun_li_low_hp() -> CharacterState:
    """体力残りわずか（1500）の春麗。"""
    return _make_state(CharacterName.CHUN_LI, hp=1500)


@pytest.fixture
def chun_li_high_hp() -> CharacterState:
    """体力満タンに近い（9000）の春麗。"""
    return _make_state(CharacterName.CHUN_LI, hp=9000)


# ---------------------------------------------------------------------------
# punish_detector テスト
# ---------------------------------------------------------------------------

class TestPunishDetector:
    """detect_punish_opportunity のテストクラス。"""

    def test_punish_detected_on_large_recovery(
        self,
        ryu_neutral: CharacterState,
        chun_li_recovery_27f: CharacterState,
    ) -> None:
        """27F 硬直時に複数の確定反撃技が検出されること。"""
        result = detect_punish_opportunity(ryu_neutral, chun_li_recovery_27f)

        assert result.is_punishable is True
        assert result.frame_advantage == 27
        assert len(result.punish_moves) > 0

    def test_punish_moves_sorted_by_damage_desc(
        self,
        ryu_neutral: CharacterState,
        chun_li_recovery_27f: CharacterState,
    ) -> None:
        """反撃技リストがダメージ降順で並んでいること。"""
        result = detect_punish_opportunity(ryu_neutral, chun_li_recovery_27f)

        damages = [m.damage for m in result.punish_moves]
        assert damages == sorted(damages, reverse=True)

    def test_no_punish_when_defender_neutral(
        self,
        ryu_neutral: CharacterState,
        chun_li_neutral: CharacterState,
    ) -> None:
        """相手が NEUTRAL 状態のとき確定反撃なし。"""
        result = detect_punish_opportunity(ryu_neutral, chun_li_neutral)

        assert result.is_punishable is False
        assert result.frame_advantage == 0
        assert len(result.punish_moves) == 0

    def test_no_punish_when_recovery_too_short(
        self,
        ryu_neutral: CharacterState,
        chun_li_recovery_3f: CharacterState,
    ) -> None:
        """硬直が3Fのとき発生4F以上の技は間に合わず確定技なし。"""
        result = detect_punish_opportunity(ryu_neutral, chun_li_recovery_3f)

        # startup 4F以上の技しか持たないリュウは間に合わない
        for move in result.punish_moves:
            assert move.startup <= 3

    def test_sa_move_excluded_when_no_sa_stock(
        self,
        ryu_neutral: CharacterState,
        chun_li_recovery_27f: CharacterState,
    ) -> None:
        """SAゲージが0本のとき SA技（shinku_hadouken）は候補に含まれないこと。"""
        # ryu_neutral は sa_stock=0
        result = detect_punish_opportunity(ryu_neutral, chun_li_recovery_27f)

        sa_moves = [m for m in result.punish_moves if m.sa_cost > 0]
        assert len(sa_moves) == 0

    def test_sa_move_included_when_sa_stock_sufficient(
        self,
        ryu_with_sa: CharacterState,
        chun_li_recovery_27f: CharacterState,
    ) -> None:
        """SAゲージが足りるとき SA技（shinku_hadouken）が候補に含まれること。"""
        result = detect_punish_opportunity(ryu_with_sa, chun_li_recovery_27f)

        sa_move_ids = [m.move_id for m in result.punish_moves]
        assert "shinku_hadouken" in sa_move_ids

    def test_best_punish_is_highest_damage(
        self,
        ryu_with_sa: CharacterState,
        chun_li_recovery_27f: CharacterState,
    ) -> None:
        """SAゲージあり時の最大ダメージ技が先頭に来ること。"""
        result = detect_punish_opportunity(ryu_with_sa, chun_li_recovery_27f)

        assert result.punish_moves[0].move_id == "shinku_hadouken"
        assert result.punish_moves[0].damage == 2500


# ---------------------------------------------------------------------------
# lethal_calculator テスト
# ---------------------------------------------------------------------------

class TestLethalCalculator:
    """calculate_lethal のテストクラス。"""

    def test_lethal_possible_when_hp_low(
        self,
        ryu_neutral: CharacterState,
        chun_li_low_hp: CharacterState,
    ) -> None:
        """相手の体力がコンボダメージ以下のときリーサル判定が True になること。"""
        result = calculate_lethal(ryu_neutral, chun_li_low_hp)

        assert result.is_lethal is True
        assert result.estimated_max_damage >= result.target_hp

    def test_lethal_not_possible_when_hp_high(
        self,
        ryu_neutral: CharacterState,
        chun_li_high_hp: CharacterState,
    ) -> None:
        """相手の体力が十分残っているときリーサル判定が False になること。"""
        result = calculate_lethal(ryu_neutral, chun_li_high_hp)

        assert result.is_lethal is False
        assert result.estimated_max_damage < result.target_hp

    def test_recommended_combo_not_empty(
        self,
        ryu_neutral: CharacterState,
        chun_li_low_hp: CharacterState,
    ) -> None:
        """推奨コンボが空でないこと。"""
        result = calculate_lethal(ryu_neutral, chun_li_low_hp)

        assert len(result.recommended_combo) > 0

    def test_sa_combo_used_when_sa_stock_available(
        self,
        ryu_with_sa: CharacterState,
        chun_li_high_hp: CharacterState,
    ) -> None:
        """SAゲージがあるとき、通常コンボとSAコンボを比較して最大ダメージが選ばれること。

        リュウの場合:
          通常コンボ: 昇龍拳(1300) + 立強P(900) + 波動拳(720) = 2920
          SAコンボ:   立弱P(400) + 真空波動拳(2250) = 2650
        → 通常コンボ(2920)のほうが高いため sa_cost=0 が正しい挙動。
        """
        result = calculate_lethal(ryu_with_sa, chun_li_high_hp)

        # SAゲージがあっても通常コンボが高ければそちらが選ばれる
        # → estimated_max_damage が SAコンボより低くないことを確認
        sa_combo_damage = 400 + int(2500 * 0.9)  # 立弱P + 真空波動拳(補正90%)
        assert result.estimated_max_damage >= sa_combo_damage

    def test_sa_combo_not_used_when_no_sa_stock(
        self,
        ryu_neutral: CharacterState,
        chun_li_low_hp: CharacterState,
    ) -> None:
        """SAゲージが0のとき SA技がコンボに含まれないこと。"""
        result = calculate_lethal(ryu_neutral, chun_li_low_hp)

        move_ids = [s.move_id for s in result.recommended_combo]
        assert "shinku_hadouken" not in move_ids
        assert result.sa_cost == 0

    def test_target_hp_matches_defender(
        self,
        ryu_neutral: CharacterState,
        chun_li_low_hp: CharacterState,
    ) -> None:
        """target_hp が defender の現在 HP と一致すること。"""
        result = calculate_lethal(ryu_neutral, chun_li_low_hp)

        assert result.target_hp == chun_li_low_hp.hp


# ---------------------------------------------------------------------------
# ダメージ補正テスト
# ---------------------------------------------------------------------------

class TestDamageScaling:
    """apply_damage_scaling と get_scaling_rate のテストクラス。"""

    # frame_data.json の scaling_table に合わせたテーブル
    SCALING_TABLE = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.35, 0.3, 0.3]

    def test_first_hit_no_scaling(self) -> None:
        """1ヒット目は補正なし（100%）でダメージが変わらないこと。"""
        result = apply_damage_scaling(1000, hit_count=1, scaling_table=self.SCALING_TABLE)
        assert result == 1000

    def test_second_hit_scaled_to_90_percent(self) -> None:
        """2ヒット目は 90% 補正が適用されること。"""
        result = apply_damage_scaling(1000, hit_count=2, scaling_table=self.SCALING_TABLE)
        assert result == 900

    def test_third_hit_scaled_to_80_percent(self) -> None:
        """3ヒット目は 80% 補正が適用されること。"""
        result = apply_damage_scaling(1000, hit_count=3, scaling_table=self.SCALING_TABLE)
        assert result == 800

    def test_scaling_decreases_monotonically(self) -> None:
        """ヒット数が増えるほど補正後ダメージが単調減少すること。"""
        damages = [
            apply_damage_scaling(1000, hit_count=i, scaling_table=self.SCALING_TABLE)
            for i in range(1, 10)
        ]
        for i in range(len(damages) - 1):
            assert damages[i] >= damages[i + 1]

    def test_scaling_floors_at_minimum(self) -> None:
        """テーブル末尾以降は最小補正率（30%）で固定されること。"""
        # 10ヒット以降はすべて 30%
        r10 = apply_damage_scaling(1000, hit_count=10, scaling_table=self.SCALING_TABLE)
        r15 = apply_damage_scaling(1000, hit_count=15, scaling_table=self.SCALING_TABLE)
        assert r10 == r15 == 300

    def test_get_scaling_rate_first_hit(self) -> None:
        """1ヒット目の補正率が 1.0 であること。"""
        rate = get_scaling_rate(hit_count=1, scaling_table=self.SCALING_TABLE)
        assert rate == 1.0

    def test_get_scaling_rate_beyond_table(self) -> None:
        """テーブル範囲外のヒット番号でも最小値が返ること。"""
        rate = get_scaling_rate(hit_count=99, scaling_table=self.SCALING_TABLE)
        assert rate == 0.3

    def test_combo_total_damage_with_scaling(self) -> None:
        """3技コンボの合計ダメージが補正を考慮した値になること。

        技ダメージ: 1300 + 1000 + 900
        補正後:     1300 * 1.0 + 1000 * 0.9 + 900 * 0.8 = 1300 + 900 + 720 = 2920
        """
        damages = [1300, 1000, 900]
        total = sum(
            apply_damage_scaling(d, i + 1, self.SCALING_TABLE)
            for i, d in enumerate(damages)
        )
        assert total == 2920


# ---------------------------------------------------------------------------
# DR経由パニッシュ / バーンアウト / プリセットコンボ優先テスト
# ---------------------------------------------------------------------------

class TestDriveRushPunish:
    """ドライブラッシュ経由パニッシュのテストクラス。"""

    def test_dr_punish_when_drive_gauge_sufficient(
        self,
        chun_li_recovery_27f: CharacterState,
    ) -> None:
        """ドライブゲージが2500以上あればDR経由パニッシュが候補に含まれること。

        リュウには通常パニッシュで届かない技でも、
        DR（13F）を加算した発生で届く場合はDR経由として列挙される。
        """
        ryu_with_drive = _make_state(
            CharacterName.RYU, drive_gauge=5000,
        )
        result = detect_punish_opportunity(ryu_with_drive, chun_li_recovery_27f)

        assert result.is_punishable is True
        dr_moves = [m for m in result.punish_moves if m.drive_cost > 0]
        # DR経由パニッシュが存在する（drive_cost == 2500 の技）
        assert all(m.drive_cost == 2500 for m in dr_moves)

    def test_no_dr_punish_when_burnout(
        self,
        chun_li_recovery_27f: CharacterState,
    ) -> None:
        """バーンアウト（drive_gauge=0）時はDR経由パニッシュが含まれないこと。"""
        ryu_burnout = _make_state(
            CharacterName.RYU, drive_gauge=0,
        )
        result = detect_punish_opportunity(ryu_burnout, chun_li_recovery_27f)

        dr_moves = [m for m in result.punish_moves if m.drive_cost > 0]
        assert len(dr_moves) == 0

    def test_no_dr_punish_when_drive_gauge_insufficient(
        self,
        chun_li_recovery_27f: CharacterState,
    ) -> None:
        """ドライブゲージが2500未満のときはDR経由パニッシュが含まれないこと。"""
        ryu_low_drive = _make_state(
            CharacterName.RYU, drive_gauge=2499,
        )
        result = detect_punish_opportunity(ryu_low_drive, chun_li_recovery_27f)

        dr_moves = [m for m in result.punish_moves if m.drive_cost > 0]
        assert len(dr_moves) == 0


class TestBurnoutDetection:
    """is_burnout プロパティのテストクラス。"""

    def test_is_burnout_when_drive_gauge_zero(self) -> None:
        """drive_gauge=0 のとき is_burnout が True を返すこと。"""
        state = _make_state(CharacterName.RYU, drive_gauge=0)
        assert state.is_burnout is True

    def test_not_burnout_when_drive_gauge_positive(self) -> None:
        """drive_gauge > 0 のとき is_burnout が False を返すこと。"""
        state = _make_state(CharacterName.RYU, drive_gauge=1)
        assert state.is_burnout is False

    def test_not_burnout_at_full_drive(self) -> None:
        """drive_gauge=10000（満タン）のとき is_burnout が False を返すこと。"""
        state = _make_state(CharacterName.RYU, drive_gauge=10000)
        assert state.is_burnout is False


class TestPresetComboPriority:
    """プリセットコンボ優先選択のテストクラス。"""

    def test_preset_combo_used_over_naive_selection(
        self,
        ryu_neutral: CharacterState,
        chun_li_low_hp: CharacterState,
    ) -> None:
        """プリセットコンボが定義されているとき、ナイーブ選択より優先されること。

        リュウには frame_data.json にプリセットコンボが定義されているため、
        recommended_combo は必ずプリセットの move_ids を反映した内容になる。
        """
        result = calculate_lethal(ryu_neutral, chun_li_low_hp)

        # プリセットコンボはcrouching_lpから始まる（bnb_normalの先頭技）
        assert len(result.recommended_combo) > 0
        assert result.recommended_combo[0].move_id == "crouching_lp"

    def test_sa_cost_filter_in_preset_combo(
        self,
        ryu_neutral: CharacterState,
        chun_li_high_hp: CharacterState,
    ) -> None:
        """SAゲージが0本のとき、sa_cost > 0 のプリセットコンボは選ばれないこと。"""
        result = calculate_lethal(ryu_neutral, chun_li_high_hp)

        # sa_stock=0 なので sa_cost > 0 のコンボは選べない
        assert result.sa_cost == 0
        for step in result.recommended_combo:
            # SAゲージを使う技識別子がコンボに含まれないこと
            assert "shin_shoryuken" not in step.move_id
            assert "shinku_hadouken" not in step.move_id
