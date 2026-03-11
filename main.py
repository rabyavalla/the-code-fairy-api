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
    try:
        return {
            "name": getattr(planet_obj, 'name', 'Unknown'),
            "sign": SIGN_MAP.get(getattr(planet_obj, 'sign', ''), getattr(planet_obj, 'sign', '')),
            "sign_short": getattr(planet_obj, 'sign', ''),
            "degree": round(getattr(planet_obj, 'position', 0), 2),
            "abs_degree": round(getattr(planet_obj, 'abs_pos', 0), 2),
            "house": HOUSE_MAP.get(getattr(planet_obj, 'house', ''), getattr(planet_obj, 'house', None)),
            "retrograde": getattr(planet_obj, 'retrograde', False),
            "element": getattr(planet_obj, 'element', ''),
            "quality": getattr(planet_obj, 'quality', ''),
        }
    except Exception:
        return None


def extract_house_cusp(subject, house_attr, display_name):
    """Extract a house cusp (ASC, MC, etc.) as a planet-like dict."""
    try:
        obj = getattr(subject, house_attr, None)
        if obj is None:
            return None
        sign_raw = getattr(obj, 'sign', None)
        sign = SIGN_MAP.get(sign_raw, sign_raw) if sign_raw else None
        if not sign:
            return None
        return {
            "name": display_name,
            "sign": sign,
            "sign_short": sign_raw,
            "degree": round(getattr(obj, 'position', 0), 2),
            "abs_degree": round(getattr(obj, 'abs_pos', 0), 2),
            "house": HOUSE_MAP.get(house_attr, None),
            "retrograde": False,
            "element": getattr(obj, 'element', ''),
            "quality": getattr(obj, 'quality', ''),
        }
    except Exception:
        return None


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

    # Try to get North Node (multiple attribute name attempts for different Kerykeion versions)
    north_node = None
    for attr in ["mean_north_lunar_node", "true_north_lunar_node", "mean_node", "true_node"]:
        try:
            north_node = getattr(subject, attr, None)
            if north_node and hasattr(north_node, 'sign'):
                break
            north_node = None
        except Exception:
            north_node = None
    if north_node:
        nn = extract_planet(north_node)
        if nn:
            planets["north_node"] = nn

    # Try to get South Node directly from Kerykeion
    south_node = None
    for attr in ["mean_south_lunar_node", "true_south_lunar_node", "mean_south_node", "true_south_node"]:
        try:
            south_node = getattr(subject, attr, None)
            if south_node and hasattr(south_node, 'sign'):
                break
            south_node = None
        except Exception:
            south_node = None
    if south_node:
        sn = extract_planet(south_node)
        if sn:
            planets["south_node"] = sn
    elif "north_node" in planets:
        # Calculate South Node from North Node (180 degrees opposite)
        nn_data = planets["north_node"]
        sn_abs = (nn_data["abs_degree"] + 180) % 360
        sn_degree_in_sign = sn_abs % 30
        sign_index = int(sn_abs // 30)
        signs_ordered = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
                         "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]
        shorts = ["Ari", "Tau", "Gem", "Can", "Leo", "Vir", "Lib", "Sco", "Sag", "Cap", "Aqu", "Pis"]
        elements = ["Fire", "Earth", "Air", "Water", "Fire", "Earth", "Air", "Water", "Fire", "Earth", "Air", "Water"]
        qualities = ["Cardinal", "Fixed", "Mutable", "Cardinal", "Fixed", "Mutable",
                     "Cardinal", "Fixed", "Mutable", "Cardinal", "Fixed", "Mutable"]
        planets["south_node"] = {
            "name": "South Node",
            "sign": signs_ordered[sign_index],
            "sign_short": shorts[sign_index],
            "degree": round(sn_degree_in_sign, 2),
            "abs_degree": round(sn_abs, 2),
            "house": None,
            "retrograde": False,
            "element": elements[sign_index],
            "quality": qualities[sign_index],
        }

    # House cusps — Ascendant, IC, Descendant, Midheaven
    asc = extract_house_cusp(subject, "first_house", "Ascendant")
    if asc:
        planets["ascendant"] = asc
    ic = extract_house_cusp(subject, "fourth_house", "IC")
    if ic:
        planets["ic"] = ic
    dsc = extract_house_cusp(subject, "seventh_house", "Descendant")
    if dsc:
        planets["descendant"] = dsc
    mc = extract_house_cusp(subject, "tenth_house", "Midheaven")
    if mc:
        planets["midheaven"] = mc

    return planets


# ─── Endpoints ─────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "the-code-fairy-api", "version": "1.1.0"}


@app.get("/debug-attrs")
def debug_attrs():
    """Debug: show available attributes for node detection."""
    try:
        s = AstrologicalSubject("Debug", 2000, 1, 1, 12, 0, "New York", "US", zodiac_type="Tropic")
        node_attrs = [a for a in dir(s) if 'node' in a.lower() or 'lunar' in a.lower()]
        results = {}
        for a in node_attrs:
            try:
                obj = getattr(s, a, None)
                if obj and hasattr(obj, 'sign'):
                    results[a] = {"sign": obj.sign, "position": round(obj.position, 2)}
                elif obj and not callable(obj):
                    results[a] = str(obj)[:100]
            except Exception as e:
                results[a] = f"error: {str(e)}"
        return {"node_attrs": node_attrs, "values": results}
    except Exception as e:
        return {"error": str(e)}


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
