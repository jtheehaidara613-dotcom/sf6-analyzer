"""SF6 AI動画解析システム - Streamlit UI。

非エンジニア向けの操作画面です。
配信URLを貼り付けて「解析」を押すだけで確定反撃・リーサル判定が確認できます。
"""

import streamlit as st

from logic_engine.lethal_calculator import calculate_lethal
from logic_engine.punish_detector import detect_punish_opportunity
from schemas import CharacterName
from vision_extractor import detect_characters_from_url, extract_game_state, is_stream_url

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

CHARACTER_LABELS: dict[CharacterName, str] = {
    CharacterName.RYU: "リュウ",
    CharacterName.CHUN_LI: "春麗",
    CharacterName.JAMIE: "ジェイミー",
    CharacterName.LUKE: "ルーク",
    CharacterName.KEN: "ケン",
    CharacterName.CAMMY: "キャミィ",
    CharacterName.JP: "JP",
}

CHARACTER_OPTIONS = list(CHARACTER_LABELS.keys())
CHARACTER_DISPLAY = [CHARACTER_LABELS[c] for c in CHARACTER_OPTIONS]

MAX_HP: dict[CharacterName, int] = {
    CharacterName.RYU: 10000,
    CharacterName.CHUN_LI: 9500,
    CharacterName.JAMIE: 10500,
    CharacterName.LUKE: 10000,
    CharacterName.KEN: 10000,
    CharacterName.CAMMY: 9500,
    CharacterName.JP: 10000,
}

# session_state のキー
KEY_MY_CHAR = "my_character"
KEY_AUTO_DETECT = "auto_detect"


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def hp_bar(current: int, maximum: int, label: str) -> None:
    """体力バーを描画する。"""
    ratio = max(0.0, current / maximum)
    color = "#e74c3c" if ratio < 0.3 else "#f39c12" if ratio < 0.6 else "#2ecc71"
    st.markdown(
        f"""
        <div style="margin-bottom:4px;">
          <span style="font-size:0.85rem;">{label}</span>
          <span style="float:right;font-size:0.85rem;font-weight:bold;">{current:,} / {maximum:,}</span>
        </div>
        <div style="background:#444;border-radius:6px;height:18px;width:100%;">
          <div style="background:{color};border-radius:6px;height:18px;width:{ratio*100:.1f}%;"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def gauge_bar(current: int, maximum: int, label: str, color: str = "#3498db") -> None:
    """ゲージバーを描画する。"""
    ratio = max(0.0, current / maximum)
    st.markdown(
        f"""
        <div style="margin-bottom:4px;">
          <span style="font-size:0.80rem;color:#aaa;">{label}</span>
          <span style="float:right;font-size:0.80rem;color:#aaa;">{int(ratio*100)}%</span>
        </div>
        <div style="background:#333;border-radius:4px;height:10px;width:100%;margin-bottom:8px;">
          <div style="background:{color};border-radius:4px;height:10px;width:{ratio*100:.1f}%;"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def player_card(title: str, state) -> None:  # type: ignore[no-untyped-def]
    """プレイヤー状態カードを描画する。"""
    max_hp = MAX_HP.get(state.character, 10000)
    sa_pips = "".join(["■" if i < state.sa_stock else "□" for i in range(3)])
    st.markdown(f"**{title}：{CHARACTER_LABELS[state.character]}**")
    hp_bar(state.hp, max_hp, "体力")
    gauge_bar(state.drive_gauge, 10000, "ドライブゲージ", "#f1c40f")
    st.markdown(
        f'<div style="font-size:0.85rem;margin-bottom:8px;">SAゲージ: '
        f'<span style="letter-spacing:4px;font-size:1.1rem;">{sa_pips}</span> '
        f'({state.sa_stock}/3)</div>',
        unsafe_allow_html=True,
    )
    frame_color = {
        "neutral": "#2ecc71", "recovery": "#e74c3c",
        "hitstun": "#e67e22", "blockstun": "#9b59b6", "startup": "#3498db",
    }.get(state.frame_state.value, "#aaa")
    st.markdown(
        f'<div style="font-size:0.85rem;">状態: '
        f'<span style="background:{frame_color};color:#fff;padding:2px 8px;border-radius:4px;">'
        f'{state.frame_state.value.upper()}</span></div>',
        unsafe_allow_html=True,
    )
    if state.remaining_recovery_frames > 0:
        st.markdown(
            f'<div style="font-size:0.80rem;color:#e74c3c;margin-top:4px;">'
            f'残り硬直: {state.remaining_recovery_frames}F</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# ページ設定・初期化
# ---------------------------------------------------------------------------

st.set_page_config(page_title="SF6 AI動画解析システム", layout="wide")

# session_state の初期値
if KEY_MY_CHAR not in st.session_state:
    st.session_state[KEY_MY_CHAR] = CharacterName.JP.value
if KEY_AUTO_DETECT not in st.session_state:
    st.session_state[KEY_AUTO_DETECT] = True

# ---------------------------------------------------------------------------
# サイドバー：設定
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("設定")

    my_char_idx = CHARACTER_OPTIONS.index(
        CharacterName(st.session_state[KEY_MY_CHAR])
    )
    new_my_char_idx = st.selectbox(
        "自分のキャラクター",
        range(len(CHARACTER_OPTIONS)),
        format_func=lambda i: CHARACTER_DISPLAY[i],
        index=my_char_idx,
        key="sidebar_my_char",
    )
    if CHARACTER_OPTIONS[new_my_char_idx].value != st.session_state[KEY_MY_CHAR]:
        st.session_state[KEY_MY_CHAR] = CHARACTER_OPTIONS[new_my_char_idx].value
        st.success(f"{CHARACTER_DISPLAY[new_my_char_idx]} を自分のキャラとして記憶しました")

    st.divider()
    st.markdown("**対応配信プラットフォーム**")
    st.markdown("- YouTube\n- Twitch")
    st.caption("現在はモックのため実際の映像解析は行いません。CV実装後に対応予定です。")

# ---------------------------------------------------------------------------
# メインUI
# ---------------------------------------------------------------------------

st.title("SF6 AI動画解析システム")

my_char = CharacterName(st.session_state[KEY_MY_CHAR])
st.caption(f"自分のキャラ: **{CHARACTER_LABELS[my_char]}** （サイドバーから変更できます）")

st.divider()

with st.form("analyze_form"):
    st.subheader("解析する配信 / 動画を入力")

    video_url = st.text_input(
        "配信URL または 動画URL",
        value="https://www.twitch.tv/your_channel",
        placeholder="https://www.twitch.tv/... または https://youtube.com/...",
        help="YouTube・Twitch・TwitCastingなどのURLを貼り付けてください",
    )

    auto_detect = st.toggle(
        "相手キャラクターを自動検出する",
        value=st.session_state[KEY_AUTO_DETECT],
    )
    st.session_state[KEY_AUTO_DETECT] = auto_detect

    opponent_idx = None
    if not auto_detect:
        opponent_idx = st.selectbox(
            "相手キャラクター",
            range(len(CHARACTER_OPTIONS)),
            format_func=lambda i: CHARACTER_DISPLAY[i],
            index=1,
        )
        st.caption("自動検出をOFFにして手動で選択しています")
    else:
        st.caption("相手キャラクターは動画から自動識別します（現在はモック）")

    submitted = st.form_submit_button("解析する", use_container_width=True, type="primary")

# ---------------------------------------------------------------------------
# 解析処理と結果表示
# ---------------------------------------------------------------------------

if submitted:
    if not video_url.strip():
        st.error("URLを入力してください")
        st.stop()

    # 配信URLかどうかを表示
    if is_stream_url(video_url):
        st.info("配信URLを検出しました。最新フレームを取得して解析します。")
    else:
        st.info("動画URLを解析します。")

    with st.spinner("解析中..."):
        try:
            # 自分のキャラは設定済み、相手は自動検出 or 手動
            character_p1 = my_char
            if auto_detect:
                _, character_p2 = detect_characters_from_url(video_url)
                # P1と同キャラになった場合は別キャラをデフォルトに
                if character_p2 == character_p1:
                    character_p2 = next(c for c in CHARACTER_OPTIONS if c != character_p1)
            else:
                character_p2 = CHARACTER_OPTIONS[opponent_idx]  # type: ignore[index]

            game_state = extract_game_state(video_url, character_p1, character_p2)
            punish = detect_punish_opportunity(game_state.player1, game_state.player2)
            lethal = calculate_lethal(game_state.player1, game_state.player2)

        except Exception as e:
            st.error(f"解析中にエラーが発生しました: {e}")
            st.stop()

    st.divider()
    st.subheader("解析結果")
    st.caption(
        f"自分: {CHARACTER_LABELS[character_p1]} vs "
        f"相手: {CHARACTER_LABELS[character_p2]}"
    )

    # プレイヤー状態
    col_p1, col_p2 = st.columns(2)
    with col_p1:
        player_card("自分（P1）", game_state.player1)
    with col_p2:
        player_card("相手（P2）", game_state.player2)

    st.divider()

    # 確定反撃 / リーサル
    col_punish, col_lethal = st.columns(2)

    with col_punish:
        st.subheader("確定反撃判定")
        if punish.is_punishable:
            st.success(f"確定反撃あり（{punish.frame_advantage}F 有利）")
            st.caption(punish.description)
            for i, move in enumerate(punish.punish_moves[:6], start=1):
                sa_badge = f" [SA{move.sa_cost}]" if move.sa_cost > 0 else ""
                st.markdown(
                    f"**{i}.** {move.move_name}{sa_badge} "
                    f"— 発生 **{move.startup}F** / ダメージ **{move.damage:,}**"
                )
            if len(punish.punish_moves) > 6:
                st.caption(f"他 {len(punish.punish_moves) - 6} 技が確定")
        else:
            st.info("現在は確定反撃チャンスがありません")
            st.caption(punish.description)

    with col_lethal:
        st.subheader("リーサル判定")
        if lethal.is_lethal:
            st.error(
                f"リーサル確定  {lethal.estimated_max_damage:,} ダメージ > "
                f"相手HP {lethal.target_hp:,}"
            )
        else:
            shortage = lethal.target_hp - lethal.estimated_max_damage
            st.info(f"リーサル不可（あと {shortage:,} 足りません）")
        st.caption(lethal.description)

        if lethal.recommended_combo:
            st.markdown("**推奨コンボ**")
            total = 0
            for step in lethal.recommended_combo:
                total += step.scaled_damage
                st.markdown(
                    f"　{step.hit_count}hit. {step.move_name} "
                    f"→ **{step.scaled_damage:,}** ダメージ "
                    f"（補正 {int(step.scaling_rate * 100)}%）"
                )
            st.markdown(f"　**合計: {total:,} ダメージ**")
            if lethal.sa_cost > 0:
                st.caption(f"SAゲージ {lethal.sa_cost} 本を消費します")
