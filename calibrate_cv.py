"""SF6 CV抽出器キャリブレーションスクリプト。

実際の動画URLを指定して、SAゲージとラウンド番号検出のraw値を確認する。
出力された比率を見て cv_extractor.py の閾値を調整すること。

使い方:
    python calibrate_cv.py <youtube_url> [--sec 開始秒数]

例:
    python calibrate_cv.py "https://www.youtube.com/watch?v=xxxxx" --sec 120
"""

import argparse
import logging
import sys

import cv2
import numpy as np

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _capture_frame(url: str, sec: float) -> np.ndarray:
    """指定URLの指定秒数からフレームを1枚取得する。"""
    from cv_extractor import _resolve_youtube_url, _resolve_twitch_url

    if "twitch.tv" in url.lower():
        stream_url = _resolve_twitch_url(url)
    else:
        stream_url = _resolve_youtube_url(url)

    cap = cv2.VideoCapture(stream_url)
    cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise RuntimeError(f"{sec}秒でフレームを取得できませんでした")

    h, w = frame.shape[:2]
    if (w, h) != (1920, 1080):
        frame = cv2.resize(frame, (1920, 1080))
    return frame


def calibrate(url: str, sec: float) -> None:
    """指定フレームのHUD値をすべて出力する。"""
    print(f"\n{'='*60}")
    print(f"URL: {url[:80]}")
    print(f"秒数: {sec:.1f}s")
    print("="*60)

    frame = _capture_frame(url, sec)

    from cv_extractor import (
        _HUD, _HUD_HUE, _SA_THRESH_1, _SA_THRESH_2, _SA_THRESH_3,
        _bar_ratio, detect_round_number,
    )

    # --- HP / Drive ---
    p1_hp    = _bar_ratio(frame, *_HUD["p1_hp"],    fill_from_right=False, hue_range=_HUD_HUE["p1_hp"])
    p2_hp    = _bar_ratio(frame, *_HUD["p2_hp"],    fill_from_right=True,  hue_range=_HUD_HUE["p2_hp"])
    p1_drive = _bar_ratio(frame, *_HUD["p1_drive"], fill_from_right=False, hue_range=_HUD_HUE["p1_drive"])
    p2_drive = _bar_ratio(frame, *_HUD["p2_drive"], fill_from_right=True,  hue_range=_HUD_HUE["p2_drive"])

    print(f"\n[HP / Drive]")
    print(f"  P1 HP:    {p1_hp:.4f}  ({p1_hp*100:.1f}%)")
    print(f"  P2 HP:    {p2_hp:.4f}  ({p2_hp*100:.1f}%)")
    print(f"  P1 Drive: {p1_drive:.4f}  ({p1_drive*100:.1f}%)")
    print(f"  P2 Drive: {p2_drive:.4f}  ({p2_drive*100:.1f}%)")

    # --- SA raw ratio ---
    def _sa_raw_ratio(x1: int, y1: int, x2: int, y2: int) -> float:
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return 0.0
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        bright_mask = (
            ((hsv[:, :, 0] >= 120) & (hsv[:, :, 0] <= 170) & (hsv[:, :, 1] > 50))
            | (hsv[:, :, 2] > 200)
        )
        return bright_mask.sum() / bright_mask.size

    p1_sa_ratio = _sa_raw_ratio(*_HUD["p1_sa"])
    p2_sa_ratio = _sa_raw_ratio(*_HUD["p2_sa"])

    def _classify_sa(ratio: float) -> str:
        if ratio >= _SA_THRESH_3:
            return f"3ストック (>= {_SA_THRESH_3})"
        elif ratio >= _SA_THRESH_2:
            return f"2ストック (>= {_SA_THRESH_2})"
        elif ratio >= _SA_THRESH_1:
            return f"1ストック (>= {_SA_THRESH_1})"
        return f"0ストック (< {_SA_THRESH_1})"

    print(f"\n[SA ストック raw比率]")
    print(f"  現在の閾値: 1={_SA_THRESH_1}, 2={_SA_THRESH_2}, 3={_SA_THRESH_3}")
    print(f"  P1 SA ratio: {p1_sa_ratio:.4f} → {_classify_sa(p1_sa_ratio)}")
    print(f"  P2 SA ratio: {p2_sa_ratio:.4f} → {_classify_sa(p2_sa_ratio)}")
    print(f"\n  ヒント: 実際の画面のSAストック数と不一致の場合は")
    print(f"  cv_extractor.py の _SA_THRESH_* 定数を調整してください")

    # --- ラウンド番号 ---
    from cv_extractor import _round_wins
    p1_wins = _round_wins(frame, *_HUD["p1_round"])
    p2_wins = _round_wins(frame, *_HUD["p2_round"])
    round_num = detect_round_number(frame)

    print(f"\n[ラウンド番号]")
    print(f"  P1勝利数: {p1_wins}")
    print(f"  P2勝利数: {p2_wins}")
    print(f"  推定ラウンド: {round_num}")

    # --- 試合シーン判定 ---
    from cv_extractor import is_match_scene
    is_match = is_match_scene(frame)
    print(f"\n[試合シーン判定]")
    print(f"  is_match_scene: {is_match}")

    # --- デバッグ画像保存 ---
    out_path = f"/tmp/sf6_calibrate_{int(sec)}s.png"
    cv2.imwrite(out_path, frame)
    print(f"\n[デバッグ画像]")
    print(f"  {out_path} に保存しました")

    # --- ROIクロップ保存 ---
    for name, (x1, y1, x2, y2) in _HUD.items():
        crop = frame[y1:y2, x1:x2]
        crop_path = f"/tmp/sf6_roi_{name}_{int(sec)}s.png"
        cv2.imwrite(crop_path, crop)
    print(f"  各ROIクロップ: /tmp/sf6_roi_*_{int(sec)}s.png")

    print("="*60)


def main() -> None:
    parser = argparse.ArgumentParser(description="SF6 CV抽出器キャリブレーションツール")
    parser.add_argument("url", help="YouTube/Twitch の動画URL")
    parser.add_argument("--sec", type=float, default=60.0,
                        help="解析する秒数 (デフォルト: 60.0)")
    args = parser.parse_args()

    try:
        calibrate(args.url, args.sec)
    except Exception as e:
        logger.error("エラー: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
