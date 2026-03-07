# スナップショットタブのバックアップ（app.py から切り出し）
# 復元する場合は app.py の tab_snap ブロックに貼り付ける

# tabs の定義:
# tab_live, tab_vod, tab_snap = st.tabs(["🔴 ライブ監視", "📼 VOD解析", "📷 スナップショット"])

"""
with tab_snap:
    st.subheader("スナップショットモード")
    st.caption("ボタンを押したタイミングで1回だけ解析します。")

    with st.form("snap_form"):
        snap_url = st.text_input(
            "配信URL または 動画URL",
            value="https://www.twitch.tv/your_channel",
        )
        auto_detect_snap = st.toggle("相手キャラを自動検出", value=True, key="snap_auto_detect")
        opp_idx_snap = None
        if not auto_detect_snap:
            opp_idx_snap = st.selectbox(
                "相手キャラクター",
                range(len(CHARACTER_OPTIONS)),
                format_func=lambda i: CHARACTER_DISPLAY[i],
                index=1,
                key="snap_opp_char",
            )
        submitted = st.form_submit_button("解析する", use_container_width=True, type="primary")

    if submitted:
        if not snap_url.strip():
            st.error("URLを入力してください")
            st.stop()

        with st.spinner("解析中..."):
            try:
                p1_char, p2_char = resolve_characters(snap_url, auto_detect_snap, opp_idx_snap)
                game_state = extract_game_state(snap_url, p1_char, p2_char)
                punish = detect_punish_opportunity(game_state.player1, game_state.player2)
                lethal = calculate_lethal(game_state.player1, game_state.player2)
            except Exception as e:
                st.error(f"解析エラー: {e}")
                st.stop()

        st.info("配信URLを検出しました。" if is_stream_url(snap_url) else "動画URLを解析しました。")
        st.divider()
        st.subheader("解析結果")
        st.caption(f"自分: {CHARACTER_LABELS[p1_char]} vs 相手: {CHARACTER_LABELS[p2_char]}")

        col_p1, col_p2 = st.columns(2)
        with col_p1:
            player_card("自分（P1）", game_state.player1)
        with col_p2:
            player_card("相手（P2）", game_state.player2)

        st.divider()
        punish_lethal_columns(punish, lethal)
"""
