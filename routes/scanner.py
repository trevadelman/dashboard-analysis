"""
routes/scanner.py — Market scanner, market pulse, analysis streams, and config.

Covers:
  POST /api/analyze
  GET  /api/analyze/stream
  GET  /api/analyze/stream/multi
  GET  /api/config
  POST /api/config
  GET  /api/scan/stream
  GET  /api/scan/cache
  GET  /api/scan/cache/info
  GET  /api/scan/sectors
  GET  /api/scan/universe/info
  POST /api/scan/universe/refresh
  GET  /api/market/pulse
  GET  /api/market/pulse/stream
  GET  /api/market/overview
"""

import json as _json
import logging
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

_SCAN_CACHE_PATH = os.path.join("data", "scan_cache.json")


def create_router(bot, login_required) -> APIRouter:
    router = APIRouter()

    # ── Analysis ──────────────────────────────────────────────────────────────

    @router.post("/api/analyze")
    async def analyze(request: Request, _=Depends(login_required)):
        try:
            data   = await request.json()
            symbol = data.get("symbol", "SPY")
            use_ai = data.get("use_ai", True)
            params = data.get("params")
            return bot.analyze_symbol(symbol, use_ai_confirmation=use_ai, params=params)
        except Exception as e:
            logger.error(f"Error analyzing symbol: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.get("/api/analyze/stream")
    async def analyze_stream(
        request: Request,
        symbol: str = "SPY",
        use_ai: bool = True,
        _=Depends(login_required),
    ):
        def event_generator():
            try:
                for chunk in bot.analyze_symbol_stream(symbol, use_ai_confirmation=use_ai):
                    yield chunk
            except Exception as e:
                logger.error(f"Error in analysis stream for {symbol}: {e}")
                yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/api/analyze/stream/multi")
    async def analyze_stream_multi(
        request: Request,
        symbol: str = "SPY",
        use_ai: bool = True,
        _=Depends(login_required),
    ):
        def event_generator():
            try:
                for chunk in bot.analyze_symbol_multi_timeframe_stream(
                    symbol, use_ai_confirmation=use_ai
                ):
                    yield chunk
            except Exception as e:
                logger.error(f"Error in multi-timeframe stream for {symbol}: {e}")
                yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Strategy config ───────────────────────────────────────────────────────

    @router.get("/api/config")
    async def get_config(request: Request, _=Depends(login_required)):
        return bot.config.STRATEGY_PARAMS

    @router.post("/api/config")
    async def update_config(request: Request, _=Depends(login_required)):
        try:
            new_params = await request.json()
            for key in bot.config.STRATEGY_PARAMS.keys():
                if key in new_params:
                    expected_type = type(bot.config.STRATEGY_PARAMS[key])
                    bot.config.STRATEGY_PARAMS[key] = expected_type(new_params[key])
            return {"status": "success", "config": bot.config.STRATEGY_PARAMS}
        except Exception as e:
            logger.error(f"Error updating config: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── Scanner ───────────────────────────────────────────────────────────────

    @router.get("/api/scan/stream")
    async def scan_stream(
        request: Request,
        list_name: str = "sp500_top100",
        custom: str = "",
        timeframe: str = "long",
        _=Depends(login_required),
    ):
        """Stream market scanner results via SSE."""
        from screeners.market_scanner import MarketScanner

        if not bot._require_api():
            async def no_api():
                yield f"data: {_json.dumps({'type': 'error', 'message': 'No Alpaca credentials — add a profile first'})}\n\n"
            return StreamingResponse(no_api(), media_type="text/event-stream")

        scanner = MarketScanner(bot.data_client, crypto_client=bot.crypto_client)

        def event_generator():
            collected = []
            try:
                for chunk in scanner.scan_stream(list_name=list_name, custom=custom, timeframe=timeframe):
                    try:
                        payload = _json.loads(chunk.removeprefix("data: ").strip())
                        if payload.get("type") == "result":
                            collected.append(payload)
                        elif payload.get("type") == "done":
                            scanner.write_cache(collected, list_name, timeframe)
                    except Exception:
                        pass
                    yield chunk
            except Exception as e:
                logger.error(f"Scanner error: {e}")
                yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/api/scan/cache")
    async def get_scan_cache(
        request: Request,
        timeframe: str = "long",
        _=Depends(login_required),
    ):
        """Return cached scan results for a specific timeframe."""
        if not os.path.exists(_SCAN_CACHE_PATH):
            return JSONResponse({"error": "No scan cache found — run a scan first"}, status_code=404)
        try:
            with open(_SCAN_CACHE_PATH, "r") as f:
                cache = _json.load(f)
            tf_data = cache.get(timeframe, {})
            if not isinstance(tf_data, dict):
                tf_data = {}
            results = list(tf_data.values())
            return {
                "timeframe":    timeframe,
                "results":      results,
                "total":        len(results),
                "signals":      sum(1 for r in results if r.get("signal") in ("BUY", "SELL")),
                "last_updated": cache.get("last_updated", ""),
            }
        except Exception as e:
            logger.error(f"Error reading scan cache: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.get("/api/scan/cache/info")
    async def get_scan_cache_info(request: Request, _=Depends(login_required)):
        """Return metadata about the scan cache — counts per timeframe, last updated."""
        if not os.path.exists(_SCAN_CACHE_PATH):
            return {"cached": False}
        try:
            with open(_SCAN_CACHE_PATH, "r") as f:
                cache = _json.load(f)
            info = {"cached": True, "last_updated": cache.get("last_updated", ""), "timeframes": {}}
            for tf in ("long", "swing", "short"):
                tf_data = cache.get(tf, {})
                if isinstance(tf_data, dict) and tf_data:
                    results = list(tf_data.values())
                    info["timeframes"][tf] = {
                        "total":   len(results),
                        "signals": sum(1 for r in results if r.get("signal") in ("BUY", "SELL")),
                    }
            return info
        except Exception as e:
            logger.error(f"Error reading scan cache info: {e}")
            return {"cached": False}

    @router.get("/api/scan/sectors")
    async def get_sectors(request: Request, _=Depends(login_required)):
        """Return the list of available GICS sector names."""
        from screeners.symbol_lists import SECTORS
        return {"sectors": list(SECTORS.keys())}

    @router.get("/api/scan/universe/info")
    async def get_universe_info(request: Request, _=Depends(login_required)):
        """Return metadata about the cached asset universe."""
        from screeners.symbol_lists import get_universe_cache_info
        return get_universe_cache_info()

    @router.post("/api/scan/universe/refresh")
    async def refresh_universe(request: Request, _=Depends(login_required)):
        """Trigger a fresh fetch of the Alpaca asset universe (equities + crypto)."""
        if not bot._require_api():
            return JSONResponse({"error": "No Alpaca credentials"}, status_code=400)
        try:
            from screeners.symbol_lists import (
                fetch_alpaca_universe,
                fetch_alpaca_crypto_universe,
                _UNIVERSE_CACHE_PATH,
                _CRYPTO_UNIVERSE_CACHE_PATH,
            )
            import os as _os

            equity_path = _os.path.normpath(_UNIVERSE_CACHE_PATH)
            if _os.path.exists(equity_path):
                _os.remove(equity_path)

            crypto_path = _os.path.normpath(_CRYPTO_UNIVERSE_CACHE_PATH)
            if _os.path.exists(crypto_path):
                _os.remove(crypto_path)

            equity_symbols = fetch_alpaca_universe(bot.trading_client, min_price=1.0)
            crypto_symbols = fetch_alpaca_crypto_universe(bot.trading_client)

            return {
                "status":       "ok",
                "equity_count": len(equity_symbols),
                "crypto_count": len(crypto_symbols),
            }
        except Exception as e:
            logger.error(f"Universe refresh error: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── Market pulse ──────────────────────────────────────────────────────────

    def _compute_stats(rows: list) -> dict | None:
        """Compute aggregate stats for a list of scan result rows."""
        if not rows:
            return None
        total   = len(rows)
        signals = [r for r in rows if r.get("signal") in ("BUY", "SELL")]
        bullish = sum(1 for r in rows if r.get("regime") == "BULLISH")
        bearish = sum(1 for r in rows if r.get("regime") == "BEARISH")
        no_trade = total - bullish - bearish

        scores    = [r["score"] for r in rows if r.get("score") is not None]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0

        grade_dist = {"A": 0, "B": 0, "C": 0, "D": 0}
        for r in rows:
            sc = r.get("score")
            if sc is None:
                continue
            if sc >= 80:
                grade_dist["A"] += 1
            elif sc >= 60:
                grade_dist["B"] += 1
            elif sc >= 40:
                grade_dist["C"] += 1
            else:
                grade_dist["D"] += 1

        score_buckets = {"0-20": 0, "20-40": 0, "40-60": 0, "60-80": 0, "80-100": 0}
        for r in rows:
            sc = r.get("score")
            if sc is None:
                continue
            if sc < 20:
                score_buckets["0-20"] += 1
            elif sc < 40:
                score_buckets["20-40"] += 1
            elif sc < 60:
                score_buckets["40-60"] += 1
            elif sc < 80:
                score_buckets["60-80"] += 1
            else:
                score_buckets["80-100"] += 1

        setups_forming = sum(
            1 for r in rows
            if r.get("signal") == "NONE"
            and r.get("tier_reached", 1) >= 2
            and (r.get("bb_width_pct") or 100) <= 50
            and (r.get("rs_vs_spy") or -999) >= 0
        )

        failure_counts: dict = {}
        for r in rows:
            if r.get("signal") != "NONE":
                continue
            if r.get("tier_reached", 1) < 2:
                failure_counts["Regime"] = failure_counts.get("Regime", 0) + 1
                continue
            bb          = r.get("bb_width_pct")
            rs          = r.get("rs_vs_spy")
            rvol        = r.get("rvol")
            reason_text = (r.get("tier2_reason") or "").lower()
            if bb is not None and bb > 50:
                failure_counts["BB Compression"] = failure_counts.get("BB Compression", 0) + 1
            elif rs is not None and rs < 0:
                failure_counts["Relative Strength"] = failure_counts.get("Relative Strength", 0) + 1
            elif "breakout" in reason_text or "broke" in reason_text or "range" in reason_text:
                failure_counts["Breakout"] = failure_counts.get("Breakout", 0) + 1
            elif rvol is not None and rvol < 1.1:
                failure_counts["RVOL"] = failure_counts.get("RVOL", 0) + 1
            elif "rsi" in reason_text:
                failure_counts["RSI"] = failure_counts.get("RSI", 0) + 1
            elif "ema9" in reason_text or "ema 9" in reason_text:
                failure_counts["EMA9 Trigger"] = failure_counts.get("EMA9 Trigger", 0) + 1
            else:
                failure_counts["Other"] = failure_counts.get("Other", 0) + 1

        top_failure = max(failure_counts, key=failure_counts.get) if failure_counts else "N/A"

        signal_rate    = len(signals) / total if total else 0
        bullish_rate   = bullish / total if total else 0
        high_score_pct = (grade_dist["A"] + grade_dist["B"]) / total if total else 0

        if signal_rate >= 0.15 and bullish_rate >= 0.6:
            stance = "RISK ON"
        elif signal_rate >= 0.05 and bullish_rate >= 0.5:
            stance = "SELECTIVE"
        elif bullish_rate < 0.3:
            stance = "RISK OFF"
        elif high_score_pct >= 0.20 and bullish_rate >= 0.5:
            stance = "COILING"
        else:
            stance = "WAIT"

        return {
            "total":          total,
            "signals":        len(signals),
            "signal_rate":    round(signal_rate * 100, 1),
            "setups_forming": setups_forming,
            "setup_rate":     round(setups_forming / total * 100, 1) if total else 0,
            "bullish":        bullish,
            "bearish":        bearish,
            "no_trade":       no_trade,
            "bullish_pct":    round(bullish / total * 100, 1) if total else 0,
            "bearish_pct":    round(bearish / total * 100, 1) if total else 0,
            "avg_score":      avg_score,
            "grade_dist":     grade_dist,
            "score_buckets":  score_buckets,
            "top_failure":    top_failure,
            "failure_counts": failure_counts,
            "stance":         stance,
            "high_score_pct": round(high_score_pct * 100, 1),
        }

    @router.get("/api/market/pulse")
    async def get_market_pulse(
        request: Request,
        timeframe: str = "all",
        _=Depends(login_required),
    ):
        """
        Compute market pulse statistics from the scan cache.

        When timeframe="all" (default), aggregates across all three timeframes.
        When timeframe is "long", "swing", or "short", returns stats for that
        timeframe only (backward-compatible).
        """
        from screeners.symbol_lists import SECTORS

        if not os.path.exists(_SCAN_CACHE_PATH):
            return JSONResponse({"error": "No scan cache — run a scan first"}, status_code=404)

        try:
            with open(_SCAN_CACHE_PATH, "r") as f:
                cache = _json.load(f)

            last_updated = cache.get("last_updated", "")

            if timeframe in ("long", "swing", "short"):
                tf_data = cache.get(timeframe, {})
                if not isinstance(tf_data, dict) or not tf_data:
                    return JSONResponse(
                        {"error": f"No cached results for timeframe '{timeframe}' — run a scan first"},
                        status_code=404,
                    )
                results = list(tf_data.values())
            else:
                results = []
                for tf in ("long", "swing", "short"):
                    tf_data = cache.get(tf, {})
                    if isinstance(tf_data, dict):
                        results.extend(tf_data.values())
                if not results:
                    return JSONResponse({"error": "No scan cache — run a scan first"}, status_code=404)

            overall = _compute_stats(results)

            sym_to_sector: dict = {}
            for sector_name, syms in SECTORS.items():
                for s in syms:
                    if s not in sym_to_sector:
                        sym_to_sector[s] = sector_name

            sector_buckets: dict = {}
            for r in results:
                sector = sym_to_sector.get(r.get("symbol", ""), "Other")
                sector_buckets.setdefault(sector, []).append(r)

            sectors = []
            for sector_name, rows in sector_buckets.items():
                stats = _compute_stats(rows)
                if stats:
                    stats["sector"] = sector_name
                    sectors.append(stats)

            sectors.sort(key=lambda s: s["avg_score"], reverse=True)

            return {
                "timeframe":    timeframe,
                "last_updated": last_updated,
                "overall":      overall,
                "sectors":      sectors,
            }

        except Exception as e:
            logger.error(f"Market pulse error: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.get("/api/market/pulse/stream")
    async def market_pulse_stream(
        request: Request,
        timeframe: str = "long",
        _=Depends(login_required),
    ):
        """Stream AI commentary on the current market pulse via SSE."""
        pulse_res = await get_market_pulse(request, timeframe=timeframe, _=None)
        if isinstance(pulse_res, JSONResponse):
            async def err():
                yield f"data: {_json.dumps({'type': 'error', 'message': 'No cache data available'})}\n\n"
            return StreamingResponse(err(), media_type="text/event-stream")

        pulse   = pulse_res if isinstance(pulse_res, dict) else {}
        overall = pulse.get("overall", {})
        sectors = pulse.get("sectors", [])

        hot_sectors     = [s for s in sectors if s.get("signal_rate", 0) >= 10]
        coiling_sectors = [s for s in sectors if s.get("setup_rate", 0) >= 10]

        sector_table = "\n".join(
            f"  {s['sector']}: score={s['avg_score']}, signals={s['signals']} ({s['signal_rate']}%), "
            f"setups={s.get('setups_forming', 0)} ({s.get('setup_rate', 0)}%), "
            f"bullish={s['bullish_pct']}%, top_failure={s.get('top_failure', 'N/A')}"
            for s in sectors
        )

        gd = overall.get("grade_dist", {})
        sb = overall.get("score_buckets", {})
        fc = overall.get("failure_counts", {})
        failure_breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(fc.items(), key=lambda x: -x[1]))

        prompt = f"""You are a quantitative market analyst reviewing a batch scan of {overall.get('total', 0)} stocks.

## SCAN PARAMETERS
- Timeframe: {timeframe.upper()} ({'daily bars' if timeframe == 'long' else 'hourly bars' if timeframe == 'swing' else '15-min bars'})
- Strategy: Compression breakout with relative strength filter (BB squeeze + RS vs SPY + RVOL + RSI momentum)
- Scoring: 0-100 composite (BB compression 25pts, RS vs SPY 25pts, RVOL 20pts, RSI momentum 15pts, ATR contraction 15pts)

## OVERALL MARKET STATS
- Stance: {overall.get('stance', 'N/A')}
- Regime: {overall.get('bullish_pct', 0)}% BULLISH / {overall.get('bearish_pct', 0)}% BEARISH / {round((overall.get('no_trade', 0) / overall.get('total', 1)) * 100, 1)}% NO_TRADE
- Avg Score: {overall.get('avg_score', 0)}/100
- High-quality setups (A+B grade): {overall.get('high_score_pct', 0)}% of universe
- Triggered signals (BUY/SELL): {overall.get('signals', 0)} ({overall.get('signal_rate', 0)}%)
- Setups forming (compressed + outperforming, no trigger yet): {overall.get('setups_forming', 0)} ({overall.get('setup_rate', 0)}%)

## GRADE DISTRIBUTION (all symbols, score-based)
- A (80-100): {gd.get('A', 0)} symbols
- B (60-79):  {gd.get('B', 0)} symbols
- C (40-59):  {gd.get('C', 0)} symbols
- D (0-39):   {gd.get('D', 0)} symbols

## SCORE DISTRIBUTION
- 80-100: {sb.get('80-100', 0)} | 60-80: {sb.get('60-80', 0)} | 40-60: {sb.get('40-60', 0)} | 20-40: {sb.get('20-40', 0)} | 0-20: {sb.get('0-20', 0)}

## SIGNAL FAILURE BREAKDOWN (why signals aren't triggering)
{failure_breakdown if failure_breakdown else 'N/A'}

## ALL SECTORS (sorted by avg score, highest first)
{sector_table}

{"## HOT SECTORS (>10% signal rate): " + ", ".join(s['sector'] for s in hot_sectors) if hot_sectors else "## No sectors with >10% signal rate"}
{"## COILING SECTORS (>10% setups forming): " + ", ".join(s['sector'] for s in coiling_sectors) if coiling_sectors else ""}

## YOUR TASK
Provide a structured market commentary with these sections:

### Market Environment
2-3 sentences on what the data tells you about the current market environment. Reference specific numbers.

### Positioning Stance
What should a momentum trader do right now? Be specific: sit on hands, hold SPY, look for selective entries, etc. Explain why based on the data.

### Sector Rotation
Which sectors are leading, which are lagging, and what does the rotation pattern suggest? Reference the failure breakdown to explain why sectors are or aren't triggering.

### Setups to Watch
If there are setups forming (compressed + outperforming but not yet triggered), what does that mean? Are we close to a broad breakout or is this a false coil?

### Actionable Takeaway
One specific, concrete action a trader should take today based on this data.

Rules: Use markdown headers. Be direct. Use actual numbers from the data. No disclaimers. Write like a seasoned trader reviewing a morning scan, not a compliance officer."""

        def event_generator():
            try:
                for chunk in bot.ai.stream_analysis(prompt):
                    yield f"data: {_json.dumps({'type': 'chunk', 'text': chunk})}\n\n"
                yield f"data: {_json.dumps({'type': 'done'})}\n\n"
            except Exception as e:
                logger.error(f"Market pulse AI stream error: {e}")
                yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/api/market/overview")
    async def get_market_overview(request: Request, _=Depends(login_required)):
        """
        Return a cross-timeframe overview for the Market Pulse page.

        Returns per-timeframe stats, active signals across all timeframes,
        and top 10 setups per timeframe.
        """
        if not os.path.exists(_SCAN_CACHE_PATH):
            return JSONResponse({"error": "No scan cache — run a scan first"}, status_code=404)

        try:
            with open(_SCAN_CACHE_PATH, "r") as f:
                cache = _json.load(f)

            last_updated = cache.get("last_updated", "")
            tf_summaries: dict = {}
            all_signals:  list = []
            top_setups:   dict = {}

            for tf in ("long", "swing", "short"):
                tf_data = cache.get(tf, {})
                if not isinstance(tf_data, dict) or not tf_data:
                    continue

                rows     = list(tf_data.values())
                total    = len(rows)
                bullish  = sum(1 for r in rows if r.get("regime") == "BULLISH")
                bearish  = sum(1 for r in rows if r.get("regime") == "BEARISH")
                signals  = [r for r in rows if r.get("signal") in ("BUY", "SELL")]
                setups   = [
                    r for r in rows
                    if r.get("signal") == "NONE"
                    and r.get("tier_reached", 1) >= 2
                    and (r.get("bb_width_pct") or 100) <= 50
                    and (r.get("rs_vs_spy") or -999) >= 0
                ]
                scores    = [r["score"] for r in rows if r.get("score") is not None]
                avg_score = round(sum(scores) / len(scores), 1) if scores else 0

                signal_rate    = len(signals) / total if total else 0
                bullish_rate   = bullish / total if total else 0
                high_score_pct = sum(1 for s in scores if s >= 60) / total if total else 0

                if signal_rate >= 0.15 and bullish_rate >= 0.6:
                    stance = "RISK ON"
                elif signal_rate >= 0.05 and bullish_rate >= 0.5:
                    stance = "SELECTIVE"
                elif bullish_rate < 0.3:
                    stance = "RISK OFF"
                elif high_score_pct >= 0.20 and bullish_rate >= 0.5:
                    stance = "COILING"
                else:
                    stance = "WAIT"

                grade_dist: dict = {"A": 0, "B": 0, "C": 0, "D": 0}
                for r in rows:
                    sc = r.get("score")
                    if sc is None:
                        continue
                    if sc >= 80:
                        grade_dist["A"] += 1
                    elif sc >= 60:
                        grade_dist["B"] += 1
                    elif sc >= 40:
                        grade_dist["C"] += 1
                    else:
                        grade_dist["D"] += 1

                failure_counts: dict = {}
                for r in rows:
                    if r.get("signal") != "NONE":
                        continue
                    if r.get("tier_reached", 1) < 2:
                        failure_counts["Regime"] = failure_counts.get("Regime", 0) + 1
                        continue
                    bb          = r.get("bb_width_pct")
                    rs          = r.get("rs_vs_spy")
                    rvol        = r.get("rvol")
                    reason_text = (r.get("tier2_reason") or "").lower()
                    if bb is not None and bb > 50:
                        failure_counts["BB Compression"] = failure_counts.get("BB Compression", 0) + 1
                    elif rs is not None and rs < 0:
                        failure_counts["Relative Strength"] = failure_counts.get("Relative Strength", 0) + 1
                    elif "breakout" in reason_text or "broke" in reason_text or "range" in reason_text:
                        failure_counts["Breakout"] = failure_counts.get("Breakout", 0) + 1
                    elif rvol is not None and rvol < 1.1:
                        failure_counts["RVOL"] = failure_counts.get("RVOL", 0) + 1
                    elif "rsi" in reason_text:
                        failure_counts["RSI"] = failure_counts.get("RSI", 0) + 1
                    elif "ema9" in reason_text or "ema 9" in reason_text:
                        failure_counts["EMA9 Trigger"] = failure_counts.get("EMA9 Trigger", 0) + 1
                    else:
                        failure_counts["Other"] = failure_counts.get("Other", 0) + 1

                tf_summaries[tf] = {
                    "total":          total,
                    "bullish":        bullish,
                    "bearish":        bearish,
                    "bullish_pct":    round(bullish / total * 100, 1) if total else 0,
                    "bearish_pct":    round(bearish / total * 100, 1) if total else 0,
                    "signals":        len(signals),
                    "signal_rate":    round(signal_rate * 100, 1),
                    "setups_forming": len(setups),
                    "setup_rate":     round(len(setups) / total * 100, 1) if total else 0,
                    "avg_score":      avg_score,
                    "stance":         stance,
                    "grade_dist":     grade_dist,
                    "failure_counts": failure_counts,
                }

                for r in signals:
                    all_signals.append({**r, "timeframe": tf})

                sorted_rows = sorted(rows, key=lambda r: r.get("score", 0), reverse=True)
                top_setups[tf] = sorted_rows[:10]

            all_signals.sort(key=lambda r: r.get("score", 0), reverse=True)

            return {
                "last_updated": last_updated,
                "timeframes":   tf_summaries,
                "signals":      all_signals,
                "top_setups":   top_setups,
            }

        except Exception as e:
            logger.error(f"Market overview error: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    return router
