"""schemas.py の単体テスト。

char_to_enum・CHARACTER_MAX_HP・CHARACTER_LABELS の正確性を検証する。
"""

import pytest

from schemas import (
    CHARACTER_LABELS,
    CHARACTER_MAX_HP,
    CharacterName,
    char_to_enum,
)


class TestCharToEnum:
    """char_to_enum の変換ロジックテスト。"""

    def test_enum_value_lowercase(self):
        """enum値（小文字）から直接変換できること。"""
        assert char_to_enum("ryu") == CharacterName.RYU
        assert char_to_enum("dee_jay") == CharacterName.DEE_JAY
        assert char_to_enum("chun_li") == CharacterName.CHUN_LI
        assert char_to_enum("m_bison") == CharacterName.M_BISON

    def test_enum_name_uppercase(self):
        """enum名（大文字）から変換できること。"""
        assert char_to_enum("RYU") == CharacterName.RYU
        assert char_to_enum("DEE_JAY") == CharacterName.DEE_JAY
        assert char_to_enum("CHUN_LI") == CharacterName.CHUN_LI

    def test_ocr_hyphen_variant(self):
        """ハイフン区切り（OCR出力）から変換できること。"""
        assert char_to_enum("CHUN-LI") == CharacterName.CHUN_LI

    def test_ocr_space_variant(self):
        """スペース区切り（OCR出力）から変換できること。"""
        assert char_to_enum("DEE JAY") == CharacterName.DEE_JAY

    def test_aliases(self):
        """エイリアスから変換できること。"""
        assert char_to_enum("CHUN") == CharacterName.CHUN_LI
        assert char_to_enum("BISON") == CharacterName.M_BISON
        assert char_to_enum("MBISON") == CharacterName.M_BISON
        assert char_to_enum("DEEJAY") == CharacterName.DEE_JAY
        assert char_to_enum("KIM") == CharacterName.KIMBERLY
        assert char_to_enum("J.P.") == CharacterName.JP

    def test_unknown_returns_none(self):
        """未知の文字列は None を返すこと。"""
        assert char_to_enum("UNKNOWN") is None
        assert char_to_enum("") is None
        assert char_to_enum("SFIII") is None

    def test_all_characters_by_value(self):
        """全キャラクターが enum値（小文字）で変換できること。"""
        for char in CharacterName:
            result = char_to_enum(char.value)
            assert result == char, f"char_to_enum({char.value!r}) failed"

    def test_all_characters_by_name(self):
        """全キャラクターが enum名（大文字）で変換できること。"""
        for char in CharacterName:
            result = char_to_enum(char.name)
            assert result == char, f"char_to_enum({char.name!r}) failed"


class TestCharacterMaxHP:
    """CHARACTER_MAX_HP の完全性・正確性テスト。"""

    def test_all_characters_have_max_hp(self):
        """全キャラクターに最大HP値が定義されていること。"""
        for char in CharacterName:
            assert char in CHARACTER_MAX_HP, f"{char} が CHARACTER_MAX_HP に未定義"

    def test_max_hp_values_are_positive(self):
        """全キャラクターの最大HPが正の値であること。"""
        for char, hp in CHARACTER_MAX_HP.items():
            assert hp > 0, f"{char} の最大HP が 0 以下"

    def test_max_hp_within_reasonable_range(self):
        """最大HPが SF6 の実際の範囲（8000〜12000）内であること。"""
        for char, hp in CHARACTER_MAX_HP.items():
            assert 8000 <= hp <= 12000, f"{char} の最大HP={hp} が想定範囲外"

    def test_known_hp_values(self):
        """既知の最大HP値が正確であること（公式値）。"""
        assert CHARACTER_MAX_HP[CharacterName.ZANGIEF] == 11000  # 最大
        assert CHARACTER_MAX_HP[CharacterName.MARISA] == 11000   # 最大
        assert CHARACTER_MAX_HP[CharacterName.DHALSIM] == 9000   # 最小
        assert CHARACTER_MAX_HP[CharacterName.AKUMA] == 9000     # 最小
        assert CHARACTER_MAX_HP[CharacterName.RYU] == 10000      # 基準値

    def test_no_extra_characters(self):
        """CHARACTER_MAX_HP に未定義キャラクターが含まれないこと。"""
        valid_chars = set(CharacterName)
        for char in CHARACTER_MAX_HP:
            assert char in valid_chars


class TestCharacterLabels:
    """CHARACTER_LABELS の完全性テスト。"""

    def test_all_characters_have_label(self):
        """全キャラクターに日本語ラベルが定義されていること。"""
        for char in CharacterName:
            assert char in CHARACTER_LABELS, f"{char} が CHARACTER_LABELS に未定義"

    def test_labels_are_non_empty(self):
        """全ラベルが空でないこと。"""
        for char, label in CHARACTER_LABELS.items():
            assert label, f"{char} のラベルが空"

    def test_no_extra_characters(self):
        """CHARACTER_LABELS に未定義キャラクターが含まれないこと。"""
        valid_chars = set(CharacterName)
        for char in CHARACTER_LABELS:
            assert char in valid_chars

    def test_character_count_matches(self):
        """CHARACTER_LABELS と CHARACTER_MAX_HP のキャラクター数が一致すること。"""
        assert len(CHARACTER_LABELS) == len(CHARACTER_MAX_HP) == len(list(CharacterName))
