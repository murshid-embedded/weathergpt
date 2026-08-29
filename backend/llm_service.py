"""
llm_service.py
The "conversational AI" brain of WeatherGPT.

Uses Claude's tool-use (function calling) to:
  1. Understand the user's natural-language query (any language, incl. Tamil/
     Hindi/other Indian languages) and figure out intent + parameters.
  2. Call the right weather_service function.
  3. Turn the raw JSON weather data into a natural, conversational reply --
     in the SAME language the user asked in, and tailored to a sector
     persona (general / farmer / aviation / marine / urban planner) if given.

This is intentionally a thin orchestration layer: all meteorological truth
comes from weather_service.py (Open-Meteo / NWP-backed). The LLM never
invents numbers -- it only explains numbers it was given via tool results.
"""

import os
import json
import httpx
import weather_service as ws

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
API_URL = "https://api.anthropic.com/v1/messages"

TOOLS = [
    {
        "name": "get_current_weather",
        "description": "Get current weather conditions and today's summary for a location.",
        "input_schema": {
            "type": "object",
            "properties": {"location": {"type": "string", "description": "City/place name, e.g. 'Trichy' or 'Kumbakonam'"}},
            "required": ["location"],
        },
    },
    {
        "name": "get_forecast",
        "description": "Get a multi-day weather forecast (up to 16 days) for a location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string"},
                "days": {"type": "integer", "description": "Number of days to forecast, default 5"},
            },
            "required": ["location"],
        },
    },
    {
        "name": "get_climate_trend",
        "description": "Get historical yearly climate trend data (temperature, rainfall) for a location, for research/climate-analysis questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string"},
                "years": {"type": "integer", "description": "How many years back to analyze, default 10"},
            },
            "required": ["location"],
        },
    },
    {
        "name": "get_alerts",
        "description": "Check for extreme weather alerts (heavy rain, heatwave, high wind) over the next 3 days for a location.",
        "input_schema": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    },
]

TOOL_IMPL = {
    "get_current_weather": lambda args: ws.get_current_weather(args["location"]),
    "get_forecast": lambda args: ws.get_forecast(args["location"], args.get("days", 5)),
    "get_climate_trend": lambda args: ws.get_climate_trend(args["location"], args.get("years", 10)),
    "get_alerts": lambda args: ws.get_alerts_raw(args["location"]),
}

SECTOR_PERSONAS = {
    "general": "Answer as a helpful general-purpose weather assistant for the public.",
    "farmer": "Answer as an agricultural weather advisor. Focus on irrigation timing, "
              "sowing/harvest suitability, and crop-relevant risk (excess rain, heat stress, wind damage).",
    "aviation": "Answer as an aviation weather briefer. Focus on visibility, wind shear risk, "
                "crosswind components, thunderstorm/convective activity, and ceiling conditions.",
    "marine": "Answer as a marine/fisherfolk weather advisor. Focus on wave height risk (infer qualitatively "
              "from wind speed), wind direction for return-to-shore timing, and storm risk.",
    "urban": "Answer as an urban planning / smart-city weather advisor. Focus on flooding/drainage risk, "
             "heat-island impact, and infrastructure stress from extreme conditions.",
}

SYSTEM_PROMPT = """You are WeatherGPT, a conversational AI weather assistant built for the Smart India \
Hackathon problem statement on weather intelligence. You have access to tools backed by real, \
live NWP-model weather data (Open-Meteo, which blends GFS/ICON model output) -- never invent \
weather numbers yourself; always call a tool to get real data first.

Rules:
- ALWAYS detect the language the user wrote in and reply in that SAME language \
(English, Tamil, Hindi, or any other Indian language). If they mix languages, mirror their mix.
- Keep replies concise, clear, and actionable -- this is for quick decision-making, not essays.
- If the query implies a sector (farming, flight/aviation, fishing/marine, city planning), \
tailor the advice using that lens even if not explicitly asked.
- If a tool call fails (e.g. unknown location), apologize briefly and ask for clarification -- \
never fabricate a fallback answer.
- For alert-worthy conditions, be clear about severity and suggest one concrete precaution.
"""


async def _call_claude(messages: list) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            API_URL,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 1024,
                "system": SYSTEM_PROMPT,
                "tools": TOOLS,
                "messages": messages,
            },
        )
        r.raise_for_status()
        return r.json()


async def answer_query(user_text: str, sector: str = "general") -> dict:
    """
    Full round trip: user text -> Claude decides tool(s) -> we execute ->
    Claude writes the natural-language, language-matched, sector-tailored reply.
    Returns {"reply": str, "raw_data": [...]} so the frontend can also render
    charts/cards from raw_data if useful.
    """
    persona_hint = SECTOR_PERSONAS.get(sector, SECTOR_PERSONAS["general"])
    messages = [{"role": "user", "content": f"[Advisory context: {persona_hint}]\n\n{user_text}"}]
    raw_data_collected = []

    for _ in range(4):  # allow a few tool-use round trips
        result = await _call_claude(messages)
        stop_reason = result.get("stop_reason")
        content = result["content"]
        messages.append({"role": "assistant", "content": content})

        if stop_reason != "tool_use":
            text_parts = [b["text"] for b in content if b["type"] == "text"]
            return {"reply": "\n".join(text_parts).strip(), "raw_data": raw_data_collected}

        tool_results = []
        for block in content:
            if block["type"] != "tool_use":
                continue
            name, args, tool_id = block["name"], block["input"], block["id"]
            try:
                data = await TOOL_IMPL[name](args)
                raw_data_collected.append({"tool": name, "args": args, "data": data})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": json.dumps(data),
                })
            except Exception as e:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": json.dumps({"error": str(e)}),
                    "is_error": True,
                })
        messages.append({"role": "user", "content": tool_results})

    return {"reply": "Sorry, I couldn't complete that request in time. Please try again.", "raw_data": raw_data_collected}
