"""
The Code Fairy — Astrology API
Calculates birth charts in both tropical and sidereal systems using Kerykeion.

Endpoints:
  POST /chart     — Calculate full birth chart
  GET  /transits  — Get current planetary positions
  GET  /health    — Health check
"""

import os
import logging
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from kerykeion import AstrologicalSubject

# Suppress Kerykeion geonames warnings in production
logging.getLogger().setLevel(logging.ERROR)

app = FastAPI(title="The Code Fairy Astrology API", version="1.0.0")

# Allow all origins for now (lock down in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Sign abbreviation to full name mapping ────
SIGN_MAP = {
    "Ari": "Aries", "Tau": "Taurus", "Gem": "Gemini", "Can": "Cancer",
    "Leo": "Leo", "Vir": "Virgo", "Lib": "Libra", "Sco": "Scorpio",
    "Sag": "Sagittarius", "Cap": "Capricorn", "Aqu": "Aquarius", "Pis": "Pisces",
}

# House name cleanup
HOUSE_MAP = {
    "First_House": 1, "Second_House": 2, "Third_House": 3, "Fourth_House": 4,
    "Fifth_House": 5, "Sixth_House": 6, "Seventh_House": 7, "Eighth_House": 8,
    "Ninth_House": 9, "Tenth_House": 10, "Eleventh_House": 11, "Twelfth_House": 12,
}


# ─── Request/Response Models ───────────────────

class ChartRequest(BaseModel):
    name: str = "User"
    year: int = Field(..., ge=1900, le=2030)
    month: int = Field(..., ge=1, le=12)
    day: int = Field(..., ge=1, le=31)
    hour: int = Field(12, ge=0, le=23)
    minute: int = Field(0, ge=0, le=59)
    city: str = "New York"
    country: str = "US"
    lat: float | None = None
    lng: float | None = None


def extract_planet(planet_obj):
    """Extract clean planet data from a Kerykeion planet object."""
    if planet_obj is None:
        return None
    return {
        "name": planet_obj.name,
        "sign": SIGN_MAP.get(planet_obj.sign, planet_obj.sign),
        "sign_short": planet_obj.sign,
        "degree": round(planet_obj.position, 2),
        "abs_degree": round(planet_obj.abs_pos, 2),
        "house": HOUSE_MAP.get(planet_obj.house, planet_obj.house),
        "retrograde": planet_obj.retrograde,
        "element": planet_obj.element,
        "quality": planet_obj.quality,
    }


def build_chart(subject):
    """Build a clean chart dictionary from an AstrologicalSubject."""
    planets = {}
    planet_names = [
        "sun", "moon", "mercury", "venus", "mars",
        "jupiter", "saturn", "uranus", "neptune", "pluto", "chiron",
    ]

    for name in planet_names:
        obj = getattr(subject, name, None)
        if obj:
            planets[name] = extract_planet(obj)

    # Try to get North Node
    north_node = getattr(subject, "mean_north_lunar_node", None)
    if north_node is None:
        # Fallback for older versions
        try:
            north_node = getattr(subject, "mean_node", None)
        except:
            pass
    if north_node:
        planets["north_node"] = extract_planet(north_node)

    return planets


# ─── Endpoints ─────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "the-code-fairy-api", "version": "1.0.0"}


@app.post("/chart")
def calculate_chart(req: ChartRequest):
    """
    Calculate a full birth chart in both tropical and sidereal systems.
    Returns planet positions, signs, degrees, houses, and retrograde status.
    """
    try:
        # Tropical chart (Western astrology — "The Surface")
        tropical = AstrologicalSubject(
            req.name, req.year, req.month, req.day, req.hour, req.minute,
            req.city, req.country,
            zodiac_type="Tropic",
        )

        # Sidereal chart (Vedic astrology — "The Depths")
        sidereal = AstrologicalSubject(
            req.name, req.year, req.month, req.day, req.hour, req.minute,
            req.city, req.country,
            zodiac_type="Sidereal",
            sidereal_mode="LAHIRI",
        )

        tropical_chart = build_chart(tropical)
        sidereal_chart = build_chart(sidereal)

        return {
            "success": True,
            "birth_data": {
                "name": req.name,
                "date": f"{req.year}-{req.month:02d}-{req.day:02d}",
                "time": f"{req.hour:02d}:{req.minute:02d}",
                "city": req.city,
                "country": req.country,
            },
            "tropical": tropical_chart,
            "sidereal": sidereal_chart,
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Chart calculation failed: {str(e)}")


@app.get("/transits")
def get_transits():
    """
    Get current planetary positions (transits) for right now.
    """
    try:
        now = datetime.now(timezone.utc)

        tropical = AstrologicalSubject(
            "Transit", now.year, now.month, now.day, now.hour, now.minute,
            "Greenwich", "GB",
            zodiac_type="Tropic",
        )

        sidereal = AstrologicalSubject(
            "Transit", now.year, now.month, now.day, now.hour, now.minute,
            "Greenwich", "GB",
            zodiac_type="Sidereal",
            sidereal_mode="LAHIRI",
        )

        return {
            "success": True,
            "timestamp": now.isoformat(),
            "tropical": build_chart(tropical),
            "sidereal": build_chart(sidereal),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transit calculation failed: {str(e)}")


# ─── Run ───────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
