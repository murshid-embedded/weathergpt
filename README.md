# WeatherGPT — Conversational AI for Weather Forecasting, Alerts & Climate Information

A working prototype built for Smart India Hackathon. Natural-language weather
chat, multilingual replies, sector-specific advisories (farmer / aviation /
marine / urban), real-time extreme-weather alerts pushed over WebSocket,
voice input/output, and historical climate-trend charts — all backed by
real NWP model data (no mock numbers).

## 1. What's actually implemented vs. what's "architecture only"

| PS requirement | Status |
|---|---|
| Real-time weather retrieval | ✅ Live via Open-Meteo (free, no key) |
| Natural language querying | ✅ Claude tool-use (`backend/llm_service.py`) |
| NWP model integration (GFS/WRF) | ✅ Real GFS/ICON-blended output via Open-Meteo — see note below |
| Extreme weather alerts / early warning | ✅ Rule-based thresholds + WebSocket push (`alerts_engine.py`) |
| Location-based advisory | ✅ Sector personas: farmer/aviation/marine/urban |
| Multilingual (Indian languages) | ✅ Claude replies in whatever language you ask in |
| Climate trend / historical analysis | ✅ Multi-year archive data + chart |
| Voice interaction | ✅ Browser Web Speech API (STT + TTS) |
| Mobile platform | ✅ as a responsive PWA-style web app (see §5 to go native) |
| MQTT / WIS2.0 / Kubernetes / Postgres at scale | 📐 Architecture only — see §6 |

**Why Open-Meteo instead of running GFS/WRF yourself:** Open-Meteo re-serves
the actual output of GFS/ICON/other national NWP models, updated every run —
so you get *real* model-based forecasts, you just don't have to host a
supercomputer to produce them. This is exactly what a production system
would do too (ingest NWP output, don't recompute it) — say this explicitly
to judges, it's a legitimate engineering decision, not a shortcut you need
to hide.

## 2. Architecture

```
                    ┌─────────────────────────┐
                    │   Frontend (browser)    │
                    │  index.html — chat UI,  │
                    │  voice I/O, alert feed, │
                    │  climate chart          │
                    └───────────┬─────────────┘
                     WebSocket  │  HTTP (fetch)
                                ▼
                    ┌─────────────────────────┐
                    │   FastAPI backend       │
                    │   main.py               │
                    ├─────────────────────────┤
                    │ /api/chat  ──────────┐  │
                    │ /api/forecast        │  │
                    │ /api/climate-trend   │  │
                    │ /api/alerts          │  │
                    │ /ws/alerts/{loc}     │  │
                    └───────────┬───────────┼──┘
                                │           │
                 ┌──────────────┘           └───────────────┐
                 ▼                                           ▼
     ┌───────────────────────┐                 ┌───────────────────────────┐
     │  llm_service.py       │                  │  weather_service.py       │
     │  Claude tool-use:     │◄────tool calls───┤  Open-Meteo wrapper:      │
     │  parses intent,       │────results──────►│  geocode / forecast /     │
     │  writes NL reply in   │                  │  current / historical     │
     │  user's language      │                  │  (GFS/ICON-backed NWP)    │
     └───────────────────────┘                  └───────────────────────────┘
                                                              ▲
                                                              │
                                                 ┌───────────────────────────┐
                                                 │  alerts_engine.py         │
                                                 │  background poll loop →   │
                                                 │  threshold check →        │
                                                 │  WebSocket broadcast      │
                                                 └───────────────────────────┘
```

## 3. Setup

```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...
uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000** — the backend serves the frontend directly.

Get a Claude API key at https://console.anthropic.com — the chat endpoint
needs it; every other endpoint (forecast, alerts, climate) works without
any key at all, since Open-Meteo is free and keyless. That means you can
demo forecasts/alerts/charts even if the API key runs out mid-demo.

## 4. Demo script (for judges)

1. **"Will it rain in Trichy tomorrow?"** — shows real-time NLU + live NWP data.
2. Switch sector to **Farmer**, ask **"Should I irrigate my field this week?"** — shows sector-tailored advisory.
3. Ask the same kind of question **in Tamil** — shows multilingual capability live.
4. Click **Watch** on "Kumbakonam" in the sidebar — shows the WebSocket alert
   feed and the 5-day forecast chart populate.
5. Ask **"How has rainfall in Tamil Nadu changed over the last 10 years?"**
   — shows climate-trend / historical analysis.
6. Tap the 🎙️ mic and speak a query — shows voice accessibility for
   rural/low-literacy users.

## 5. Turning this into "mobile-based" for the PS wording

The frontend is a single responsive HTML file — it already works on mobile
browsers and can be installed as a PWA (add a `manifest.json` + service
worker) with near-zero extra work. If your team wants a native app instead,
wrap `index.html` in a WebView (Flutter/React Native) rather than rebuilding
the UI — same backend, no changes needed.

## 6. Scaling roadmap (put this on a slide, don't build it live)

- **Ingestion**: Replace the polling loop in `alerts_engine.py` with a
  **WIS2.0 / MQTT** subscriber to IMD's real-time bulletin feed, so alerts
  arrive push-based instead of polled.
- **Message bus**: Put a Kafka/RabbitMQ layer between ingestion and
  notification fan-out so WebSocket, Telegram bot, and SMS (Twilio, for
  offline rural reach) notifiers all consume the same alert stream.
- **Storage**: Move from in-memory subscriptions to **PostgreSQL** (user
  preferences, location subscriptions, query logs) — schema is a natural
  fit since it's already relational.
- **Deployment**: Containerize backend + a Postgres + a message broker with
  **Docker Compose** for local dev, **Kubernetes** for horizontal scaling
  the FastAPI pods and the alert poller as separate deployments.
- **IMD integration**: Swap/augment `weather_service.py`'s thresholds and
  raw data with IMD's official data.gov.in warning APIs for
  government-grade accuracy (hooks are marked `TODO(IMD)` in the code).

## 7. File map

```
weathergpt/
├── README.md
├── backend/
│   ├── main.py             # FastAPI app + routes + websocket
│   ├── llm_service.py      # Claude tool-use orchestration (the "GPT")
│   ├── weather_service.py  # Open-Meteo wrapper (geocode/forecast/climate/alerts)
│   ├── alerts_engine.py    # background polling + websocket broadcast
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    └── index.html          # chat UI, voice I/O, alert feed, climate chart
```
