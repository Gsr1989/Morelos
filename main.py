"""
MORELOS — FUSIÓN: panel web (Flask) + bot de Telegram (aiogram) en UN servicio.

Antes eran dos servicios de Render ($14/mes). Ahora uno solo ($7/mes).

  · FastAPI atiende /webhook (async) → Dispatcher de aiogram
  · TODO lo demás se lo pasa a Flask con WSGIMiddleware, sin tocar rutas
  · OJO: el bot tenía su propia /consulta/{folio} en JSON que CHOCABA con la
    del panel (HTML). Se quitó la del bot — gana la página del panel.

Start command en Render:
    gunicorn main:app -k uvicorn.workers.UvicornWorker -w 1 --timeout 120
"""

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

try:
    from a2wsgi import WSGIMiddleware
except ImportError:  # pragma: no cover
    from starlette.middleware.wsgi import WSGIMiddleware

import config_morelos as cfg
import bot_morelos
import panel_morelos


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg.logger.info("=" * 60)
    cfg.logger.info("[SISTEMA] MORELOS FUSIONADO v7.0 — panel + bot en un servicio")
    cfg.logger.info("=" * 60)

    try:
        await bot_morelos.arranque_bot()
    except Exception as e:
        cfg.logger.error(f"[ARRANQUE BOT] {e}")

    try:
        cfg.logger.info(f"[SISTEMA] Siguiente folio: {cfg.leer_siguiente_folio()}")
        cfg.logger.info(f"[SISTEMA] Placa actual: {cfg.placa_actual()}")
    except Exception:
        pass

    cfg.logger.info("[SISTEMA] Panel en /  ·  Webhook en /webhook")
    cfg.logger.info("[SISTEMA] Editor de tablas en /admin/editor")
    cfg.logger.info("[SISTEMA] Ajuste de fechas en /admin/fechas")

    yield

    cfg.logger.info("[CIERRE] Deteniendo servicios...")
    try:
        await bot_morelos.cierre_bot()
    except Exception:
        pass
    cfg.logger.info("[CIERRE] Listo")


app = FastAPI(
    lifespan=lifespan,
    title="MORELOS — Panel + Bot",
    version="7.0",
    docs_url=None,
    redoc_url=None,
)


@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        await bot_morelos.procesar_update(data)
        return {"ok": True}
    except Exception as e:
        cfg.logger.error(f"[WEBHOOK] {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


@app.get("/health")
async def health():
    return {
        "ok":              True,
        "sistema":         "MORELOS FUSIONADO v7.0",
        "panel":           "Flask montado en /",
        "bot":             "aiogram via /webhook",
        "vigencia":        f"{cfg.DIAS_PERMISO} dias",
        "precio":          f"${cfg.PRECIO_PERMISO}",
        "timer_bot":       f"{cfg.HORAS_TIMER_BOT} horas",
        "timers_activos":  len(bot_morelos.timers_activos),
        "siguiente_folio": cfg.leer_siguiente_folio(),
        "placa_actual":    cfg.placa_actual(),
        "editor_tablas":   "/admin/editor",
        "ajuste_fechas":   "/admin/fechas",
    }


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "morelos-fusionado",
            "time": datetime.now(cfg.TZ_MEXICO).isoformat()}


@app.get("/status")
async def status_detail():
    return {
        "sistema":         "MORELOS FUSIONADO v7.0",
        "timers_activos":  len(bot_morelos.timers_activos),
        "folios":          bot_morelos.snapshot_timers(),
        "siguiente_folio": cfg.leer_siguiente_folio(),
        "placa_actual":    cfg.placa_actual(),
        "timestamp":       datetime.now(cfg.TZ_MEXICO).isoformat(),
    }


# Panel Flask al final: atrapa todo lo que no sea /webhook /health /healthz /status
app.mount("/", WSGIMiddleware(panel_morelos.flask_app))


if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
