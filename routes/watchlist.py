"""
routes/watchlist.py — Watchlist CRUD.

Covers:
  GET    /api/watchlist
  POST   /api/watchlist
  DELETE /api/watchlist/{entry_id}
  PATCH  /api/watchlist/{entry_id}
"""

import json as _json
import logging
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

_SCAN_CACHE_PATH = os.path.join("data", "scan_cache.json")


def create_router(bot, login_required) -> APIRouter:
    router = APIRouter()

    @router.get("/api/watchlist")
    async def api_watchlist_get(request: Request, _=Depends(login_required)):
        """
        Return all watchlist entries enriched with current data from the scan cache.

        Each entry gets:
          current_score  — slash-separated score per timeframe (long/swing/short)
          current_grade  — best grade across timeframes
          current_signal — BUY/SELL if any timeframe fired, else NONE
          current_price  — from the scan cache (long preferred)
        """
        from data.watchlist import load as wl_load

        entries = wl_load()

        cache_by_tf: dict = {}
        if os.path.exists(_SCAN_CACHE_PATH):
            try:
                with open(_SCAN_CACHE_PATH, "r") as f:
                    raw = _json.load(f)
                for tf in ("long", "swing", "short"):
                    if isinstance(raw.get(tf), dict):
                        cache_by_tf[tf] = raw[tf]
            except Exception:
                pass

        enriched = []
        for e in entries:
            row         = dict(e)
            sym         = e.get("symbol", "")
            grade_order = ["A", "B", "C", "D"]

            tf_score_parts = []
            best_grade     = None
            best_signal    = None
            current_price  = None

            for tf in ("long", "swing", "short"):
                cached = cache_by_tf.get(tf, {}).get(sym)
                if cached:
                    sc = cached.get("score")
                    tf_score_parts.append(str(sc) if sc is not None else "—")
                    gr = cached.get("grade")
                    if gr and gr in grade_order:
                        if best_grade is None or grade_order.index(gr) < grade_order.index(best_grade):
                            best_grade = gr
                    sig = cached.get("signal")
                    if sig and sig != "NONE" and best_signal is None:
                        best_signal = sig
                    if tf == "long" and cached.get("price") is not None:
                        current_price = cached.get("price")
                    elif current_price is None and cached.get("price") is not None:
                        current_price = cached.get("price")
                else:
                    tf_score_parts.append("—")

            row["current_score"]  = "/".join(tf_score_parts) if any(p != "—" for p in tf_score_parts) else None
            row["current_grade"]  = best_grade
            row["current_signal"] = best_signal or "NONE"
            row["current_price"]  = current_price
            enriched.append(row)

        return enriched

    @router.post("/api/watchlist")
    async def api_watchlist_add(request: Request, _=Depends(login_required)):
        """Add a symbol snapshot to the watchlist."""
        from data.watchlist import add as wl_add
        body = await request.json()
        try:
            entry = wl_add(
                symbol        = body.get("symbol", ""),
                timeframe     = body.get("timeframe", "long"),
                price_at_add  = body.get("price_at_add"),
                score_at_add  = body.get("score_at_add"),
                grade_at_add  = body.get("grade_at_add"),
                signal_at_add = body.get("signal_at_add"),
                tier1_at_add  = body.get("tier1_at_add"),
                tier2_at_add  = body.get("tier2_at_add"),
                notes         = body.get("notes", ""),
            )
            return entry
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)

    @router.delete("/api/watchlist/{entry_id}")
    async def api_watchlist_remove(entry_id: str, _=Depends(login_required)):
        """Remove a watchlist entry by id."""
        from data.watchlist import remove as wl_remove
        found = wl_remove(entry_id)
        if not found:
            return JSONResponse({"error": "Entry not found"}, status_code=404)
        return {"ok": True}

    @router.patch("/api/watchlist/{entry_id}")
    async def api_watchlist_update(entry_id: str, request: Request, _=Depends(login_required)):
        """Update the notes field for a watchlist entry."""
        from data.watchlist import update_notes as wl_update_notes
        body  = await request.json()
        notes = body.get("notes", "")
        entry = wl_update_notes(entry_id, notes)
        if entry is None:
            return JSONResponse({"error": "Entry not found"}, status_code=404)
        return entry

    return router
