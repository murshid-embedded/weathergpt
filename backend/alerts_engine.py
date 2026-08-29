"""
alerts_engine.py
Real-time extreme-weather alert dissemination.

- Users/clients "subscribe" a location via the API.
- A background asyncio task polls each subscribed location on an interval
  and pushes any new alert to all WebSocket clients watching that location.
- This demonstrates "faster dissemination of weather information" and
  "extreme weather alerts / early warning dissemination" from the PS,
  without needing a real MQTT/WIS2.0 broker for the demo.

Production note (for your PPT / architecture slide):
  Swap the in-memory `subscriptions` dict + asyncio.sleep loop for:
    - MQTT / WIS2.0 topic-based pub-sub for IMD <-> platform integration
    - A durable queue (Kafka/RabbitMQ) between the poller and notifiers
    - Push channels: WebSocket (app), Telegram/WhatsApp Bot API, SMS (Twilio)
      for rural/offline reach.
"""

import asyncio
import time
from fastapi import WebSocket
import weather_service as ws

POLL_INTERVAL_SECONDS = 300  # 5 min; lower for demo purposes if needed

# location_name -> set of connected websockets
subscriptions: dict[str, set[WebSocket]] = {}
# location_name -> last seen alert signature set (to avoid re-notifying identical alerts)
_last_alert_keys: dict[str, set] = {}


async def subscribe(location: str, ws_client: WebSocket):
    subscriptions.setdefault(location, set()).add(ws_client)


def unsubscribe(ws_client: WebSocket):
    for clients in subscriptions.values():
        clients.discard(ws_client)


async def _broadcast(location: str, payload: dict):
    dead = []
    for client in subscriptions.get(location, set()):
        try:
            await client.send_json(payload)
        except Exception:
            dead.append(client)
    for d in dead:
        subscriptions[location].discard(d)


async def poll_loop():
    """Run forever as a background task; checks every subscribed location."""
    while True:
        locations = list(subscriptions.keys())
        for loc in locations:
            if not subscriptions.get(loc):
                continue
            try:
                result = await ws.get_alerts_raw(loc)
                alerts = result["alerts"]
                keys = {(a["date"], a["type"], a["severity"]) for a in alerts}
                new_keys = keys - _last_alert_keys.get(loc, set())
                if new_keys:
                    new_alerts = [a for a in alerts if (a["date"], a["type"], a["severity"]) in new_keys]
                    await _broadcast(loc, {
                        "type": "alert",
                        "location": result["location"],
                        "alerts": new_alerts,
                        "timestamp": time.time(),
                    })
                _last_alert_keys[loc] = keys
            except Exception as e:
                # Don't crash the loop on a bad location / transient API failure
                print(f"[alerts_engine] poll error for {loc}: {e}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
