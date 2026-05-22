"""
routes/pages.py — HTML page routes.

Covers: /, /login, /logout, /scanner, /positions, /backtest, /market, /bot, /watchlist
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from data.settings_store import get_setting

templates = Jinja2Templates(directory="templates")


def create_router(bot, login_required) -> APIRouter:
    router = APIRouter()

    def _password_required() -> bool:
        pw = get_setting("dashboard_password")
        return bool(pw and pw.strip())

    @router.get("/login", response_class=HTMLResponse)
    async def login_get(request: Request):
        if not _password_required():
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @router.post("/login", response_class=HTMLResponse)
    async def login_post(request: Request, password: str = Form(...)):
        stored_pw = get_setting("dashboard_password") or ""
        if password == stored_pw:
            request.session["authenticated"] = True
            return RedirectResponse(url="/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {"error": "Incorrect password."})

    @router.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        if _password_required():
            return RedirectResponse(url="/login", status_code=303)
        return RedirectResponse(url="/", status_code=303)

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request, _=Depends(login_required)):
        return templates.TemplateResponse(request, "dashboard.html", {"active_page": "dashboard"})

    @router.get("/scanner", response_class=HTMLResponse)
    async def scanner_page(request: Request, _=Depends(login_required)):
        return templates.TemplateResponse(request, "scanner.html", {"active_page": "scanner"})

    @router.get("/positions", response_class=HTMLResponse)
    async def positions_page(request: Request, _=Depends(login_required)):
        return templates.TemplateResponse(request, "positions.html", {"active_page": "positions"})

    @router.get("/backtest", response_class=HTMLResponse)
    async def backtest_page(request: Request, _=Depends(login_required)):
        return templates.TemplateResponse(request, "backtest.html", {"active_page": "backtest"})

    @router.get("/market", response_class=HTMLResponse)
    async def market_pulse_page(request: Request, _=Depends(login_required)):
        return templates.TemplateResponse(request, "market.html", {"active_page": "market"})

    @router.get("/bot", response_class=HTMLResponse)
    async def bot_page(request: Request, _=Depends(login_required)):
        return templates.TemplateResponse(request, "bot.html", {"active_page": "bot"})

    @router.get("/watchlist", response_class=HTMLResponse)
    async def watchlist_page(request: Request, _=Depends(login_required)):
        return templates.TemplateResponse(request, "watchlist.html", {"active_page": "watchlist"})

    return router
