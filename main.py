"""SF6 AI動画解析システム - FastAPI アプリケーション。

動画URLとキャラクター情報を受け取り、vision_extractor と logic_engine を
パイプライン実行して解析結果を返す Web API サーバーです。

エンドポイント:
    POST /api/v1/analyze : メイン解析エンドポイント
    GET  /health         : ヘルスチェック
"""

import asyncio
import logging
import logging.config
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from logic_engine.lethal_calculator import calculate_lethal
from logic_engine.punish_detector import detect_punish_opportunity
from schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ErrorResponse,
)
from vision_extractor import extract_game_state

# ---------------------------------------------------------------------------
# ロギング設定
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

ANALYZE_TIMEOUT_SECONDS = 30.0


# ---------------------------------------------------------------------------
# アプリケーション初期化
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """アプリケーションのライフサイクル管理。

    起動時にフレームデータのプリロードや初期化処理を行います。

    Args:
        app: FastAPI アプリケーションインスタンス。

    Yields:
        None
    """
    logger.info("SF6 AI動画解析システム 起動中...")
    yield
    logger.info("SF6 AI動画解析システム シャットダウン")


app = FastAPI(
    title="SF6 AI動画解析システム",
    description=(
        "Street Fighter 6 の対戦動画を解析し、"
        "確定反撃チャンスとリーサル判定をリアルタイムに提供します。"
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# 例外ハンドラ
# ---------------------------------------------------------------------------

@app.exception_handler(FileNotFoundError)
async def file_not_found_handler(request: Request, exc: FileNotFoundError) -> JSONResponse:
    """フレームデータファイル不在時の例外ハンドラ。

    Args:
        request: HTTPリクエスト。
        exc: 発生した FileNotFoundError。

    Returns:
        HTTP 500 エラーレスポンス。
    """
    logger.error("フレームデータファイルが見つかりません: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error_code="FRAME_DATA_NOT_FOUND",
            message="サーバー内部エラー: フレームデータが見つかりません。",
            detail=str(exc),
        ).model_dump(),
    )


@app.exception_handler(KeyError)
async def key_error_handler(request: Request, exc: KeyError) -> JSONResponse:
    """キャラクターデータ不在時の例外ハンドラ。

    Args:
        request: HTTPリクエスト。
        exc: 発生した KeyError。

    Returns:
        HTTP 404 エラーレスポンス。
    """
    logger.error("キャラクターデータが見つかりません: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content=ErrorResponse(
            error_code="CHARACTER_NOT_FOUND",
            message=f"キャラクターデータが見つかりません: {exc}",
        ).model_dump(),
    )


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    summary="ヘルスチェック",
    tags=["システム"],
)
async def health_check() -> dict[str, str]:
    """サーバーの稼働状態を確認するエンドポイント。

    Returns:
        ステータス情報の辞書。
    """
    return {"status": "ok", "service": "sf6-analyzer"}


@app.post(
    "/api/v1/analyze",
    response_model=AnalyzeResponse,
    summary="動画解析",
    description=(
        "動画URLとキャラクター情報を受け取り、"
        "確定反撃チャンスとリーサル判定を返します。"
    ),
    tags=["解析"],
    responses={
        200: {"description": "解析成功"},
        408: {"model": ErrorResponse, "description": "解析タイムアウト（30秒超過）"},
        422: {"description": "バリデーションエラー（不正なURL・キャラクター名）"},
        500: {"model": ErrorResponse, "description": "サーバー内部エラー"},
    },
)
async def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    """動画を解析して確定反撃とリーサル判定を返すメインエンドポイント。

    処理パイプライン:
        1. vision_extractor でゲーム状態を抽出（モック）
        2. punish_detector で確定反撃を判定
        3. lethal_calculator でリーサルを計算
        4. AnalyzeResponse として返却

    Args:
        request: 解析リクエスト（video_url, character_p1, character_p2）。

    Returns:
        解析結果を含む AnalyzeResponse。

    Raises:
        HTTPException(408): 処理が30秒を超えた場合。
        HTTPException(500): 内部処理で予期しないエラーが発生した場合。
    """
    video_url_str = str(request.video_url)
    logger.info(
        "解析リクエスト受信 | url=%s, p1=%s, p2=%s",
        video_url_str,
        request.character_p1.value,
        request.character_p2.value,
    )

    try:
        game_state = await asyncio.wait_for(
            asyncio.to_thread(
                extract_game_state,
                video_url_str,
                request.character_p1,
                request.character_p2,
            ),
            timeout=ANALYZE_TIMEOUT_SECONDS,
        )
        logger.info("vision_extractor 完了 | frame=%d", game_state.frame_number)

    except asyncio.TimeoutError:
        logger.error("解析タイムアウト: %s秒を超過しました", ANALYZE_TIMEOUT_SECONDS)
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail=ErrorResponse(
                error_code="ANALYZE_TIMEOUT",
                message=f"解析処理が {ANALYZE_TIMEOUT_SECONDS} 秒以内に完了しませんでした。",
            ).model_dump(),
        )
    except Exception as exc:
        logger.exception("vision_extractor で予期しないエラー: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorResponse(
                error_code="EXTRACTION_ERROR",
                message="動画解析中にエラーが発生しました。",
                detail=str(exc),
            ).model_dump(),
        )

    # 確定反撃判定（P1視点: P1が攻撃側、P2がリカバリー側）
    logger.info("punish_detector 開始")
    punish_result = detect_punish_opportunity(
        attacker=game_state.player1,
        defender=game_state.player2,
    )
    logger.info(
        "punish_detector 完了 | is_punishable=%s, moves=%d",
        punish_result.is_punishable,
        len(punish_result.punish_moves),
    )

    # リーサル判定（P1視点: P1が攻撃側、P2が守備側）
    logger.info("lethal_calculator 開始")
    lethal_result = calculate_lethal(
        attacker=game_state.player1,
        defender=game_state.player2,
    )
    logger.info(
        "lethal_calculator 完了 | is_lethal=%s, damage=%d",
        lethal_result.is_lethal,
        lethal_result.estimated_max_damage,
    )

    response = AnalyzeResponse(
        video_url=video_url_str,
        frame_number=game_state.frame_number,
        round_number=game_state.round_number,
        player1_state=game_state.player1,
        player2_state=game_state.player2,
        punish_opportunity=punish_result,
        lethal_result=lethal_result,
    )

    logger.info("解析完了 | punishable=%s, lethal=%s", punish_result.is_punishable, lethal_result.is_lethal)
    return response
