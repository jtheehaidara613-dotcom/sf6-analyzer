"""YouTube VOD 検索モジュール。

yt-dlp を使ってプレイヤーのSF6 VODを検索する機能を提供する。
collect_pro_data.py / batch_collect.py から利用する。
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# HUDレイアウトが非標準の動画を除外するキーワード
_SKIP_TITLE_KEYWORDS = (
    "best replays",
    "high level gameplay",
    "▰",
    "💥",
    "🔥sf6",
    "🔥 sf6",
    " guide",
    "tutorial",
    "combo guide",
    "how to play",
    "character guide",
    "reacts to",
    "reaction",
    "gameplay trailer",
    "reveal trailer",
    "trailer",
)


def _sanitize_name(name: str) -> str:
    """検索クエリ用に特殊文字・絵文字を除去する。"""
    name = re.sub(r"[^\w\s\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def search_youtube_vod(player_name: str, char: str, max_results: int = 5) -> str | None:
    """yt-dlp の ytsearch でプレイヤーのSF6 VODを検索して最初のURLを返す。

    特殊文字・絵文字は自動除去してから検索する。
    非標準HUDのオーバーレイ系チャンネルは除外する。

    Args:
        player_name: プレイヤー名
        char: キャラクター名（例: "JP", "RYU"）
        max_results: 検索結果の上限数

    Returns:
        見つかった動画のURL、見つからない場合は None
    """
    import yt_dlp

    sanitized = _sanitize_name(player_name)
    query = f"{sanitized} SF6 Street Fighter 6 {char}"
    search_url = f"ytsearch{max_results}:{query}"
    logger.info("YouTube検索: %s", query)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_url, download=False)
            entries = info.get("entries", [])
            if not entries:
                logger.warning("検索結果なし: %s", query)
                return None

            for entry in entries:
                video_id = entry.get("id")
                title    = entry.get("title", "").lower()
                duration = entry.get("duration") or 0
                if duration and duration < 120:
                    continue
                if any(kw in title for kw in _SKIP_TITLE_KEYWORDS):
                    logger.debug("オーバーレイ系チャンネルのためスキップ: %s", title)
                    continue
                url = f"https://www.youtube.com/watch?v={video_id}"
                logger.info("採用: [%s] %s", url, title)
                return url

            logger.warning("条件を満たす動画が見つかりませんでした（フォールバック候補を試します）")
            return None

    except Exception as e:
        logger.error("YouTube検索失敗: %s", e)
        return None
