"""SF6 AI動画解析システム - Streamlit UI。

モード:
  - ライブ監視: 配信を一定間隔で自動解析し、イベントを蓄積表示する
  - VOD解析:   動画URLを解析し、試合サマリーを生成する
  - スナップショット: 任意のタイミングで1回だけ解析する（従来モード）
"""

import datetime

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from logic_engine.lethal_calculator import calculate_lethal
from logic_engine.match_monitor import (
    MatchLog,
    build_coaching_report,
    build_counter_strategy_report,
    build_pro_coaching_report,
    build_pro_comparison_report,
    build_stats_report,
    build_strategic_report,
    build_vod_summary,
    detect_events,
    user_stats,
)
from logic_engine.pro_benchmarks import get_all_players, get_benchmark, composite_benchmark, PlayerBenchmark
from logic_engine.punish_detector import detect_punish_opportunity
from schemas import CHARACTER_LABELS, CHARACTER_MAX_HP, CharacterName
from vision_extractor import detect_characters_from_url, extract_game_state

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

CHARACTER_OPTIONS = list(CHARACTER_LABELS.keys())
CHARACTER_DISPLAY = [CHARACTER_LABELS[c] for c in CHARACTER_OPTIONS]
MAX_HP = CHARACTER_MAX_HP  # 後方互換エイリアス

KEY_MY_CHAR       = "my_character"
KEY_AUTO_DETECT   = "auto_detect"
KEY_MATCH_LOG     = "match_log"
KEY_PREV_STATE    = "prev_game_state"
KEY_MONITORING    = "monitoring_active"
KEY_VIDEO_URL     = "video_url"


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def hp_bar(current: int, maximum: int, label: str) -> None:
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
    max_hp = MAX_HP.get(state.character, 10000)
    sa_pips = "".join(["■" if i < state.sa_stock else "□" for i in range(3)])
    burnout = state.drive_gauge == 0
    burnout_badge = (
        ' <span style="background:#e74c3c;color:#fff;font-size:0.75rem;'
        'padding:1px 6px;border-radius:4px;font-weight:bold;">BURNOUT</span>'
        if burnout else ""
    )
    st.markdown(
        f"**{title}：{CHARACTER_LABELS[state.character]}**{burnout_badge}",
        unsafe_allow_html=True,
    )
    hp_bar(state.hp, max_hp, "体力")
    drive_color = "#e74c3c" if burnout else "#f1c40f"
    gauge_bar(state.drive_gauge, 10000, "ドライブゲージ", drive_color)
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


def punish_lethal_columns(punish, lethal) -> None:  # type: ignore[no-untyped-def]
    col_p, col_l = st.columns(2)
    with col_p:
        st.subheader("確定反撃判定")
        if punish.is_punishable:
            st.success(f"確定反撃あり（{punish.frame_advantage}F 有利）")
            st.caption(punish.description)
            for i, move in enumerate(punish.punish_moves[:6], start=1):
                sa_badge = f" `SA{move.sa_cost}`" if move.sa_cost > 0 else ""
                dr_badge = " `DR`" if move.drive_cost > 0 else ""
                st.markdown(
                    f"**{i}.** {move.move_name}{sa_badge}{dr_badge} "
                    f"— 発生 **{move.startup}F** / ダメージ **{move.damage:,}**"
                )
            if len(punish.punish_moves) > 6:
                st.caption(f"他 {len(punish.punish_moves) - 6} 技が確定")
        else:
            st.info("現在は確定反撃チャンスがありません")
            st.caption(punish.description)

    with col_l:
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


def resolve_characters(
    video_url: str, auto_detect: bool, opponent_idx
) -> tuple[CharacterName, CharacterName]:
    my_char = CharacterName(st.session_state[KEY_MY_CHAR])
    if auto_detect:
        _, p2 = _cached_detect_characters(video_url)
        if p2 == my_char:
            p2 = next(c for c in CHARACTER_OPTIONS if c != my_char)
    else:
        p2 = CHARACTER_OPTIONS[opponent_idx]
    return my_char, p2


def _render_coaching(advices: list) -> None:
    if not advices:
        st.info("データが不足しています。監視時間を伸ばしてください。")
        return
    for adv in advices:
        if adv["level"] == "good":
            st.success(f"**{adv['title']}**  \n{adv['body']}")
        elif adv["level"] == "warn":
            st.warning(f"**{adv['title']}**  \n{adv['body']}")
        else:
            st.info(f"**{adv['title']}**  \n{adv['body']}")


_PRO_PLAYER_OPTIONS = ["プロ6名平均"] + get_all_players()
_PRO_PLAYER_KEYS    = ["composite"]   + get_all_players()


@st.cache_data(ttl=300)
def _cached_detect_characters(video_url: str) -> tuple[CharacterName, CharacterName]:
    """キャラクター検出結果を300秒キャッシュ（同一URLで毎サイクル再検出しない）。"""
    return detect_characters_from_url(video_url)


@st.cache_data
def _cached_composite_benchmark(character: str) -> PlayerBenchmark:
    """composite_benchmark の計算結果をキャッシュ（セッション中変化しない）。"""
    return composite_benchmark(character)


def report_ui(log: MatchLog, report_type: str, tab_key: str = "") -> None:
    """レポートタイプに応じてサマリーを描画する。"""
    if report_type == "統計分析":
        stats = build_stats_report(log)
        cols = st.columns(4)
        items = list(stats.items())
        for i, (label, val) in enumerate(items):
            cols[i % 4].metric(label, val)

    elif report_type == "コーチング":
        _render_coaching(build_coaching_report(log))

    elif report_type == "プロ向けコーチング":
        st.caption("バーンアウト・ドライブゲージ管理・ストリーク分析などの詳細アドバイス")
        _render_coaching(build_pro_coaching_report(log))

    elif report_type == "戦略レポート":
        st.caption("チャンス変換率・因果連鎖・優先課題を分析します（3分以上のデータ推奨）")
        _render_coaching(build_strategic_report(log))

    elif report_type == "プロ比較":
        my_char = CharacterName(st.session_state[KEY_MY_CHAR])
        st.caption(f"登録済みプロ（{CHARACTER_LABELS[my_char]}）のベンチマークと自分の指標を比較します")
        sel_idx = st.selectbox(
            "比較対象プレイヤー",
            range(len(_PRO_PLAYER_OPTIONS)),
            format_func=lambda i: _PRO_PLAYER_OPTIONS[i],
            key=f"pro_compare_sel_{tab_key}",
        )
        player_key = _PRO_PLAYER_KEYS[sel_idx]
        # 選手プロフィールバッジ
        if player_key != "composite":
            bench = get_benchmark(player_key)
            if bench:
                verified = "公開情報・大会実績で検証済み" if bench.verified else "推定値"
                dr_label = {"high": "節約型", "med": "バランス型", "low": "積極使用型"}.get(bench.dr_economy, "")
                st.markdown(
                    f"<div style='background:#1a2a3a;border-left:4px solid #3498db;"
                    f"padding:8px 14px;border-radius:0 6px 6px 0;margin-bottom:12px;'>"
                    f"<b>{bench.display_name}</b> — {bench.style_label} &nbsp;"
                    f"<span style='font-size:0.8rem;color:#aaa;'>DR: {dr_label} / データ: {verified}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        else:
            bench = _cached_composite_benchmark(my_char.value)
        # 視覚的指標比較
        if bench and len(log.events) >= 3:
            u = user_stats(log)
            c1, c2, c3, c4 = st.columns(4)
            def _delta(user_v, bench_v, lower_is_better=False):
                if user_v is None:
                    return None
                d = user_v - bench_v
                return f"{d:+.1f}pt" if not lower_is_better else f"{-d:+.1f}pt"
            c1.metric("自分BO率", f"{u['burnout_rate']:.1f}%",
                      delta=_delta(u['burnout_rate'], bench.burnout_rate_pct, lower_is_better=True),
                      delta_color="normal", help=f"プロ水準: {bench.burnout_rate_pct:.0f}%（低いほど良い）")
            c2.metric("相手BO誘導率", f"{u['opp_burnout_rate']:.1f}%",
                      delta=_delta(u['opp_burnout_rate'], bench.opp_burnout_pct),
                      delta_color="normal", help=f"プロ水準: {bench.opp_burnout_pct:.0f}%（高いほど良い）")
            c3.metric("与ダメ率", f"{u['deal_ratio']:.1f}%",
                      delta=_delta(u['deal_ratio'], bench.deal_ratio_pct),
                      delta_color="normal", help=f"プロ水準: {bench.deal_ratio_pct:.0f}%")
            punish_label = f"{u['punish_conv']:.0f}%" if u['punish_conv'] is not None else "—"
            c4.metric("確定反撃変換率", punish_label,
                      delta=_delta(u['punish_conv'], bench.punish_conv_pct) if u['punish_conv'] is not None else None,
                      delta_color="normal", help=f"プロ水準: {bench.punish_conv_pct:.0f}%（高いほど良い）")
            st.divider()
        _render_coaching(build_pro_comparison_report(log, player_key=player_key, character=my_char.value))

    else:  # イベントログ（デフォルト）
        event_log_ui(log, n=len(log.events) if len(log.events) <= 20 else 20)


def event_log_ui(log: MatchLog, n: int = 10) -> None:
    recent = log.recent(n)
    if not recent:
        st.caption("まだイベントはありません")
        return
    color_map = {
        "punish_opportunity":   "#27ae60",
        "lethal_chance":        "#c0392b",
        "took_damage":          "#e67e22",
        "opponent_took_damage": "#2980b9",
        "low_hp":               "#8e44ad",
        "burnout":              "#e74c3c",
        "burnout_opponent":     "#16a085",
    }
    for ev in reversed(recent):
        color = color_map.get(ev.event_type.value, "#555")
        detail_html = (
            f"  <span style='color:#aaa;font-size:0.8rem;'>— {ev.detail}</span>"
            if ev.detail else ""
        )
        st.markdown(
            f'<div style="border-left:4px solid {color};padding:6px 12px;'
            f'margin-bottom:6px;background:#1a1d24;border-radius:0 4px 4px 0;">'
            f'<span style="font-size:0.8rem;color:#aaa;">{ev.time_str}</span> '
            f'{ev.icon} <b>{ev.description}</b>{detail_html}</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# session_state 初期化
# ---------------------------------------------------------------------------

st.set_page_config(page_title="SF6 AI動画解析システム", layout="wide")

for k, v in {
    KEY_MY_CHAR:     CharacterName.JP.value,
    KEY_AUTO_DETECT: True,
    KEY_MATCH_LOG:   None,
    KEY_PREV_STATE:  None,
    KEY_MONITORING:  False,
    KEY_VIDEO_URL:   "",
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ---------------------------------------------------------------------------
# サイドバー
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("設定")
    my_char_idx = CHARACTER_OPTIONS.index(CharacterName(st.session_state[KEY_MY_CHAR]))
    new_idx = st.selectbox(
        "自分のキャラクター",
        range(len(CHARACTER_OPTIONS)),
        format_func=lambda i: CHARACTER_DISPLAY[i],
        index=my_char_idx,
        key="sidebar_my_char",
    )
    if CHARACTER_OPTIONS[new_idx].value != st.session_state[KEY_MY_CHAR]:
        st.session_state[KEY_MY_CHAR] = CHARACTER_OPTIONS[new_idx].value
        st.success(f"{CHARACTER_DISPLAY[new_idx]} を自分のキャラとして記憶しました")

    st.divider()
    st.info(
        "**[開発中] CV解析について**\n\n"
        "現在、体力・ゲージの読み取りはフレームデータ参照モード（モック）で動作しています。"
        "実配信映像からの自動読み取り（Computer Vision）は開発中です。",
        icon="🔬",
    )
    st.divider()
    st.markdown("**対応配信プラットフォーム**")
    st.markdown("- YouTube\n- Twitch")
    st.caption("CV実装: yt-dlp / streamlink / OpenCV")

# ---------------------------------------------------------------------------
# メインUI
# ---------------------------------------------------------------------------

st.title("SF6 AI動画解析システム")
my_char = CharacterName(st.session_state[KEY_MY_CHAR])
st.caption(f"自分のキャラ: **{CHARACTER_LABELS[my_char]}** （サイドバーから変更できます）")

tab_live, tab_vod, tab_vs = st.tabs(["🔴 ライブ監視", "📼 VOD解析", "🆚 対戦相手分析"])


# ===========================================================================
# ライブ監視タブ
# ===========================================================================

with tab_live:
    st.subheader("ライブ監視モード")
    st.caption("配信を定期的に自動解析して、イベントをリアルタイムで記録します。")

    col_url, col_interval = st.columns([3, 1])
    with col_url:
        live_url = st.text_input(
            "配信URL",
            value=st.session_state[KEY_VIDEO_URL] or "https://www.twitch.tv/your_channel",
            key="live_url_input",
        )
    with col_interval:
        refresh_sec = st.selectbox("更新間隔", [10, 20, 30, 60], index=1, key="live_refresh_sec",
                                   format_func=lambda s: f"{s}秒")

    auto_detect_live = st.toggle("相手キャラを自動検出", value=True, key="live_auto_detect")
    opp_idx_live = None
    if not auto_detect_live:
        opp_idx_live = st.selectbox(
            "相手キャラクター",
            range(len(CHARACTER_OPTIONS)),
            format_func=lambda i: CHARACTER_DISPLAY[i],
            index=1,
            key="live_opp_char",
        )

    col_start, col_stop, col_reset = st.columns(3)
    with col_start:
        if st.button("監視開始", type="primary", use_container_width=True, key="live_start"):
            st.session_state[KEY_MONITORING] = True
            st.session_state[KEY_VIDEO_URL]  = live_url
            st.session_state[KEY_MATCH_LOG]  = MatchLog()
            st.session_state[KEY_PREV_STATE] = None
    with col_stop:
        if st.button("監視停止", use_container_width=True, key="live_stop"):
            st.session_state[KEY_MONITORING] = False
    with col_reset:
        if st.button("ログリセット", use_container_width=True, key="live_reset"):
            st.session_state[KEY_MATCH_LOG]  = MatchLog()
            st.session_state[KEY_PREV_STATE] = None

    if st.session_state[KEY_MONITORING]:
        st_autorefresh(interval=refresh_sec * 1000, key="live_autorefresh")
        st.success(f"監視中... {refresh_sec}秒ごとに自動更新")

        with st.spinner("解析中..."):
            try:
                p1_char, p2_char = resolve_characters(
                    st.session_state[KEY_VIDEO_URL], auto_detect_live, opp_idx_live
                )
                game_state = extract_game_state(st.session_state[KEY_VIDEO_URL], p1_char, p2_char)
                punish = detect_punish_opportunity(game_state.player1, game_state.player2)
                lethal = calculate_lethal(game_state.player1, game_state.player2)

                log: MatchLog = st.session_state[KEY_MATCH_LOG]
                for ev in detect_events(
                    game_state, punish, lethal,
                    st.session_state[KEY_PREV_STATE],
                    MAX_HP.get(p1_char, 10000),
                ):
                    log.append(ev)
                st.session_state[KEY_PREV_STATE] = game_state
                analysis_ok = True

            except Exception as e:
                st.warning(f"解析エラー（次回リトライ）: {e}")
                analysis_ok = False

        if analysis_ok:
            log = st.session_state[KEY_MATCH_LOG]
            st.markdown(
                f"**最終更新: {datetime.datetime.now().strftime('%H:%M:%S')}**"
                f"  |  監視時間: {log.elapsed_str}"
            )

            col_p1, col_p2 = st.columns(2)
            with col_p1:
                player_card("自分（P1）", game_state.player1)
            with col_p2:
                player_card("相手（P2）", game_state.player2)

            st.divider()
            punish_lethal_columns(punish, lethal)

            st.divider()
            st.subheader("レポート")
            report_type_live = st.radio(
                "レポートタイプ",
                ["イベントログ", "統計分析", "コーチング", "プロ向けコーチング", "戦略レポート", "プロ比較"],
                horizontal=True,
                key="live_report_type",
            )
            report_ui(log, report_type_live, tab_key="live")

    else:
        st.info("「監視開始」を押すと自動で配信を解析し始めます。")


# ===========================================================================
# VOD解析タブ
# ===========================================================================

with tab_vod:
    st.subheader("VOD解析モード")
    st.caption("録画済みの動画URLを解析して、サマリーレポートを出力します。")

    vod_url = st.text_input(
        "動画URL",
        placeholder="https://youtube.com/watch?v=...",
        key="vod_url_input",
    )
    auto_detect_vod = st.toggle("相手キャラを自動検出", value=True, key="vod_auto_detect")
    opp_idx_vod = None
    if not auto_detect_vod:
        opp_idx_vod = st.selectbox(
            "相手キャラクター",
            range(len(CHARACTER_OPTIONS)),
            format_func=lambda i: CHARACTER_DISPLAY[i],
            index=1,
            key="vod_opp_char",
        )

    if st.button("VOD解析を実行", type="primary", use_container_width=True, key="vod_run"):
        if not vod_url.strip():
            st.error("URLを入力してください")
            st.stop()

        vod_log = MatchLog()
        with st.spinner("解析中..."):
            try:
                p1_char, p2_char = resolve_characters(vod_url, auto_detect_vod, opp_idx_vod)
                game_state = extract_game_state(vod_url, p1_char, p2_char)
                punish = detect_punish_opportunity(game_state.player1, game_state.player2)
                lethal = calculate_lethal(game_state.player1, game_state.player2)
                for ev in detect_events(game_state, punish, lethal, None, MAX_HP.get(p1_char, 10000)):
                    vod_log.append(ev)
            except Exception as e:
                st.error(f"解析エラー: {e}")
                st.stop()

        st.divider()
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            player_card("自分（P1）", game_state.player1)
        with col_p2:
            player_card("相手（P2）", game_state.player2)

        st.divider()
        punish_lethal_columns(punish, lethal)

        st.divider()
        st.subheader("レポート")
        report_type_vod = st.radio(
            "レポートタイプ",
            ["イベントログ", "統計分析", "コーチング", "プロ向けコーチング", "戦略レポート", "プロ比較"],
            horizontal=True,
            key="vod_report_type",
        )
        report_ui(vod_log, report_type_vod, tab_key="vod")


# ===========================================================================
# 対戦相手分析タブ
# ===========================================================================

with tab_vs:
    st.subheader("対戦相手分析モード")
    st.caption(
        "自分と相手それぞれのVOD URLを入力して解析し、"
        "「この相手にどう勝つか」の対策アドバイスを生成します。"
    )

    col_my, col_opp = st.columns(2)
    with col_my:
        st.markdown("**自分のVOD**")
        vs_my_url = st.text_input(
            "自分のVOD URL",
            placeholder="https://youtube.com/watch?v=... （自分が映っている動画）",
            key="vs_my_url",
        )
        vs_my_char_idx = st.selectbox(
            "自分のキャラ",
            range(len(CHARACTER_OPTIONS)),
            format_func=lambda i: CHARACTER_DISPLAY[i],
            index=CHARACTER_OPTIONS.index(CharacterName(st.session_state[KEY_MY_CHAR])),
            key="vs_my_char",
        )
    with col_opp:
        st.markdown("**相手のVOD**")
        vs_opp_url = st.text_input(
            "相手のVOD URL",
            placeholder="https://youtube.com/watch?v=... （相手が映っている動画）",
            key="vs_opp_url",
        )
        vs_opp_char_idx = st.selectbox(
            "相手のキャラ",
            range(len(CHARACTER_OPTIONS)),
            format_func=lambda i: CHARACTER_DISPLAY[i],
            index=1,
            key="vs_opp_char",
        )

    st.caption(
        "💡 相手のVODは「相手が自分視点（P1）で映っている動画」を使うと精度が上がります。"
        "相手の配信アーカイブや、相手視点リプレイが理想です。"
    )

    if st.button("対戦相手を分析する", type="primary", use_container_width=True, key="vs_run"):
        if not vs_my_url.strip() or not vs_opp_url.strip():
            st.error("自分・相手、両方のURLを入力してください")
            st.stop()

        my_char_vs  = CHARACTER_OPTIONS[vs_my_char_idx]
        opp_char_vs = CHARACTER_OPTIONS[vs_opp_char_idx]

        col_spin_my, col_spin_opp = st.columns(2)

        with col_spin_my:
            with st.spinner("自分のVODを解析中..."):
                try:
                    my_gs = extract_game_state(vs_my_url, my_char_vs, opp_char_vs)
                    my_punish = detect_punish_opportunity(my_gs.player1, my_gs.player2)
                    my_lethal = calculate_lethal(my_gs.player1, my_gs.player2)
                    my_log = MatchLog()
                    for ev in detect_events(my_gs, my_punish, my_lethal, None, MAX_HP.get(my_char_vs, 10000)):
                        my_log.append(ev)
                    my_ok = True
                except Exception as e:
                    st.error(f"自分VOD解析エラー: {e}")
                    my_ok = False

        with col_spin_opp:
            with st.spinner("相手のVODを解析中..."):
                try:
                    # 相手VODでは相手がP1として解析する
                    opp_gs = extract_game_state(vs_opp_url, opp_char_vs, my_char_vs)
                    opp_punish = detect_punish_opportunity(opp_gs.player1, opp_gs.player2)
                    opp_lethal = calculate_lethal(opp_gs.player1, opp_gs.player2)
                    opp_log = MatchLog()
                    for ev in detect_events(opp_gs, opp_punish, opp_lethal, None, MAX_HP.get(opp_char_vs, 10000)):
                        opp_log.append(ev)
                    opp_ok = True
                except Exception as e:
                    st.error(f"相手VOD解析エラー: {e}")
                    opp_ok = False

        if not (my_ok and opp_ok):
            st.stop()

        st.divider()
        st.subheader("解析結果")
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            st.markdown("**自分**")
            player_card("自分（P1）", my_gs.player1)
        with col_p2:
            st.markdown("**相手**")
            player_card("相手（P1）", opp_gs.player1)

        st.divider()
        st.subheader("対戦相手分析 → 対策アドバイス")
        _render_coaching(build_counter_strategy_report(opp_log))

        st.divider()
        with st.expander("自分のVOD分析レポートを見る", expanded=False):
            my_report_type = st.radio(
                "レポートタイプ（自分）",
                ["統計分析", "プロ向けコーチング", "戦略レポート", "プロ比較"],
                horizontal=True,
                key="vs_my_report_type",
            )
            report_ui(my_log, my_report_type, tab_key="vs_my")
