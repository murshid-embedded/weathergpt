"""
main.py — WeatherGPT backend (FastAPI)

Endpoints:
  POST /api/chat            -> conversational query (text in, natural-language reply out)
  GET  /api/forecast        -> raw multi-day forecast JSON (for charts)
  GET  /api/climate-trend   -> raw historical trend JSON (for charts)
  GET  /api/alerts          -> raw current alerts JSON for a location
  WS   /ws/alerts/{location}-> live push of new extreme-weather alerts

Run:
  uvicorn main:app --reload --port 8000

Requires ANTHROPIC_API_KEY in the environment (see .env.example) for /api/chat.
Everything else works with zero API keys (Open-Meteo is free/keyless).
"""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import weather_service as ws
import llm_service
import alerts_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(alerts_engine.poll_loop())
    yield
    task.cancel()


app = FastAPI(title="WeatherGPT", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    sector: str = "general"


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(500, "ANTHROPIC_API_KEY not set on the server. See .env.example.")
    try:
        return await llm_service.answer_query(req.message, req.sector)
    except Exception as e:
        raise HTTPException(500, f"chat failed: {e}")


@app.get("/api/forecast")
async def forecast(location: str = Query(...), days: int = Query(5, ge=1, le=16)):
    try:
        return await ws.get_forecast(location, days)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/climate-trend")
async def climate_trend(location: str = Query(...), years: int = Query(10, ge=1, le=40)):
    try:
        return await ws.get_climate_trend(location, years)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/alerts")
async def alerts(location: str = Query(...)):
    try:
        return await ws.get_alerts_raw(location)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.websocket("/ws/alerts/{location}")
async def ws_alerts(websocket: WebSocket, location: str):
    await websocket.accept()
    await alerts_engine.subscribe(location, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        alerts_engine.unsubscribe(websocket)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
