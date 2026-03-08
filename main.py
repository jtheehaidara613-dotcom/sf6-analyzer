"""SF6 AI動画解析システム - FastAPI アプリケーション。

動画URLとキャラクター情報を受け取り、vision_extractor と logic_engine を
パイプライン実行して解析結果を返す Web API サーバーです。

エンドポイント:
    POST /api/v1/analyze         : メイン解析エンドポイント
    POST /api/v1/scan            : 動画全体スキャン
    POST /api/v1/live/start      : ライブ解析セッション開始
    DELETE /api/v1/live/{id}     : ライブ解析セッション停止
    GET  /api/v1/live/{id}       : セッション状態 + 最新結果
    GET  /api/v1/live/{id}/stream: SSE ストリーム（最新結果を継続プッシュ）
    GET  /api/v1/history         : 解析履歴一覧
    GET  /api/v1/stats           : 集計統計
    GET  /health                 : ヘルスチェック
"""

import asyncio
import json
import logging
import logging.config
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from database import fetch_results, fetch_stats, init_db, save_result
from live_analyzer import session_manager
from logic_engine.lethal_calculator import calculate_lethal
from logic_engine.punish_detector import detect_punish_opportunity
from schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ErrorResponse,
    HistoryItem,
    HistoryResponse,
    LiveStartRequest,
    LiveStartResponse,
    LiveStatusResponse,
    ScanRequest,
    ScanResponse,
    StatsResponse,
)
from vision_extractor import extract_game_state, scan_and_analyze

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
    init_db()
    yield
    logger.info("ライブセッションを全て停止中...")
    session_manager.stop_all()
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

    save_result(response)
    logger.info("解析完了 | punishable=%s, lethal=%s", punish_result.is_punishable, lethal_result.is_lethal)
    return response


SCAN_TIMEOUT_SECONDS = 600.0  # 動画全体スキャンは最大10分


@app.post(
    "/api/v1/scan",
    response_model=ScanResponse,
    summary="動画全体スキャン",
    description=(
        "動画全体を自動スキャンして試合シーンを検出し、"
        "各シーンの確定反撃・リーサル判定を一括返却します。"
    ),
    tags=["解析"],
    responses={
        200: {"description": "スキャン成功"},
        408: {"model": ErrorResponse, "description": "スキャンタイムアウト（600秒超過）"},
        422: {"description": "バリデーションエラー"},
        500: {"model": ErrorResponse, "description": "サーバー内部エラー"},
    },
)
async def scan(request: ScanRequest) -> ScanResponse:
    """動画全体をスキャンして全試合シーンを解析するエンドポイント。

    Args:
        request: スキャンリクエスト。

    Returns:
        全試合シーンの解析結果を含む ScanResponse。
    """
    video_url_str = str(request.video_url)
    logger.info(
        "スキャンリクエスト受信 | url=%s, p1=%s, p2=%s, interval=%.1fs",
        video_url_str,
        request.character_p1.value,
        request.character_p2.value,
        request.scan_interval_sec,
    )

    try:
        results = await asyncio.wait_for(
            asyncio.to_thread(
                scan_and_analyze,
                video_url_str,
                request.character_p1,
                request.character_p2,
                request.scan_interval_sec,
                request.max_duration_sec,
                request.max_workers,
            ),
            timeout=SCAN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.error("スキャンタイムアウト: %s秒を超過", SCAN_TIMEOUT_SECONDS)
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail=ErrorResponse(
                error_code="SCAN_TIMEOUT",
                message=f"スキャン処理が {SCAN_TIMEOUT_SECONDS} 秒以内に完了しませんでした。",
            ).model_dump(),
        )
    except Exception as exc:
        logger.exception("スキャン中に予期しないエラー: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorResponse(
                error_code="SCAN_ERROR",
                message="動画スキャン中にエラーが発生しました。",
                detail=str(exc),
            ).model_dump(),
        )

    scenes: list[AnalyzeResponse] = []
    for t, game_state in results:
        punish_result = detect_punish_opportunity(
            attacker=game_state.player1,
            defender=game_state.player2,
        )
        lethal_result = calculate_lethal(
            attacker=game_state.player1,
            defender=game_state.player2,
        )
        scenes.append(AnalyzeResponse(
            video_url=video_url_str,
            frame_number=game_state.frame_number,
            round_number=game_state.round_number,
            player1_state=game_state.player1,
            player2_state=game_state.player2,
            punish_opportunity=punish_result,
            lethal_result=lethal_result,
        ))

    for scene in scenes:
        save_result(scene)
    logger.info("スキャン完了 | %d シーン解析済み", len(scenes))
    return ScanResponse(
        video_url=video_url_str,
        total_scenes=len(scenes),
        scenes=scenes,
    )


# ---------------------------------------------------------------------------
# ライブ配信解析エンドポイント
# ---------------------------------------------------------------------------

@app.post(
    "/api/v1/live/start",
    response_model=LiveStartResponse,
    summary="ライブ解析開始",
    description="ライブ配信URLを指定してリアルタイム解析セッションを開始する。",
    tags=["ライブ"],
    responses={
        200: {"description": "セッション開始成功"},
        422: {"description": "バリデーションエラー"},
    },
)
async def live_start(request: LiveStartRequest) -> LiveStartResponse:
    """ライブ解析セッションを開始し session_id を返す。

    Args:
        request: ライブ開始リクエスト（配信URL・キャラクター・解析間隔）。

    Returns:
        セッションID と初期ステータスを含む LiveStartResponse。
    """
    session = session_manager.create(
        video_url=str(request.video_url),
        character_p1=request.character_p1,
        character_p2=request.character_p2,
        interval_sec=request.interval_sec,
    )
    logger.info("ライブセッション開始 | id=%s url=%s", session.session_id, str(request.video_url)[:60])
    return LiveStartResponse(session_id=session.session_id, status=session.status.value)


@app.delete(
    "/api/v1/live/{session_id}",
    summary="ライブ解析停止",
    tags=["ライブ"],
    responses={
        200: {"description": "停止成功"},
        404: {"description": "セッションが存在しない"},
    },
)
async def live_stop(session_id: str) -> dict[str, str]:
    """指定セッションのライブ解析を停止する。

    Args:
        session_id: 停止対象のセッションID。

    Returns:
        停止結果メッセージ。
    """
    stopped = session_manager.stop(session_id)
    if not stopped:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"セッションが見つかりません: {session_id}",
        )
    logger.info("ライブセッション停止 | id=%s", session_id)
    return {"status": "stopped", "session_id": session_id}


@app.get(
    "/api/v1/live/{session_id}",
    response_model=LiveStatusResponse,
    summary="ライブ解析状態取得",
    tags=["ライブ"],
    responses={
        200: {"description": "状態取得成功"},
        404: {"description": "セッションが存在しない"},
    },
)
async def live_status(session_id: str) -> LiveStatusResponse:
    """セッションの状態と最新の解析結果を返す。

    Args:
        session_id: 対象のセッションID。

    Returns:
        セッション状態と最新解析結果（まだなければ null）。
    """
    session = session_manager.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"セッションが見つかりません: {session_id}",
        )
    return LiveStatusResponse(
        session_id=session_id,
        status=session.status.value,
        latest_result=session.get_latest(),
        error_message=session.error_message,
    )


@app.get(
    "/api/v1/live/{session_id}/stream",
    summary="ライブ解析SSEストリーム",
    description="Server-Sent Events で最新の解析結果を継続プッシュする。",
    tags=["ライブ"],
    responses={
        200: {"description": "SSEストリーム（text/event-stream）"},
        404: {"description": "セッションが存在しない"},
    },
)
async def live_stream(session_id: str, poll_interval_sec: float = 1.0) -> StreamingResponse:
    """SSE ストリームで最新解析結果を継続プッシュする。

    クライアントは `EventSource` でこのエンドポイントに接続すると、
    解析結果が更新されるたびに JSON イベントを受信できる。

    Args:
        session_id: ストリーム対象のセッションID。
        poll_interval_sec: 結果チェック間隔（秒）。デフォルト 1 秒。

    Returns:
        text/event-stream 形式の StreamingResponse。
    """
    session = session_manager.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"セッションが見つかりません: {session_id}",
        )

    async def event_generator():
        last_frame = -1
        while True:
            result = session.get_latest()
            if result is not None and result.frame_number != last_frame:
                last_frame = result.frame_number
                data = result.model_dump_json()
                yield f"data: {data}\n\n"
            elif session.status.value in ("stopped", "error"):
                error_msg = session.error_message or "セッション終了"
                yield f"event: close\ndata: {json.dumps({'reason': error_msg})}\n\n"
                break
            await asyncio.sleep(poll_interval_sec)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# 履歴・統計エンドポイント
# ---------------------------------------------------------------------------

@app.get(
    "/api/v1/history",
    response_model=HistoryResponse,
    summary="解析履歴一覧",
    tags=["履歴"],
)
async def history(
    video_url: str | None = None,
    character: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> HistoryResponse:
    """保存済み解析結果のメタデータ一覧を返す。

    Args:
        video_url: URLで絞り込み（部分一致）。
        character: キャラクター名で絞り込み（p1 or p2 どちらかに一致）。
        limit: 最大取得件数（デフォルト 50）。
        offset: スキップ件数（ページネーション用）。
    """
    rows = await asyncio.to_thread(fetch_results, video_url, character, limit, offset)
    items = [
        HistoryItem(
            **{k: bool(v) if k in ("is_punishable", "is_lethal") else v for k, v in row.items()
               if k != "payload"}
        )
        for row in rows
    ]
    return HistoryResponse(total_returned=len(items), offset=offset, items=items)


@app.get(
    "/api/v1/stats",
    response_model=StatsResponse,
    summary="集計統計",
    tags=["履歴"],
)
async def stats(video_url: str | None = None) -> StatsResponse:
    """解析結果の集計統計を返す。

    Args:
        video_url: URLで絞り込み（部分一致）。None で全件対象。
    """
    data = await asyncio.to_thread(fetch_stats, video_url)
    return StatsResponse(**data)
