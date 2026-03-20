"""
The Code Fairy — Astrology API
Calculates birth charts in both tropical and sidereal systems using Kerykeion.

Endpoints:
  POST /chart              — Calculate full birth chart
  GET  /transits           — Get current planetary positions
  POST /transit-aspects    — Get transit aspects to natal chart
  POST /moon-forecast      — Get personalized moon forecast
  POST /mood               — Log mood data
  POST /cycles             — Get active planetary cycles
  GET  /forecast/retrogrades  — Upcoming retrogrades (next 90 days)
  GET  /forecast/eclipses     — Upcoming eclipses (next 12 months)
  POST /forecast/aspects      — Major upcoming aspects (next 90 days)
  POST /forecast/personal     — Personalized cosmic forecast
  GET  /forecast/lunar-phases — Current moon phase + upcoming
  GET  /forecast/ingresses    — Upcoming sign changes
  GET  /forecast/cosmic-news  — Current events correlated with active transits
  POST /fairy/ask             — The Code Fairy agent (Beca's voice, chart-aware AI)
  GET  /health                — Health check
"""

import os
import json
import hashlib
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from urllib.request import urlopen, Request
from urllib.error import URLError
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from kerykeion import AstrologicalSubject

# Suppress Kerykeion geonames warnings in production
logging.getLogger().setLevel(logging.ERROR)

app = FastAPI(title="The Code Fairy Astrology API", version="2.0.0")

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

# Average daily motion (degrees per day) for major planets
PLANET_DAILY_MOTION = {
    "Sun": 1.0,
    "Moon": 13.2,
    "Mercury": 1.0,
    "Venus": 1.0,
    "Mars": 0.5,
    "Jupiter": 0.083,
    "Saturn": 0.033,
    "Uranus": 0.014,
    "Neptune": 0.003,
    "Pluto": 0.002,
    "Chiron": 0.15,
}

# Moon phases enum
MOON_PHASES = [
    "new moon", "waxing crescent", "first quarter", "waxing gibbous",
    "full moon", "waning gibbous", "last quarter", "waning crescent",
]


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
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)


class MoodEntry(BaseModel):
    user_id: str
    date: str  # ISO date
    emotions: List[str]  # list of selected emotions
    notes: str = ""
    moon_sign: str = ""
    moon_house: int = 0


def format_degrees(position):
    """Convert decimal degrees to degrees and minutes format.

    Args:
        position: Decimal degree value (e.g., 23.45)

    Returns:
        dict with degree_whole (int), minute (int), and degree_formatted (str)
    """
    degree_whole = int(position)
    minute = int((position % 1) * 60)
    degree_formatted = f"{degree_whole}°{minute}'"
    return {
        "degree_whole": degree_whole,
        "minute": minute,
        "degree_formatted": degree_formatted,
    }


def extract_planet(planet_obj):
    """Extract clean planet data from a Kerykeion planet object.

    Includes degree + minutes format (degree_whole, minute, degree_formatted)
    while maintaining backward compatibility with existing degree fields.
    """
    if planet_obj is None:
        return None
    try:
        position = getattr(planet_obj, 'position', 0)
        abs_pos = getattr(planet_obj, 'abs_pos', 0)

        base_data = {
            "name": getattr(planet_obj, 'name', 'Unknown'),
            "sign": SIGN_MAP.get(getattr(planet_obj, 'sign', ''), getattr(planet_obj, 'sign', '')),
            "sign_short": getattr(planet_obj, 'sign', ''),
            "degree": round(position, 2),  # Backward compatibility
            "abs_degree": round(abs_pos, 2),  # Backward compatibility
            "house": HOUSE_MAP.get(getattr(planet_obj, 'house', ''), getattr(planet_obj, 'house', None)),
            "retrograde": getattr(planet_obj, 'retrograde', False),
            "element": getattr(planet_obj, 'element', ''),
            "quality": getattr(planet_obj, 'quality', ''),
        }

        # Add degree + minutes format
        degree_info = format_degrees(position)
        base_data.update(degree_info)

        return base_data
    except Exception:
        return None


def extract_house_cusp(subject, house_attr, display_name):
    """Extract a house cusp (ASC, MC, etc.) as a planet-like dict.

    Includes degree + minutes format for consistency with extract_planet.
    """
    try:
        obj = getattr(subject, house_attr, None)
        if obj is None:
            return None
        sign_raw = getattr(obj, 'sign', None)
        sign = SIGN_MAP.get(sign_raw, sign_raw) if sign_raw else None
        if not sign:
            return None

        position = getattr(obj, 'position', 0)
        abs_pos = getattr(obj, 'abs_pos', 0)

        base_data = {
            "name": display_name,
            "sign": sign,
            "sign_short": sign_raw,
            "degree": round(position, 2),  # Backward compatibility
            "abs_degree": round(abs_pos, 2),  # Backward compatibility
            "house": HOUSE_MAP.get(house_attr, None),
            "retrograde": False,
            "element": getattr(obj, 'element', ''),
            "quality": getattr(obj, 'quality', ''),
        }

        # Add degree + minutes format
        degree_info = format_degrees(position)
        base_data.update(degree_info)

        return base_data
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

        degree_info = format_degrees(sn_degree_in_sign)
        planets["south_node"] = {
            "name": "South Node",
            "sign": signs_ordered[sign_index],
            "sign_short": shorts[sign_index],
            "degree": round(sn_degree_in_sign, 2),
            "abs_degree": round(sn_abs, 2),
            "degree_whole": degree_info["degree_whole"],
            "minute": degree_info["minute"],
            "degree_formatted": degree_info["degree_formatted"],
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

    # Get all 12 house cusps for chart rendering
    house_cusps = get_all_house_cusps(subject)

    # Return planets as top-level keys + house_cusps as a separate key
    result = dict(planets)
    result["_house_cusps"] = house_cusps
    return result


# ─── Helper Functions for New Endpoints ────────

def get_all_house_cusps(subject):
    """Extract all 12 house cusps from an AstrologicalSubject.

    Returns a dict: {1: abs_pos, 2: abs_pos, ..., 12: abs_pos}
    """
    house_attrs = [
        "first_house", "second_house", "third_house", "fourth_house",
        "fifth_house", "sixth_house", "seventh_house", "eighth_house",
        "ninth_house", "tenth_house", "eleventh_house", "twelfth_house",
    ]
    cusps = {}
    for i, attr in enumerate(house_attrs, 1):
        try:
            obj = getattr(subject, attr, None)
            if obj and hasattr(obj, 'abs_pos'):
                cusps[i] = getattr(obj, 'abs_pos', 0)
        except Exception:
            pass
    return cusps


def get_planet_house(planet_abs_degree, house_cusps):
    """Determine which house a planet falls into based on absolute degree.

    Args:
        planet_abs_degree: Absolute degree (0-360)
        house_cusps: Dict {1: cusp_degree, 2: cusp_degree, ...}

    Returns:
        House number (1-12) or None
    """
    if not house_cusps:
        return None

    for house in range(1, 13):
        cusp_start = house_cusps.get(house, 0)
        cusp_end = house_cusps.get(house + 1 if house < 12 else 1, 0)

        # Handle wrapping at 360 degrees
        if house == 12:
            cusp_end = house_cusps.get(1, 0)
            if cusp_end < cusp_start:
                # House 12 wraps around
                if planet_abs_degree >= cusp_start or planet_abs_degree < cusp_end:
                    return 12
            else:
                if cusp_start <= planet_abs_degree < cusp_end:
                    return 12
        else:
            if cusp_end < cusp_start:
                # This house wraps around 0 degrees
                if planet_abs_degree >= cusp_start or planet_abs_degree < cusp_end:
                    return house
            else:
                if cusp_start <= planet_abs_degree < cusp_end:
                    return house

    return None


def calculate_aspect_orb(planet1_degree, planet2_degree, aspect_angle):
    """Calculate the orb (difference) between a theoretical aspect and actual positions.

    Args:
        planet1_degree: Absolute degree of first planet
        planet2_degree: Absolute degree of second planet
        aspect_angle: Target aspect angle (0, 60, 90, 120, 180)

    Returns:
        Tuple: (orb_value, applying_flag)
        - orb_value: Absolute difference in degrees
        - applying_flag: True if aspect is applying (planets moving closer)
    """
    # Calculate angular difference
    diff = abs(planet1_degree - planet2_degree)
    if diff > 180:
        diff = 360 - diff

    # Calculate how far from exact aspect
    orb = abs(diff - aspect_angle)

    # For simplicity, assume aspect is applying if difference is less than aspect angle
    applying = diff < aspect_angle

    return round(orb, 2), applying


def get_major_aspects(transit_planet_degree, natal_planet_degree, transit_name, natal_name):
    """Check for major aspects between transit and natal planet.

    Returns list of aspect dicts.
    """
    aspects = []
    aspect_angles = [0, 60, 90, 120, 180]  # Conjunction, sextile, square, trine, opposition
    aspect_names = ["conjunction", "sextile", "square", "trine", "opposition"]

    # Determine orb based on planet type
    if transit_name in ["Sun", "Moon"] or natal_name in ["Sun", "Moon"]:
        orb_limit = 8.0
    else:
        orb_limit = 6.0

    for angle, aspect_name in zip(aspect_angles, aspect_names):
        orb, applying = calculate_aspect_orb(transit_planet_degree, natal_planet_degree, angle)
        if orb <= orb_limit:
            aspects.append({
                "aspect_type": aspect_name,
                "orb": orb,
                "applying": applying,
            })

    return aspects


def calculate_moon_phase(sun_degree, moon_degree):
    """Calculate moon phase from sun-moon angle.

    Returns one of: new moon, waxing crescent, first quarter, waxing gibbous,
                    full moon, waning gibbous, last quarter, waning crescent
    """
    # Calculate angle from sun to moon
    angle = (moon_degree - sun_degree) % 360

    # Divide into 8 phases
    phase_index = int((angle / 360) * 8) % 8
    return MOON_PHASES[phase_index]


def estimate_next_aspect_date(natal_position, transit_position, planet_name, aspect_angle=0):
    """Estimate when transit planet will reach the exact aspect to natal position.

    Args:
        natal_position: Natal planet absolute degree
        transit_position: Current transit planet absolute degree
        planet_name: Name of transit planet
        aspect_angle: Target aspect angle (0=conjunction, 90=square, 180=opposition)

    Returns:
        ISO format date string
    """
    # Target degree = natal position + aspect angle (where transit needs to be)
    target_degree = (natal_position + aspect_angle) % 360

    # Calculate angular distance remaining to reach target
    distance = (target_degree - transit_position) % 360

    # If very close (< 5 degrees ahead), the aspect may have just passed
    # Check if the shorter path is backwards (already passed)
    if distance > 350:
        distance = 360 - distance  # It just passed, estimate when it last was exact
        # Return a recent past date or just show "recent"
        daily_motion = PLANET_DAILY_MOTION.get(planet_name, 0.5)
        if daily_motion == 0:
            daily_motion = 0.5
        days_ago = distance / daily_motion
        past_date = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return past_date.date().isoformat()

    # Get daily motion
    daily_motion = PLANET_DAILY_MOTION.get(planet_name, 0.5)
    if daily_motion == 0:
        daily_motion = 0.5

    # Calculate days until exact
    days_remaining = distance / daily_motion

    # Cap at reasonable maximum (don't show dates centuries away)
    max_days = 365 * 30  # 30 years max
    if days_remaining > max_days:
        return None

    # Get future date
    future_date = datetime.now(timezone.utc) + timedelta(days=days_remaining)
    return future_date.date().isoformat()


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

        # Sidereal chart — "The Constellations"
        # Using FAGAN_BRADLEY ayanamsa which matches standard Western sidereal calculations
        sidereal = AstrologicalSubject(
            req.name, req.year, req.month, req.day, req.hour, req.minute,
            req.city, req.country,
            zodiac_type="Sidereal",
            sidereal_mode="FAGAN_BRADLEY",
        )

        tropical_chart = build_chart(tropical)
        sidereal_chart = build_chart(sidereal)

        # Extract house cusps for chart rendering
        tropical_cusps = tropical_chart.pop("_house_cusps", {})
        sidereal_cusps = sidereal_chart.pop("_house_cusps", {})

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
            "house_cusps": {
                "tropical": {str(k): v for k, v in tropical_cusps.items()},
                "sidereal": {str(k): v for k, v in sidereal_cusps.items()},
            },
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
            sidereal_mode="FAGAN_BRADLEY",
        )

        tropical_chart = build_chart(tropical)
        sidereal_chart = build_chart(sidereal)

        # Remove internal house cusps from transit response
        tropical_chart.pop("_house_cusps", None)
        sidereal_chart.pop("_house_cusps", None)

        return {
            "success": True,
            "timestamp": now.isoformat(),
            "tropical": tropical_chart,
            "sidereal": sidereal_chart,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transit calculation failed: {str(e)}")


@app.post("/transit-aspects")
def get_transit_aspects(req: ChartRequest):
    """
    Calculate how current transits aspect the user's natal chart.

    Returns:
    - Transit planets with their houses and aspects to natal planets
    """
    try:
        # Calculate natal chart (tropical)
        natal = AstrologicalSubject(
            req.name, req.year, req.month, req.day, req.hour, req.minute,
            req.city, req.country,
            zodiac_type="Tropic",
        )

        # Calculate current transits
        now = datetime.now(timezone.utc)
        transits = AstrologicalSubject(
            "Transit", now.year, now.month, now.day, now.hour, now.minute,
            "Greenwich", "GB",
            zodiac_type="Tropic",
        )

        # Get all 12 house cusps from natal chart
        natal_houses = get_all_house_cusps(natal)

        # List of planets to check
        planet_names = [
            "sun", "moon", "mercury", "venus", "mars",
            "jupiter", "saturn", "uranus", "neptune", "pluto",
        ]

        results = []

        # For each transit planet
        for planet_name in planet_names:
            try:
                transit_planet = getattr(transits, planet_name, None)
                natal_planet = getattr(natal, planet_name, None)

                if not transit_planet or not hasattr(transit_planet, 'abs_pos'):
                    continue

                transit_degree = getattr(transit_planet, 'abs_pos', 0)
                transit_sign = SIGN_MAP.get(getattr(transit_planet, 'sign', ''), '')
                transit_pos = getattr(transit_planet, 'position', 0)

                # Determine which house this transit planet is in
                transit_house = get_planet_house(transit_degree, natal_houses)

                # Format the transit degree
                degree_info = format_degrees(transit_pos)
                transit_degree_formatted = degree_info["degree_formatted"]

                # Check aspects with all natal planets
                aspects = []
                for natal_pname in planet_names:
                    try:
                        n_planet = getattr(natal, natal_pname, None)
                        if not n_planet or not hasattr(n_planet, 'abs_pos'):
                            continue

                        natal_degree = getattr(n_planet, 'abs_pos', 0)
                        natal_sign = SIGN_MAP.get(getattr(n_planet, 'sign', ''), '')

                        planet_aspects = get_major_aspects(
                            transit_degree, natal_degree,
                            transit_planet.name, n_planet.name
                        )

                        for aspect in planet_aspects:
                            aspect["natal_planet"] = n_planet.name
                            aspect["natal_sign"] = natal_sign
                            aspects.append(aspect)

                    except Exception:
                        continue

                results.append({
                    "transit_planet": transit_planet.name,
                    "transit_sign": transit_sign,
                    "transit_degree": transit_degree_formatted,
                    "natal_house": transit_house,
                    "aspects": aspects,
                })

            except Exception:
                continue

        return {
            "success": True,
            "birth_data": {
                "name": req.name,
                "date": f"{req.year}-{req.month:02d}-{req.day:02d}",
            },
            "transits": results,
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Transit aspects calculation failed: {str(e)}")


@app.post("/moon-forecast")
def get_moon_forecast(req: ChartRequest):
    """
    Get personalized moon forecast for a user based on natal data.

    Returns:
    - Current transit moon info
    - Natal moon info
    - House transit moon is in
    - Aspects to natal planets
    - Current moon phase
    """
    try:
        # Calculate natal chart
        natal = AstrologicalSubject(
            req.name, req.year, req.month, req.day, req.hour, req.minute,
            req.city, req.country,
            zodiac_type="Tropic",
        )

        # Calculate current transits
        now = datetime.now(timezone.utc)
        transits = AstrologicalSubject(
            "Transit", now.year, now.month, now.day, now.hour, now.minute,
            "Greenwich", "GB",
            zodiac_type="Tropic",
        )

        # Get house cusps
        natal_houses = get_all_house_cusps(natal)

        # Extract moon info
        transit_moon = transits.moon
        natal_moon = natal.moon

        if not transit_moon or not natal_moon:
            raise ValueError("Moon position not found")

        # Transit moon details
        transit_moon_pos = getattr(transit_moon, 'position', 0)
        transit_moon_abs = getattr(transit_moon, 'abs_pos', 0)
        transit_moon_sign = SIGN_MAP.get(getattr(transit_moon, 'sign', ''), '')
        transit_degree_info = format_degrees(transit_moon_pos)

        # Natal moon details
        natal_moon_pos = getattr(natal_moon, 'position', 0)
        natal_moon_abs = getattr(natal_moon, 'abs_pos', 0)
        natal_moon_sign = SIGN_MAP.get(getattr(natal_moon, 'sign', ''), '')
        natal_house = HOUSE_MAP.get(getattr(natal_moon, 'house', ''), getattr(natal_moon, 'house', None))
        natal_degree_info = format_degrees(natal_moon_pos)

        # Which house is transit moon in
        transit_moon_house = get_planet_house(transit_moon_abs, natal_houses)

        # Aspects with natal planets
        aspects = []
        planet_names = [
            "sun", "mercury", "venus", "mars",
            "jupiter", "saturn", "uranus", "neptune", "pluto",
        ]

        for planet_name in planet_names:
            try:
                n_planet = getattr(natal, planet_name, None)
                if not n_planet or not hasattr(n_planet, 'abs_pos'):
                    continue

                natal_degree = getattr(n_planet, 'abs_pos', 0)
                natal_sign = SIGN_MAP.get(getattr(n_planet, 'sign', ''), '')

                # Use 8° orb for moon aspects
                planet_aspects = get_major_aspects(transit_moon_abs, natal_degree, "Moon", n_planet.name)

                for aspect in planet_aspects:
                    aspect["natal_planet"] = n_planet.name
                    aspect["natal_sign"] = natal_sign
                    aspects.append(aspect)

            except Exception:
                continue

        # Calculate moon phase from sun-moon angle
        transit_sun = transits.sun
        transit_sun_abs = getattr(transit_sun, 'abs_pos', 0) if transit_sun else 0
        moon_phase = calculate_moon_phase(transit_sun_abs, transit_moon_abs)

        return {
            "success": True,
            "birth_data": {
                "name": req.name,
                "date": f"{req.year}-{req.month:02d}-{req.day:02d}",
            },
            "current_moon": {
                "sign": transit_moon_sign,
                "degree": transit_degree_info["degree_formatted"],
                "degree_whole": transit_degree_info["degree_whole"],
                "minute": transit_degree_info["minute"],
            },
            "natal_moon": {
                "sign": natal_moon_sign,
                "degree": natal_degree_info["degree_formatted"],
                "degree_whole": natal_degree_info["degree_whole"],
                "minute": natal_degree_info["minute"],
                "house": natal_house,
            },
            "transit_moon_house": transit_moon_house,
            "aspects": aspects,
            "moon_phase": moon_phase,
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Moon forecast calculation failed: {str(e)}")


@app.post("/mood")
async def log_mood(entry: MoodEntry):
    """
    Log mood data. Validates and returns the entry.

    In the future, this will be wired to Supabase for persistent storage.
    For now, returns the validated data.
    """
    try:
        return {
            "success": True,
            "entry": entry.model_dump(),
            "message": "Mood logged successfully. Future versions will persist to database.",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Mood logging failed: {str(e)}")


@app.post("/cycles")
def get_planetary_cycles(req: ChartRequest):
    """
    Calculate active and upcoming planetary cycles for a user.

    Key cycles:
    - Saturn Return (Saturn conjunct natal Saturn, ~29.5 year cycle)
    - Jupiter Return (Jupiter conjunct natal Jupiter, ~12 year cycle)
    - Saturn Square (Saturn square natal Saturn)
    - Saturn Opposition (Saturn opposition natal Saturn)
    - Chiron Return (~50 years)
    - Uranus Opposition (~42 years)
    - Neptune Square (~41 years)
    - Pluto Square (~varies)

    Returns status: "active", "approaching", "completed", or "distant"
    """
    try:
        # Calculate natal chart
        natal = AstrologicalSubject(
            req.name, req.year, req.month, req.day, req.hour, req.minute,
            req.city, req.country,
            zodiac_type="Tropic",
        )

        # Calculate current transits
        now = datetime.now(timezone.utc)
        transits = AstrologicalSubject(
            "Transit", now.year, now.month, now.day, now.hour, now.minute,
            "Greenwich", "GB",
            zodiac_type="Tropic",
        )

        cycles_to_check = [
            # (natal_planet_name, transit_planet_name, aspect_type, orb_active, orb_approaching, description)
            # Inner/social planets: tighter orbs
            ("saturn", "saturn", "conjunction", 3.0, 8.0, "A major life restructuring cycle occurring approximately every 29.5 years."),
            ("jupiter", "jupiter", "conjunction", 3.0, 8.0, "A growth and expansion cycle occurring approximately every 12 years."),
            ("saturn", "saturn", "square", 3.0, 7.0, "A challenging learning phase occurring approximately every 7.4 years."),
            ("saturn", "saturn", "opposition", 3.0, 7.0, "A culmination of Saturn's lessons, occurring approximately every 14-15 years."),
            ("chiron", "chiron", "conjunction", 5.0, 10.0, "A profound healing and integration cycle occurring approximately every 50 years."),
            # Outer planets: much wider orbs (they move extremely slowly, cycles are felt for years)
            ("uranus", "uranus", "opposition", 8.0, 15.0, "A period of radical change and liberation occurring around age 38-44."),
            ("neptune", "neptune", "square", 8.0, 15.0, "A dissolution and spiritual awakening phase occurring around age 40-42."),
            ("pluto", "pluto", "square", 8.0, 15.0, "A period of deep transformation — timing varies by generation (age 36-60+)."),
        ]

        cycles = []

        for natal_pname, transit_pname, aspect_type, orb_active, orb_approaching, description in cycles_to_check:
            try:
                natal_planet = getattr(natal, natal_pname, None)
                transit_planet = getattr(transits, transit_pname, None)

                if not natal_planet or not transit_planet:
                    continue

                natal_degree = getattr(natal_planet, 'abs_pos', 0)
                transit_degree = getattr(transit_planet, 'abs_pos', 0)
                natal_sign = SIGN_MAP.get(getattr(natal_planet, 'sign', ''), '')
                natal_pos = getattr(natal_planet, 'position', 0)

                # Determine aspect angle
                if aspect_type == "conjunction":
                    aspect_angle = 0
                elif aspect_type == "square":
                    aspect_angle = 90
                elif aspect_type == "opposition":
                    aspect_angle = 180
                else:
                    aspect_angle = 0

                # Calculate orb
                orb, applying = calculate_aspect_orb(transit_degree, natal_degree, aspect_angle)

                # Determine status using proper thresholds
                if orb <= orb_active:
                    status = "active"
                elif orb <= orb_approaching:
                    status = "approaching"
                else:
                    status = "distant"

                # Estimate next exact date (pass aspect_angle for correct calculation)
                estimated_date = estimate_next_aspect_date(natal_degree, transit_degree, transit_planet.name, aspect_angle)

                # Format degree
                degree_info = format_degrees(natal_pos)

                # Format transit degree too
                transit_sign = SIGN_MAP.get(getattr(transit_planet, 'sign', ''), '')
                transit_pos = getattr(transit_planet, 'position', 0)
                transit_degree_info = format_degrees(transit_pos)

                cycles.append({
                    "name": f"{transit_planet.name} {aspect_type.title()}",
                    "type": aspect_type,
                    "transit_planet": transit_planet.name,
                    "natal_planet": natal_planet.name,
                    "natal_degree": f"{degree_info['degree_formatted']} {natal_sign}",
                    "transit_degree": f"{transit_degree_info['degree_formatted']} {transit_sign}",
                    "orb": round(orb, 1),
                    "applying": applying,
                    "status": status,
                    "estimated_exact_date": estimated_date,
                    "description": description,
                })

            except Exception:
                continue

        return {
            "success": True,
            "birth_data": {
                "name": req.name,
                "date": f"{req.year}-{req.month:02d}-{req.day:02d}",
            },
            "cycles": cycles,
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cycles calculation failed: {str(e)}")


# ─── Forecast / Predictive Analytics Endpoints ─

# Retrograde periods for 2025-2027 (pre-computed from ephemeris data)
# Format: (planet, start_date, end_date, sign_at_start, sign_at_end)
RETROGRADE_DATA = [
    # 2025
    ("Mercury", "2025-03-14", "2025-04-07", "Aries", "Pisces"),
    ("Mercury", "2025-07-17", "2025-08-11", "Leo", "Leo"),
    ("Mercury", "2025-11-09", "2025-11-29", "Sagittarius", "Scorpio"),
    ("Venus", "2025-03-01", "2025-04-12", "Aries", "Pisces"),
    ("Mars", "2025-01-06", "2025-02-23", "Cancer", "Cancer"),
    ("Jupiter", "2025-11-11", "2026-03-10", "Cancer", "Cancer"),
    ("Saturn", "2025-07-13", "2025-11-27", "Aries", "Pisces"),
    ("Uranus", "2025-09-06", "2026-02-04", "Gemini", "Taurus"),
    ("Neptune", "2025-07-04", "2025-12-10", "Aries", "Pisces"),
    ("Pluto", "2025-05-04", "2025-10-13", "Aquarius", "Aquarius"),
    # 2026
    ("Mercury", "2026-02-25", "2026-03-20", "Pisces", "Pisces"),
    ("Mercury", "2026-06-29", "2026-07-23", "Cancer", "Cancer"),
    ("Mercury", "2026-10-24", "2026-11-13", "Scorpio", "Scorpio"),
    ("Venus", "2026-10-02", "2026-11-13", "Scorpio", "Libra"),
    ("Mars", "2027-01-10", "2027-04-01", "Leo", "Leo"),
    ("Jupiter", "2025-11-11", "2026-03-10", "Cancer", "Cancer"),
    ("Saturn", "2026-08-01", "2026-12-10", "Taurus", "Aries"),
    ("Uranus", "2026-09-17", "2027-02-12", "Gemini", "Gemini"),
    ("Neptune", "2026-07-09", "2026-12-14", "Aries", "Aries"),
    ("Pluto", "2026-05-09", "2026-10-17", "Aquarius", "Aquarius"),
    # 2027
    ("Mercury", "2027-02-09", "2027-03-03", "Aquarius", "Aquarius"),
    ("Mercury", "2027-06-10", "2027-07-04", "Cancer", "Gemini"),
    ("Mercury", "2027-10-07", "2027-10-28", "Scorpio", "Libra"),
    ("Venus", "2027-05-09", "2027-06-20", "Gemini", "Taurus"),
]

# Eclipse data 2025-2027 (pre-computed)
ECLIPSE_DATA = [
    # 2025
    {"date": "2025-03-29", "type": "solar_total", "sign": "Aries", "degree": 8.96, "saros": 149,
     "theme": "Bold new beginnings — identity, self-assertion, courage to start fresh"},
    {"date": "2025-03-14", "type": "lunar_total", "sign": "Virgo", "degree": 23.95, "saros": 132,
     "theme": "Release perfectionism — health routines, daily habits, service work"},
    {"date": "2025-09-07", "type": "lunar_total", "sign": "Pisces", "degree": 15.23, "saros": 137,
     "theme": "Spiritual surrender — dissolving illusions, creative transcendence"},
    {"date": "2025-09-21", "type": "solar_partial", "sign": "Virgo", "degree": 29.08, "saros": 154,
     "theme": "Refine your craft — analytical clarity, discernment, health breakthroughs"},
    # 2026
    {"date": "2026-02-17", "type": "solar_annular", "sign": "Aquarius", "degree": 28.77, "saros": 121,
     "theme": "Community revolution — innovation, collective vision, humanitarian ideals"},
    {"date": "2026-03-03", "type": "lunar_total", "sign": "Virgo", "degree": 12.34, "saros": 142,
     "theme": "Purify and heal — release what no longer serves your wellbeing"},
    {"date": "2026-08-12", "type": "solar_total", "sign": "Leo", "degree": 19.88, "saros": 126,
     "theme": "Creative rebirth — self-expression, romance, inner child healing"},
    {"date": "2026-08-28", "type": "lunar_total", "sign": "Pisces", "degree": 4.67, "saros": 147,
     "theme": "Dream dissolution — releasing fantasies to embrace deeper truth"},
    # 2027
    {"date": "2027-02-06", "type": "solar_annular", "sign": "Aquarius", "degree": 17.45, "saros": 131,
     "theme": "Future visioning — technology, freedom, breaking from tradition"},
    {"date": "2027-02-20", "type": "lunar_penumbral", "sign": "Virgo", "degree": 1.89, "saros": 152,
     "theme": "Subtle shifts in routine — small refinements with big impact"},
    {"date": "2027-07-18", "type": "lunar_penumbral", "sign": "Capricorn", "degree": 25.34, "saros": 119,
     "theme": "Career recalibration — restructuring ambitions and public role"},
    {"date": "2027-08-02", "type": "solar_total", "sign": "Leo", "degree": 9.72, "saros": 136,
     "theme": "Heart awakening — courage, joy, authentic self-expression"},
]

# Interpretations for retrograde planets
RETROGRADE_THEMES = {
    "Mercury": {
        "domain": "Communication & Technology",
        "energy": "Review, revisit, and refine how you think and communicate",
        "do": "Back up data, revisit old ideas, reconnect with people, edit and revise",
        "avoid": "Signing major contracts, launching new tech, making big purchases of electronics",
    },
    "Venus": {
        "domain": "Love & Values",
        "energy": "Reassess relationships, finances, and what you truly value",
        "do": "Reflect on relationship patterns, revisit your budget, reconnect with old friends",
        "avoid": "Starting new relationships, drastic appearance changes, major financial commitments",
    },
    "Mars": {
        "domain": "Action & Drive",
        "energy": "Internalize your energy — strategy over force",
        "do": "Review your goals, refine your approach, rest and recharge physical energy",
        "avoid": "Starting new competitive ventures, confrontations, risky physical activities",
    },
    "Jupiter": {
        "domain": "Growth & Expansion",
        "energy": "Inner growth over outer expansion — deepen your philosophy",
        "do": "Revisit your beliefs, study, plan future growth, reassess what abundance means",
        "avoid": "Overcommitting, taking on too much, excessive spending on growth",
    },
    "Saturn": {
        "domain": "Structure & Discipline",
        "energy": "Review your foundations — are your structures serving you?",
        "do": "Reassess commitments, revisit long-term plans, address neglected responsibilities",
        "avoid": "Making binding long-term commitments, ignoring structural issues",
    },
    "Uranus": {
        "domain": "Innovation & Liberation",
        "energy": "Internal revolution — process recent changes before making more",
        "do": "Integrate recent breakthroughs, reflect on where you need freedom, innovate quietly",
        "avoid": "Impulsive radical changes, forcing innovation, rebelling without purpose",
    },
    "Neptune": {
        "domain": "Dreams & Spirituality",
        "energy": "The veil thins — see clearly what was previously obscured",
        "do": "Practice discernment, revisit creative visions, deepen spiritual practices",
        "avoid": "Escapism, idealization of people or situations, ignoring red flags",
    },
    "Pluto": {
        "domain": "Transformation & Power",
        "energy": "Deep internal transformation — process and integrate shadow work",
        "do": "Journaling, therapy, releasing control, allowing transformation",
        "avoid": "Power struggles, forcing transformation on others, resisting necessary endings",
    },
}

# Interpretations for major aspects between outer planets
ASPECT_THEMES = {
    ("Jupiter", "Saturn", "conjunction"): "Societal reset — new 20-year cycle of building and growth begins",
    ("Jupiter", "Saturn", "square"): "Growing pains — tension between expansion and restriction challenges you to find balance",
    ("Jupiter", "Saturn", "opposition"): "Harvest or reckoning — the structures you built are tested and matured",
    ("Jupiter", "Uranus", "conjunction"): "Breakthrough expansion — sudden opportunities, tech innovation, freedom breakthroughs",
    ("Jupiter", "Uranus", "square"): "Restless growth — desire for freedom clashes with expanding commitments",
    ("Jupiter", "Uranus", "opposition"): "Liberation through growth — balancing security with the call to break free",
    ("Jupiter", "Neptune", "conjunction"): "Spiritual expansion — heightened faith, creativity, and compassion flood in",
    ("Jupiter", "Neptune", "square"): "Idealism vs reality — beautiful visions may not have solid foundations yet",
    ("Jupiter", "Neptune", "opposition"): "Faith tested — discernment needed between genuine inspiration and illusion",
    ("Jupiter", "Pluto", "conjunction"): "Power amplified — intense ambition, transformation through growth",
    ("Jupiter", "Pluto", "square"): "Power struggle expansion — be mindful of manipulation or obsessive growth",
    ("Jupiter", "Pluto", "opposition"): "Power dynamics peak — confront where external forces control your growth",
    ("Saturn", "Uranus", "conjunction"): "New order — innovative structures emerge from the old",
    ("Saturn", "Uranus", "square"): "Old vs new — tension between tradition and revolution demands creative solutions",
    ("Saturn", "Uranus", "opposition"): "Breaking point — outdated structures must evolve or collapse",
    ("Saturn", "Neptune", "conjunction"): "Dreams meet reality — spiritual ideals take concrete form",
    ("Saturn", "Neptune", "square"): "Disillusionment or crystallization — face where dreams need grounding",
    ("Saturn", "Neptune", "opposition"): "Reality check — dissolving structures reveal what was always an illusion",
    ("Saturn", "Pluto", "conjunction"): "Total restructuring — power meets discipline, societies transform",
    ("Saturn", "Pluto", "square"): "Pressure builds — existing power structures face intense testing",
    ("Saturn", "Pluto", "opposition"): "Breakdown/breakthrough — massive pressure forces transformation of foundations",
    ("Uranus", "Neptune", "conjunction"): "Consciousness shift — generational spiritual and technological awakening",
    ("Uranus", "Neptune", "square"): "Restless idealism — the urge to revolutionize meets spiritual confusion",
    ("Uranus", "Pluto", "conjunction"): "Revolutionary transformation — societal upheaval and rebirth",
    ("Uranus", "Pluto", "square"): "Radical change — deep societal tensions demand transformation and liberation",
    ("Neptune", "Pluto", "conjunction"): "Civilizational shift — occurs ~every 492 years, profound collective transformation",
    ("Neptune", "Pluto", "sextile"): "Generational flow — subtle spiritual evolution supports deep transformation",
}


def get_sign_for_degree(abs_degree):
    """Convert absolute degree to zodiac sign."""
    signs = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
             "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]
    idx = int(abs_degree // 30) % 12
    return signs[idx]


@app.get("/forecast/retrogrades")
def get_upcoming_retrogrades():
    """
    Get upcoming retrograde periods for the next 90 days.
    Returns currently active retrogrades and upcoming ones.
    """
    try:
        now = datetime.now(timezone.utc).date()
        window_end = now + timedelta(days=90)

        active = []
        upcoming = []

        for planet, start_str, end_str, sign_start, sign_end in RETROGRADE_DATA:
            start = datetime.strptime(start_str, "%Y-%m-%d").date()
            end = datetime.strptime(end_str, "%Y-%m-%d").date()

            # Skip if entirely in the past
            if end < now:
                continue

            # Skip if too far in the future
            if start > window_end:
                continue

            theme = RETROGRADE_THEMES.get(planet, {})
            entry = {
                "planet": planet,
                "start_date": start_str,
                "end_date": end_str,
                "sign_start": sign_start,
                "sign_end": sign_end,
                "days_total": (end - start).days,
                "domain": theme.get("domain", ""),
                "energy": theme.get("energy", ""),
                "do": theme.get("do", ""),
                "avoid": theme.get("avoid", ""),
            }

            if start <= now <= end:
                entry["status"] = "active"
                entry["days_remaining"] = (end - now).days
                entry["progress_pct"] = round(((now - start).days / max((end - start).days, 1)) * 100)
                active.append(entry)
            elif start > now:
                entry["status"] = "upcoming"
                entry["days_until"] = (start - now).days
                upcoming.append(entry)

        # Sort upcoming by start date
        upcoming.sort(key=lambda x: x["start_date"])

        return {
            "success": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "active_retrogrades": active,
            "upcoming_retrogrades": upcoming,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Retrograde forecast failed: {str(e)}")


@app.get("/forecast/eclipses")
def get_upcoming_eclipses():
    """
    Get upcoming eclipses for the next 12 months.
    Returns eclipse date, type, sign, degree, and thematic interpretation.
    """
    try:
        now = datetime.now(timezone.utc).date()
        window_end = now + timedelta(days=365)

        eclipses = []
        for eclipse in ECLIPSE_DATA:
            edate = datetime.strptime(eclipse["date"], "%Y-%m-%d").date()
            if edate < now:
                continue
            if edate > window_end:
                continue

            degree_info = format_degrees(eclipse["degree"])
            eclipses.append({
                "date": eclipse["date"],
                "type": eclipse["type"],
                "type_display": eclipse["type"].replace("_", " ").title(),
                "sign": eclipse["sign"],
                "degree": degree_info["degree_formatted"],
                "theme": eclipse["theme"],
                "days_until": (edate - now).days,
            })

        # Sort by date
        eclipses.sort(key=lambda x: x["date"])

        return {
            "success": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "eclipses": eclipses,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Eclipse forecast failed: {str(e)}")


@app.post("/forecast/aspects")
def get_upcoming_aspects():
    """
    Get major aspects forming between outer planets in the next 90 days.
    These are the collective/generational transits that affect everyone.
    """
    try:
        now = datetime.now(timezone.utc)
        outer_planets = ["jupiter", "saturn", "uranus", "neptune", "pluto"]
        aspect_checks = [
            (0, "conjunction"), (60, "sextile"), (90, "square"),
            (120, "trine"), (180, "opposition"),
        ]

        # Get current positions
        transits = AstrologicalSubject(
            "Transit", now.year, now.month, now.day, now.hour, now.minute,
            "Greenwich", "GB", zodiac_type="Tropic",
        )

        aspects_found = []

        for i, p1_name in enumerate(outer_planets):
            for p2_name in outer_planets[i+1:]:
                p1 = getattr(transits, p1_name, None)
                p2 = getattr(transits, p2_name, None)
                if not p1 or not p2:
                    continue

                p1_abs = getattr(p1, 'abs_pos', 0)
                p2_abs = getattr(p2, 'abs_pos', 0)
                p1_sign = SIGN_MAP.get(getattr(p1, 'sign', ''), '')
                p2_sign = SIGN_MAP.get(getattr(p2, 'sign', ''), '')

                for angle, aspect_name in aspect_checks:
                    orb, applying = calculate_aspect_orb(p1_abs, p2_abs, angle)

                    # Only show aspects within 8 degrees
                    if orb <= 8.0:
                        theme_key = (p1.name, p2.name, aspect_name)
                        theme = ASPECT_THEMES.get(theme_key, f"{p1.name} {aspect_name} {p2.name}")

                        # Estimate when exact
                        if applying and orb > 0.5:
                            # Rough estimate based on combined motion
                            daily_motion_diff = abs(
                                PLANET_DAILY_MOTION.get(p1.name, 0.01) -
                                PLANET_DAILY_MOTION.get(p2.name, 0.01)
                            )
                            if daily_motion_diff > 0:
                                days_to_exact = orb / daily_motion_diff
                                exact_date = (now + timedelta(days=days_to_exact)).date().isoformat()
                            else:
                                exact_date = None
                        else:
                            exact_date = None

                        aspects_found.append({
                            "planet1": p1.name,
                            "planet1_sign": p1_sign,
                            "planet2": p2.name,
                            "planet2_sign": p2_sign,
                            "aspect": aspect_name,
                            "orb": round(orb, 2),
                            "applying": applying,
                            "exact_date_estimate": exact_date,
                            "theme": theme,
                            "intensity": "exact" if orb <= 1 else ("strong" if orb <= 3 else "building"),
                        })

        # Sort by orb (tightest first)
        aspects_found.sort(key=lambda x: x["orb"])

        return {
            "success": True,
            "timestamp": now.isoformat(),
            "major_aspects": aspects_found,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Aspect forecast failed: {str(e)}")


@app.post("/forecast/personal")
def get_personal_forecast(req: ChartRequest):
    """
    Generate a personalized cosmic forecast blending the user's natal chart
    with upcoming transits, retrogrades, and eclipses.

    This is the premium endpoint — gives users tailored predictions.
    """
    try:
        # Calculate natal chart
        natal = AstrologicalSubject(
            req.name, req.year, req.month, req.day, req.hour, req.minute,
            req.city, req.country, zodiac_type="Tropic",
        )

        now = datetime.now(timezone.utc)
        today = now.date()

        # Get current transits
        transits = AstrologicalSubject(
            "Transit", now.year, now.month, now.day, now.hour, now.minute,
            "Greenwich", "GB", zodiac_type="Tropic",
        )

        natal_houses = get_all_house_cusps(natal)
        signs_ordered = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
                         "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]

        # ── 1. Retrograde impact analysis ──
        retrograde_impacts = []
        for planet, start_str, end_str, sign_start, sign_end in RETROGRADE_DATA:
            start = datetime.strptime(start_str, "%Y-%m-%d").date()
            end = datetime.strptime(end_str, "%Y-%m-%d").date()

            if end < today or start > today + timedelta(days=90):
                continue

            is_active = start <= today <= end

            # Find which natal house this retrograde activates
            transit_planet = getattr(transits, planet.lower(), None)
            if transit_planet:
                t_abs = getattr(transit_planet, 'abs_pos', 0)
                affected_house = get_planet_house(t_abs, natal_houses)
            else:
                affected_house = None

            # Check if retrograde planet aspects any natal planets
            natal_aspects = []
            if transit_planet:
                t_abs = getattr(transit_planet, 'abs_pos', 0)
                for pname in ["sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn"]:
                    np = getattr(natal, pname, None)
                    if np:
                        n_abs = getattr(np, 'abs_pos', 0)
                        aspects = get_major_aspects(t_abs, n_abs, planet, np.name)
                        for a in aspects:
                            if a["orb"] <= 5:
                                natal_aspects.append({
                                    "natal_planet": np.name,
                                    "aspect": a["aspect_type"],
                                    "orb": a["orb"],
                                })

            theme = RETROGRADE_THEMES.get(planet, {})
            retrograde_impacts.append({
                "planet": planet,
                "status": "active" if is_active else "upcoming",
                "start_date": start_str,
                "end_date": end_str,
                "affected_house": affected_house,
                "natal_aspects": natal_aspects,
                "domain": theme.get("domain", ""),
                "personal_impact": f"Activating your {_ordinal(affected_house)} house" if affected_house else "General influence",
                "intensity": "high" if natal_aspects else "moderate",
            })

        # ── 2. Eclipse impact analysis ──
        eclipse_impacts = []
        for eclipse in ECLIPSE_DATA:
            edate = datetime.strptime(eclipse["date"], "%Y-%m-%d").date()
            if edate < today or edate > today + timedelta(days=365):
                continue

            eclipse_abs = eclipse["degree"] + signs_ordered.index(eclipse["sign"]) * 30 if eclipse["sign"] in signs_ordered else eclipse["degree"]

            # Which natal house does this eclipse hit?
            affected_house = get_planet_house(eclipse_abs, natal_houses)

            # Does it conjunct/oppose any natal planets?
            natal_hits = []
            for pname in ["sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "pluto"]:
                np = getattr(natal, pname, None)
                if np:
                    n_abs = getattr(np, 'abs_pos', 0)
                    orb = abs(eclipse_abs - n_abs)
                    if orb > 180:
                        orb = 360 - orb
                    if orb <= 5:
                        natal_hits.append({"planet": np.name, "orb": round(orb, 1), "type": "conjunction"})
                    elif abs(orb - 180) <= 5:
                        natal_hits.append({"planet": np.name, "orb": round(abs(orb - 180), 1), "type": "opposition"})

            eclipse_impacts.append({
                "date": eclipse["date"],
                "type": eclipse["type"].replace("_", " ").title(),
                "sign": eclipse["sign"],
                "degree": format_degrees(eclipse["degree"])["degree_formatted"],
                "theme": eclipse["theme"],
                "affected_house": affected_house,
                "natal_hits": natal_hits,
                "personal_significance": "high" if natal_hits else ("moderate" if affected_house else "low"),
                "days_until": (edate - today).days,
            })

        # ── 3. Key transit windows (next 30 days) ──
        transit_windows = []
        outer_transits = ["jupiter", "saturn", "uranus", "neptune", "pluto"]
        for t_name in outer_transits:
            tp = getattr(transits, t_name, None)
            if not tp:
                continue
            t_abs = getattr(tp, 'abs_pos', 0)
            t_sign = SIGN_MAP.get(getattr(tp, 'sign', ''), '')
            t_house = get_planet_house(t_abs, natal_houses)

            # Check aspects to natal planets
            key_aspects = []
            for n_name in ["sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn"]:
                np = getattr(natal, n_name, None)
                if not np:
                    continue
                n_abs = getattr(np, 'abs_pos', 0)
                aspects = get_major_aspects(t_abs, n_abs, tp.name, np.name)
                for a in aspects:
                    if a["orb"] <= 5:
                        key_aspects.append({
                            "natal_planet": np.name,
                            "aspect": a["aspect_type"],
                            "orb": a["orb"],
                            "applying": a["applying"],
                        })

            if key_aspects or t_house:
                transit_windows.append({
                    "transit_planet": tp.name,
                    "transit_sign": t_sign,
                    "natal_house": t_house,
                    "retrograde": getattr(tp, 'retrograde', False),
                    "key_aspects": key_aspects,
                })

        # ── 4. This week's cosmic weather summary ──
        sun_sign = SIGN_MAP.get(getattr(transits.sun, 'sign', ''), '') if transits.sun else ''
        moon_sign = SIGN_MAP.get(getattr(transits.moon, 'sign', ''), '') if transits.moon else ''
        moon_abs = getattr(transits.moon, 'abs_pos', 0) if transits.moon else 0
        sun_abs = getattr(transits.sun, 'abs_pos', 0) if transits.sun else 0
        moon_phase = calculate_moon_phase(sun_abs, moon_abs)
        moon_house = get_planet_house(moon_abs, natal_houses)

        weekly_summary = {
            "sun_sign": sun_sign,
            "moon_sign": moon_sign,
            "moon_phase": moon_phase,
            "moon_in_house": moon_house,
            "active_retrogrades_count": sum(1 for r in retrograde_impacts if r["status"] == "active"),
        }

        return {
            "success": True,
            "birth_data": {
                "name": req.name,
                "date": f"{req.year}-{req.month:02d}-{req.day:02d}",
            },
            "weekly_summary": weekly_summary,
            "retrograde_impacts": retrograde_impacts,
            "eclipse_impacts": eclipse_impacts,
            "transit_windows": transit_windows,
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Personal forecast failed: {str(e)}")


def _ordinal(n):
    """Convert number to ordinal string (1st, 2nd, 3rd, etc.)"""
    if n is None:
        return ""
    n = int(n)
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10 if n % 10 in {1, 2, 3} and n not in {11, 12, 13} else 0, "th")
    return f"{n}{suffix}"


# Moon phase guidance for each phase
MOON_PHASE_GUIDANCE = {
    "new moon": {
        "energy": "Seeds & Intentions",
        "message": "The sky is dark and fertile. This is your blank page — set intentions, plant seeds, begin what you've been dreaming about. The universe is listening.",
        "do": "Journal your intentions, start new projects, set goals, meditate on what you want to call in",
        "avoid": "Launching publicly, making big announcements, forcing outcomes",
    },
    "waxing crescent": {
        "energy": "Emerging Momentum",
        "message": "Your intentions are taking root beneath the surface. Stay committed even when you can't see results yet. Faith fuels growth.",
        "do": "Take small first steps, gather resources, build momentum, affirm your direction",
        "avoid": "Giving up too soon, comparing your beginning to someone else's middle",
    },
    "first quarter": {
        "energy": "Challenge & Commitment",
        "message": "Resistance arrives to test your resolve. This tension is not a sign to stop — it's the universe asking: how badly do you want this?",
        "do": "Push through obstacles, make decisions, take action despite doubt, adjust your approach",
        "avoid": "Abandoning plans at the first sign of difficulty, people-pleasing over your own goals",
    },
    "waxing gibbous": {
        "energy": "Refinement & Trust",
        "message": "Almost there. Fine-tune, adjust, and trust the process. The details matter now. Polish what you've been building.",
        "do": "Edit, refine, perfect your work, seek feedback, make final adjustments",
        "avoid": "Perfectionism that prevents progress, starting something entirely new",
    },
    "full moon": {
        "energy": "Illumination & Release",
        "message": "Everything is lit up. Emotions run high, truths are revealed, and what's been building reaches a peak. Celebrate what's working. Release what isn't.",
        "do": "Celebrate wins, release what no longer serves you, have honest conversations, charge your crystals",
        "avoid": "Making impulsive decisions from heightened emotions, clinging to what needs to go",
    },
    "waning gibbous": {
        "energy": "Gratitude & Sharing",
        "message": "The harvest is in. Share your wisdom, express gratitude, and give back. You've learned something valuable — pass it on.",
        "do": "Share knowledge, mentor others, practice gratitude, distribute what you've gathered",
        "avoid": "Hoarding resources or knowledge, starting major new ventures",
    },
    "last quarter": {
        "energy": "Letting Go",
        "message": "Time to clear the field. Release old habits, relationships, and beliefs that have run their course. Make space for what's next.",
        "do": "Declutter physically and emotionally, forgive, end what's complete, tie up loose ends",
        "avoid": "Starting new things, holding on to what's clearly finished",
    },
    "waning crescent": {
        "energy": "Rest & Surrender",
        "message": "The quietest phase. Rest deeply, dream, and surrender. The void before the new moon is sacred — don't fill it with noise.",
        "do": "Rest, sleep, meditate, take baths, dream, be still, reflect on the full cycle",
        "avoid": "Overworking, forcing productivity, making major commitments",
    },
}

# Planetary ingress data 2025-2027 (when planets change signs)
INGRESS_DATA = [
    # 2025
    {"date": "2025-03-20", "planet": "Sun", "sign": "Aries", "message": "Spring equinox — the astrological new year begins. Fresh starts, bold energy, and the courage to initiate."},
    {"date": "2025-04-19", "planet": "Sun", "sign": "Taurus", "message": "Slow down and savor. Taurus season grounds you in your body, your values, and what genuinely nourishes you."},
    {"date": "2025-05-20", "planet": "Sun", "sign": "Gemini", "message": "Curiosity awakens. Gemini season brings new conversations, ideas, and connections. Stay playful."},
    {"date": "2025-05-25", "planet": "Jupiter", "sign": "Cancer", "message": "Jupiter enters Cancer — a once-in-12-years expansion of home, family, emotional security, and nurturing. A deeply fertile year begins."},
    {"date": "2025-06-20", "planet": "Sun", "sign": "Cancer", "message": "Summer solstice. Cancer season turns you inward toward home, roots, and emotional truth."},
    {"date": "2025-07-06", "planet": "Uranus", "sign": "Gemini", "message": "Uranus enters Gemini for the first time since 1949. Seven years of revolution in communication, media, AI, and how we think. Everything changes."},
    {"date": "2025-07-22", "planet": "Sun", "sign": "Leo", "message": "Leo season: your heart wants to be seen. Create, play, love boldly, and let your light be unapologetic."},
    {"date": "2025-08-22", "planet": "Sun", "sign": "Virgo", "message": "Virgo season refines and heals. Tend to your body, your routines, and the sacred details of daily life."},
    {"date": "2025-09-22", "planet": "Sun", "sign": "Libra", "message": "Autumn equinox — Libra season seeks harmony, beauty, and balanced partnerships. What needs rebalancing in your life?"},
    {"date": "2025-10-22", "planet": "Sun", "sign": "Scorpio", "message": "The veil thins. Scorpio season invites you into depth, intimacy, transformation, and the power of what's hidden."},
    {"date": "2025-11-21", "planet": "Sun", "sign": "Sagittarius", "message": "Adventure calls. Sagittarius season expands your horizons through travel, philosophy, and unshakeable faith."},
    {"date": "2025-12-21", "planet": "Sun", "sign": "Capricorn", "message": "Winter solstice. Capricorn season builds your legacy. What are you willing to work for with quiet, relentless devotion?"},
    # 2026
    {"date": "2026-01-19", "planet": "Sun", "sign": "Aquarius", "message": "Aquarius season electrifies your vision for the future. Think bigger, think weirder, think for the collective."},
    {"date": "2026-02-18", "planet": "Sun", "sign": "Pisces", "message": "Pisces season dissolves boundaries. Dream, create, heal, and let the mystical in. The zodiac year is ending — reflect."},
    {"date": "2026-02-13", "planet": "Saturn", "sign": "Aries", "message": "Saturn enters Aries — a new 29-year disciplinary cycle begins. Mastering initiative, courage, and self-authority becomes the work."},
    {"date": "2026-03-20", "planet": "Sun", "sign": "Aries", "message": "Spring equinox and astrological new year. Aries season ignites your willpower and your willingness to begin again."},
    {"date": "2026-03-30", "planet": "Neptune", "sign": "Aries", "message": "Neptune enters Aries for the first time since 1874. A new 14-year dream cycle begins — the collective imagination is reborn with warrior spirit."},
    {"date": "2026-04-19", "planet": "Sun", "sign": "Taurus", "message": "Taurus season grounds the fiery spring energy. Return to your senses, your values, your garden."},
    {"date": "2026-05-20", "planet": "Sun", "sign": "Gemini", "message": "Gemini season opens the doors of perception. New ideas, conversations, and mental adventures await."},
    {"date": "2026-06-09", "planet": "Jupiter", "sign": "Leo", "message": "Jupiter enters Leo — 12 months of expanded creativity, romance, generosity, and joyful self-expression. Your heart grows three sizes."},
    {"date": "2026-06-20", "planet": "Sun", "sign": "Cancer", "message": "Summer solstice. Cancer season holds you close. Nurture what matters and let yourself be held."},
    {"date": "2026-07-22", "planet": "Sun", "sign": "Leo", "message": "Leo season with Jupiter in Leo — this is a once-in-12-years portal for creative breakthroughs and heart-led living."},
    {"date": "2026-08-22", "planet": "Sun", "sign": "Virgo", "message": "Virgo season brings integration. After Leo's fire, now refine, heal, and serve with precision."},
    {"date": "2026-09-22", "planet": "Sun", "sign": "Libra", "message": "Autumn equinox. Libra season rebalances your relationships and your relationship with beauty and justice."},
    {"date": "2026-10-22", "planet": "Sun", "sign": "Scorpio", "message": "Scorpio season dives deep. Face what you've been avoiding — the treasure is always guarded by the dragon."},
    {"date": "2026-11-21", "planet": "Sun", "sign": "Sagittarius", "message": "Sagittarius season launches arrows of meaning into the sky. Where is your truth pointing you?"},
    {"date": "2026-12-21", "planet": "Sun", "sign": "Capricorn", "message": "Winter solstice and Capricorn season. The mountain is yours to climb. Every step counts."},
]


@app.get("/forecast/lunar-phases")
def get_lunar_phases():
    """
    Get current moon phase with rich guidance, plus upcoming new and full moons.
    """
    try:
        now = datetime.now(timezone.utc)
        transits = AstrologicalSubject(
            "Transit", now.year, now.month, now.day, now.hour, now.minute,
            "Greenwich", "GB", zodiac_type="Tropic",
        )

        sun_abs = getattr(transits.sun, 'abs_pos', 0) if transits.sun else 0
        moon_abs = getattr(transits.moon, 'abs_pos', 0) if transits.moon else 0
        moon_sign = SIGN_MAP.get(getattr(transits.moon, 'sign', ''), '') if transits.moon else ''
        moon_pos = getattr(transits.moon, 'position', 0) if transits.moon else 0

        phase = calculate_moon_phase(sun_abs, moon_abs)
        guidance = MOON_PHASE_GUIDANCE.get(phase, {})

        # Calculate moon illumination percentage
        angle = (moon_abs - sun_abs) % 360
        illumination = round((1 - abs(180 - angle) / 180) * 100)
        # More accurate: use cosine
        import math
        illumination = round((1 - math.cos(math.radians(angle))) / 2 * 100)

        # Calculate days until next new and full moon
        # New moon = sun-moon angle of 0, Full moon = 180
        moon_daily = 13.2 - 1.0  # Moon motion minus sun motion
        angle_to_new = (360 - angle) % 360
        angle_to_full = (180 - angle) % 360
        days_to_new = round(angle_to_new / moon_daily, 1)
        days_to_full = round(angle_to_full / moon_daily, 1)

        degree_info = format_degrees(moon_pos)

        return {
            "success": True,
            "timestamp": now.isoformat(),
            "current_moon": {
                "phase": phase,
                "sign": moon_sign,
                "degree": degree_info["degree_formatted"],
                "illumination_pct": illumination,
            },
            "guidance": {
                "energy": guidance.get("energy", ""),
                "message": guidance.get("message", ""),
                "do": guidance.get("do", ""),
                "avoid": guidance.get("avoid", ""),
            },
            "upcoming": {
                "days_to_new_moon": days_to_new,
                "days_to_full_moon": days_to_full,
            },
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lunar phase forecast failed: {str(e)}")


@app.get("/forecast/ingresses")
def get_upcoming_ingresses():
    """
    Get upcoming planetary sign changes (ingresses) for the next 90 days.
    Major ingresses (Jupiter, Saturn, Uranus, Neptune, Pluto) shown for next 12 months.
    """
    try:
        now = datetime.now(timezone.utc).date()
        results = []

        for entry in INGRESS_DATA:
            edate = datetime.strptime(entry["date"], "%Y-%m-%d").date()
            if edate < now:
                continue

            # Sun ingresses: 90 day window. Outer planets: 365 day window.
            is_major = entry["planet"] not in ("Sun", "Mercury", "Venus", "Mars")
            window = 365 if is_major else 90
            if edate > now + timedelta(days=window):
                continue

            results.append({
                "date": entry["date"],
                "planet": entry["planet"],
                "sign": entry["sign"],
                "message": entry["message"],
                "is_major": is_major,
                "days_until": (edate - now).days,
            })

        results.sort(key=lambda x: x["date"])

        return {
            "success": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ingresses": results,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingress forecast failed: {str(e)}")


# ═══════════════════════════════════════════════════════════════════
# COSMIC MIRROR — Current Events + Astrology Correlation Engine
# ═══════════════════════════════════════════════════════════════════

# Planet → mundane rulership topics (what each planet governs in world events)
PLANET_NEWS_DOMAINS = {
    "Mercury": {
        "topics": ["technology", "communication", "social media", "AI", "internet",
                    "transportation", "education", "journalism", "media"],
        "mundane": "Communication, technology, trade, and information flow",
        "keywords_rss": ["tech", "AI", "social media", "internet", "education", "transport"],
    },
    "Venus": {
        "topics": ["economy", "markets", "fashion", "art", "culture", "relationships",
                    "beauty", "luxury", "entertainment", "music"],
        "mundane": "Markets, art, culture, diplomacy, and social harmony",
        "keywords_rss": ["economy", "markets", "culture", "fashion", "entertainment"],
    },
    "Mars": {
        "topics": ["military", "conflict", "sports", "competition", "defense",
                    "violence", "energy", "industry", "protest"],
        "mundane": "Conflict, military action, competition, and collective drive",
        "keywords_rss": ["military", "conflict", "war", "sports", "protest", "energy"],
    },
    "Jupiter": {
        "topics": ["law", "religion", "philosophy", "higher education", "international",
                    "expansion", "growth", "travel", "immigration", "optimism"],
        "mundane": "Law, religion, international relations, and collective optimism",
        "keywords_rss": ["law", "court", "international", "religion", "university", "immigration"],
    },
    "Saturn": {
        "topics": ["government", "regulation", "infrastructure", "austerity",
                    "institutions", "authority", "restriction", "aging", "tradition"],
        "mundane": "Government, institutions, regulation, and structural reform",
        "keywords_rss": ["government", "regulation", "infrastructure", "policy", "institution"],
    },
    "Uranus": {
        "topics": ["revolution", "technology", "disruption", "innovation", "freedom",
                    "rebellion", "earthquake", "electricity", "space", "crypto"],
        "mundane": "Revolution, technological breakthroughs, sudden change, and liberation",
        "keywords_rss": ["revolution", "innovation", "breakthrough", "crypto", "space", "disruption"],
    },
    "Neptune": {
        "topics": ["pandemic", "ocean", "pharmaceutical", "film", "music", "spirituality",
                    "scandal", "deception", "oil", "drugs", "compassion", "mental health"],
        "mundane": "Collective dreams, illusion/disillusion, spirituality, and the unseen",
        "keywords_rss": ["pharmaceutical", "ocean", "film", "scandal", "mental health", "spiritual"],
    },
    "Pluto": {
        "topics": ["power", "corruption", "transformation", "death", "rebirth",
                    "nuclear", "underground", "wealth inequality", "control", "surveillance"],
        "mundane": "Power dynamics, transformation, hidden forces, and societal rebirth",
        "keywords_rss": ["power", "corruption", "nuclear", "wealth", "surveillance", "transformation"],
    },
}

# Sign → mundane themes (what area of life the sign activates in world events)
SIGN_MUNDANE_THEMES = {
    "Aries": "identity, independence, military action, new beginnings in leadership",
    "Taurus": "economy, banking, agriculture, material resources, environmental stability",
    "Gemini": "media, communication networks, education policy, local communities",
    "Cancer": "homeland, housing, food security, family policy, emotional wellbeing",
    "Leo": "leadership, entertainment, children, creative expression, national pride",
    "Virgo": "healthcare, labor, daily systems, public health, service industries",
    "Libra": "diplomacy, justice system, partnerships between nations, social equality",
    "Scorpio": "shared resources, debt, insurance, taboos, power beneath the surface",
    "Sagittarius": "international law, religion, higher education, publishing, travel policy",
    "Capricorn": "government structures, corporations, tradition, authority, long-term planning",
    "Aquarius": "technology, social movements, humanitarian causes, collective consciousness",
    "Pisces": "healthcare, spirituality, compassion fatigue, oceans, film/music/art",
}

# RSS feeds (free, no API key) — diverse, reliable sources
RSS_FEEDS = [
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml", "source": "BBC World"},
    {"url": "https://feeds.bbci.co.uk/news/technology/rss.xml", "source": "BBC Tech"},
    {"url": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "source": "BBC Science"},
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "source": "NYT World"},
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml", "source": "NYT Tech"},
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml", "source": "NYT Science"},
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "source": "NYT Business"},
    {"url": "https://www.theguardian.com/world/rss", "source": "Guardian World"},
    {"url": "http://feeds.reuters.com/reuters/topNews", "source": "Reuters"},
    {"url": "https://feeds.npr.org/1001/rss.xml", "source": "NPR News"},
]

# Simple in-memory cache for RSS fetches (refresh every 30 min)
_news_cache = {"data": None, "fetched_at": None}
_cosmic_news_cache = {"data": None, "fetched_at": None}

def _fetch_rss_articles(max_per_feed=15):
    """Fetch articles from RSS feeds. Returns list of {title, description, source, published, link}."""
    now = datetime.now(timezone.utc)

    # Return cache if fresh (< 30 min old)
    if _news_cache["data"] and _news_cache["fetched_at"]:
        age = (now - _news_cache["fetched_at"]).total_seconds()
        if age < 1800:
            return _news_cache["data"]

    articles = []
    for feed in RSS_FEEDS:
        try:
            req = Request(feed["url"], headers={"User-Agent": "TheCodeFairy/2.0 AstrologyApp"})
            with urlopen(req, timeout=8) as resp:
                raw = resp.read()

            root = ET.fromstring(raw)

            # Handle both RSS 2.0 and Atom formats
            items = root.findall(".//item")
            if not items:
                # Try Atom format
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                items = root.findall(".//atom:entry", ns)

            count = 0
            for item in items:
                if count >= max_per_feed:
                    break

                # RSS 2.0 format
                title_el = item.find("title")
                desc_el = item.find("description")
                link_el = item.find("link")
                pub_el = item.find("pubDate")

                # Atom fallback
                if title_el is None:
                    ns = {"atom": "http://www.w3.org/2005/Atom"}
                    title_el = item.find("atom:title", ns)
                    desc_el = item.find("atom:summary", ns)
                    link_el = item.find("atom:link", ns)

                title = title_el.text.strip() if title_el is not None and title_el.text else ""
                desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
                link = ""
                if link_el is not None:
                    link = link_el.text.strip() if link_el.text else link_el.get("href", "")
                pub = pub_el.text.strip() if pub_el is not None and pub_el.text else ""

                # Strip HTML from description
                import re
                desc = re.sub(r'<[^>]+>', '', desc)
                if len(desc) > 300:
                    desc = desc[:297] + "..."

                if title:
                    articles.append({
                        "title": title,
                        "description": desc,
                        "source": feed["source"],
                        "published": pub,
                        "link": link,
                    })
                    count += 1

        except Exception as e:
            logging.warning(f"Failed to fetch RSS from {feed['source']}: {e}")
            continue

    _news_cache["data"] = articles
    _news_cache["fetched_at"] = now
    return articles


def _score_article_for_planet(article, planet_key):
    """Score how relevant an article is to a planet's domain. Returns 0-100."""
    domains = PLANET_NEWS_DOMAINS.get(planet_key, {})
    topics = domains.get("topics", [])
    keywords = domains.get("keywords_rss", [])

    text = (article["title"] + " " + article["description"]).lower()
    score = 0

    # Keyword matching
    for kw in keywords:
        if kw.lower() in text:
            score += 15

    for topic in topics:
        if topic.lower() in text:
            score += 10

    # Cap at 100
    return min(score, 100)


def _get_active_transits_for_news():
    """Get currently active astrological events that should correlate with news."""
    now = datetime.now(timezone.utc).date()
    active_events = []

    # Active retrogrades
    for planet, start_str, end_str, sign_start, sign_end in RETROGRADE_DATA:
        start = datetime.strptime(start_str, "%Y-%m-%d").date()
        end = datetime.strptime(end_str, "%Y-%m-%d").date()
        if start <= now <= end:
            active_events.append({
                "type": "retrograde",
                "planet": planet,
                "sign": sign_start,
                "label": f"{planet} Retrograde in {sign_start}",
                "energy": RETROGRADE_THEMES.get(planet, {}).get("energy", ""),
            })

    # Current planetary positions from Kerykeion (today's sky)
    try:
        today = datetime.now(timezone.utc)
        sky = AstrologicalSubject(
            "Now", today.year, today.month, today.day,
            today.hour, today.minute,
            lng=0, lat=51.5, city="London", nation="GB"
        )
        outer_planets = ["Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"]
        for p in outer_planets:
            pdata = getattr(sky, p.lower(), None) if hasattr(sky, p.lower()) else None
            if pdata is None:
                pdata = getattr(sky, f"{p.lower()}_data", None)
            if pdata:
                sign = pdata.get("sign", "") if isinstance(pdata, dict) else getattr(pdata, "sign", "")
                if sign:
                    active_events.append({
                        "type": "transit",
                        "planet": p,
                        "sign": sign,
                        "label": f"{p} in {sign}",
                        "energy": f"{p} transiting {sign} — activating themes of {SIGN_MUNDANE_THEMES.get(sign, 'collective evolution')}",
                    })
    except Exception as e:
        logging.warning(f"Could not get current sky for news correlation: {e}")

    # Upcoming eclipses within 14 days (the "eclipse window")
    for entry in ECLIPSE_DATA:
        edate = datetime.strptime(entry["date"], "%Y-%m-%d").date()
        days_until = (edate - now).days
        if -3 <= days_until <= 14:
            active_events.append({
                "type": "eclipse",
                "planet": "Moon" if "lunar" in entry["type"].lower() else "Sun",
                "sign": entry["sign"],
                "label": f'{entry["type"].replace("_", " ").title()} in {entry["sign"]}',
                "energy": f"Eclipse season — fated events, revelations, and turning points around {SIGN_MUNDANE_THEMES.get(entry['sign'], 'collective themes')}",
            })

    return active_events


def _generate_cosmic_interpretation(planet, sign, article_title, transit_type):
    """Generate an astrological interpretation connecting a news event to a transit."""
    planet_domain = PLANET_NEWS_DOMAINS.get(planet, {}).get("mundane", "collective forces")
    sign_theme = SIGN_MUNDANE_THEMES.get(sign, "collective evolution")

    if transit_type == "retrograde":
        templates = [
            f"With {planet} retrograde in {sign}, we're collectively revisiting themes of {sign_theme}. This story reflects that energy — old patterns resurfacing for review.",
            f"{planet} retrograde asks us to look backward before moving forward. In {sign}, the focus is on {sign_theme} — and the world is responding in kind.",
            f"The reversal energy of {planet} retrograde in {sign} manifests in how we're collectively re-examining {planet_domain.lower()}.",
        ]
    elif transit_type == "eclipse":
        templates = [
            f"Eclipse energy in {sign} activates fated events around {sign_theme}. This headline mirrors the cosmic acceleration — things are moving fast.",
            f"During eclipse season in {sign}, hidden truths surface. The cosmic spotlight is on {sign_theme}, and the world stage reflects it.",
            f"Eclipses in {sign} catalyze turning points in {sign_theme}. What's happening now carries echoes of a larger cosmic narrative.",
        ]
    else:
        templates = [
            f"{planet} in {sign} channels its energy through {sign_theme}. This headline is a real-world echo of that transit.",
            f"As {planet} moves through {sign}, it activates collective themes of {sign_theme}. We see this playing out in real time.",
            f"The {planet}-in-{sign} transit governs {planet_domain.lower()} — and this story shows how those cosmic currents manifest on Earth.",
        ]

    # Use a hash of the article title to deterministically select a template
    idx = int(hashlib.md5(article_title.encode()).hexdigest(), 16) % len(templates)
    return templates[idx]


@app.get("/forecast/cosmic-news")
def get_cosmic_news():
    """
    The Cosmic Mirror — correlates current world events with active astrological transits.
    Fetches news from RSS feeds, scores them against planetary domains,
    and returns the most astrologically relevant stories with interpretations.
    """
    try:
        now = datetime.now(timezone.utc)

        # Return cache if fresh (< 15 min)
        if _cosmic_news_cache["data"] and _cosmic_news_cache["fetched_at"]:
            age = (now - _cosmic_news_cache["fetched_at"]).total_seconds()
            if age < 900:
                return _cosmic_news_cache["data"]

        # 1. Get active astrological events
        active_events = _get_active_transits_for_news()

        # 2. Fetch current news
        articles = _fetch_rss_articles(max_per_feed=12)

        if not articles:
            return {
                "success": True,
                "timestamp": now.isoformat(),
                "cosmic_mirror": [],
                "active_sky": active_events,
                "message": "The cosmic mirror is clear — no news feeds available right now. Try again shortly.",
            }

        # 3. Score each article against each active event
        correlations = []
        seen_titles = set()  # Deduplicate

        for event in active_events:
            planet = event["planet"]
            scored = []
            for article in articles:
                if article["title"] in seen_titles:
                    continue
                score = _score_article_for_planet(article, planet)
                if score >= 15:  # Only include meaningfully relevant articles
                    scored.append((score, article))

            # Take top 2 per event
            scored.sort(key=lambda x: -x[0])
            for score, article in scored[:2]:
                if article["title"] in seen_titles:
                    continue
                seen_titles.add(article["title"])

                correlations.append({
                    "headline": article["title"],
                    "summary": article["description"],
                    "source": article["source"],
                    "link": article["link"],
                    "transit": event["label"],
                    "transit_type": event["type"],
                    "planet": event["planet"],
                    "sign": event["sign"],
                    "relevance_score": score,
                    "cosmic_interpretation": _generate_cosmic_interpretation(
                        event["planet"], event["sign"], article["title"], event["type"]
                    ),
                })

        # Sort by relevance score, take top 8
        correlations.sort(key=lambda x: -x["relevance_score"])
        correlations = correlations[:8]

        # 4. Generate a daily "cosmic weather" summary
        retrogrades = [e for e in active_events if e["type"] == "retrograde"]
        eclipses = [e for e in active_events if e["type"] == "eclipse"]
        major_transits = [e for e in active_events if e["type"] == "transit"]

        summary_parts = []
        if retrogrades:
            planets = ", ".join(r["planet"] for r in retrogrades)
            summary_parts.append(f"{planets} {'is' if len(retrogrades) == 1 else 'are'} retrograde — a time of collective review and revision")
        if eclipses:
            summary_parts.append(f"Eclipse season is active — expect accelerated events and revelations")
        if major_transits:
            # Highlight the most notable outer planet transit
            notable = [t for t in major_transits if t["planet"] in ("Pluto", "Neptune", "Uranus")]
            if notable:
                t = notable[0]
                summary_parts.append(f"{t['planet']} in {t['sign']} is reshaping {SIGN_MUNDANE_THEMES.get(t['sign'], 'the collective')}")

        daily_summary = ". ".join(summary_parts) + "." if summary_parts else "The sky is quietly holding — a moment of cosmic pause."

        result = {
            "success": True,
            "timestamp": now.isoformat(),
            "daily_summary": daily_summary,
            "cosmic_mirror": correlations,
            "active_sky": [
                {"label": e["label"], "type": e["type"], "energy": e["energy"]}
                for e in active_events
            ],
            "total_articles_scanned": len(articles),
            "feeds_active": len(set(a["source"] for a in articles)),
        }

        _cosmic_news_cache["data"] = result
        _cosmic_news_cache["fetched_at"] = now
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cosmic news correlation failed: {str(e)}")


# ═══════════════════════════════════════════════════════════════════
# PREDICTIVE ENGINE — Forward-Looking Cosmic Predictions
# ═══════════════════════════════════════════════════════════════════

# Historical context — what happened when these transits last occurred
HISTORICAL_PATTERNS = {
    # Planet-in-sign historical data
    ("Pluto", "Aquarius"): {
        "last_transit": "1778–1798",
        "events": [
            "The American and French Revolutions — total overthrow of power structures",
            "The Industrial Revolution accelerated — technology reshaped society",
            "The Enlightenment peaked — reason, science, and collective ideals over monarchy",
        ],
        "theme": "Revolution in power, technology, and collective governance",
    },
    ("Neptune", "Aries"): {
        "last_transit": "1861–1875",
        "events": [
            "The American Civil War — a fight for ideals of freedom and identity",
            "Rise of nationalism and independence movements across Europe",
            "The birth of the Red Cross — compassion applied to warfare",
        ],
        "theme": "Collective dreams channeled through identity, independence, and warrior compassion",
    },
    ("Uranus", "Gemini"): {
        "last_transit": "1942–1949",
        "events": [
            "The invention of the first computers (ENIAC, Colossus)",
            "The birth of the United Nations — new global communication",
            "Television went mainstream — total revolution in media and information",
        ],
        "theme": "Revolution in communication technology, media, and how information flows",
    },
    ("Saturn", "Aries"): {
        "last_transit": "1996–1999",
        "events": [
            "Rise of dotcom entrepreneurship — discipline applied to bold new ventures",
            "Kosovo conflict — hard lessons about military intervention",
            "Corporate governance reforms — authority and accountability in leadership",
        ],
        "theme": "Mastering initiative, learning discipline in leadership, and structural courage",
    },
    ("Saturn", "Pisces"): {
        "last_transit": "1993–1996",
        "events": [
            "Healthcare reform debates in the US — structuring compassion",
            "The rise of antidepressants (Prozac era) — material approach to emotional health",
            "End of apartheid in South Africa — dissolving structures of separation",
        ],
        "theme": "Building real structures around healing, compassion, and collective surrender",
    },
    ("Jupiter", "Cancer"): {
        "last_transit": "2013–2014",
        "events": [
            "Affordable Care Act expansion — growth in healthcare/family protection",
            "Housing markets recovered — real estate expansion",
            "Immigration policy became a central cultural debate",
        ],
        "theme": "Expansion in home, family, food security, and emotional nurturing at scale",
    },
    ("Jupiter", "Leo"): {
        "last_transit": "2014–2015",
        "events": [
            "Social media influencer culture exploded — self-expression as currency",
            "Marriage equality legalized in the US — love wins",
            "Streaming entertainment (Netflix) disrupted Hollywood — creative renaissance",
        ],
        "theme": "Expansion in creativity, entertainment, romance, and joyful self-expression",
    },
    # Retrograde themes by planet (general)
    ("Mercury", "retrograde"): {
        "last_transit": "Occurs 3-4x yearly",
        "events": [
            "Technology glitches, travel delays, and communication breakdowns",
            "Old friends, exes, and unfinished conversations resurface",
            "Contracts signed during Mercury retrograde often need revision",
        ],
        "theme": "Review, revisit, revise — the universe's editing period",
    },
    ("Venus", "retrograde"): {
        "last_transit": "Occurs every 18 months",
        "events": [
            "Market corrections and financial reassessments",
            "Past relationships and old values resurface for review",
            "Cultural shifts in what we find beautiful or worthy",
        ],
        "theme": "Reassessing love, money, and what we truly value",
    },
}

# Specific prediction templates for planet+sign+event_type combos
def _generate_predictions(days_ahead=60):
    """Generate dated predictions based on upcoming astrological events."""
    now = datetime.now(timezone.utc).date()
    window_end = now + timedelta(days=days_ahead)
    predictions = []
    pred_id = 0

    # ── Retrograde predictions ──
    for planet, start_str, end_str, sign_start, sign_end in RETROGRADE_DATA:
        start = datetime.strptime(start_str, "%Y-%m-%d").date()
        end = datetime.strptime(end_str, "%Y-%m-%d").date()

        # Skip if entirely past or too far future
        if end < now or start > window_end:
            continue

        domains = PLANET_NEWS_DOMAINS.get(planet, {})
        topics = domains.get("topics", [])
        mundane = domains.get("mundane", "")
        sign_theme = SIGN_MUNDANE_THEMES.get(sign_start, "")

        days_until = max(0, (start - now).days)
        is_active = start <= now <= end

        # Historical context
        hist_key = (planet, "retrograde")
        historical = HISTORICAL_PATTERNS.get(hist_key, None)

        # Generate specific predictions
        planet_predictions = []

        if planet == "Mercury":
            planet_predictions = [
                f"Expect technology disruptions — app outages, software bugs, and miscommunications between {start_str} and {end_str}",
                f"Travel delays and transportation issues likely, especially around {sign_start}-themed contexts ({sign_theme})",
                f"Old contacts, conversations, and unfinished projects from the past will resurface for review",
                f"Contracts and agreements signed in this window may need revision — double-check everything",
            ]
        elif planet == "Venus":
            planet_predictions = [
                f"Financial markets may face turbulence or correction between {start_str} and {end_str}",
                f"Cultural conversations about beauty standards, art, and what we value will intensify",
                f"Past relationships or old patterns around money and love resurface for examination",
                f"Diplomacy between nations may stall or require renegotiation during this window",
            ]
        elif planet == "Mars":
            planet_predictions = [
                f"Increased tensions, protests, or military escalations between {start_str} and {end_str}",
                f"Sports scandals or unexpected upsets in major competitions",
                f"Industrial and energy sector disruptions — supply chain issues likely",
                f"Collective frustration simmers; previously suppressed anger may erupt publicly",
            ]
        elif planet == "Jupiter":
            planet_predictions = [
                f"Legal and judicial systems face review — landmark cases may be revisited",
                f"International relations slow down; treaties and agreements delayed",
                f"Higher education and religious institutions undergo internal examination",
                f"The collective pauses on expansion to ask: are we growing in the right direction?",
            ]
        elif planet == "Saturn":
            planet_predictions = [
                f"Government structures face pressure — policy rollbacks or institutional reviews likely",
                f"Infrastructure vulnerabilities exposed — bridges, systems, and regulations tested",
                f"Authority figures face accountability; leaders may step down or face scrutiny",
                f"Regulatory frameworks are questioned — are the rules actually working?",
            ]
        elif planet in ("Uranus", "Neptune", "Pluto"):
            planet_predictions = [
                f"{planet} retrograde is generational — internal shifts in {mundane.lower()} unfold slowly",
                f"Themes from earlier in the year around {sign_theme} will be revisited and integrated",
                f"What seemed like a breakthrough earlier may need refinement before its next evolution",
            ]
        else:
            planet_predictions = [
                f"{planet} retrograde activates review in {mundane.lower()} between {start_str} and {end_str}",
            ]

        pred_id += 1
        predictions.append({
            "id": f"retro_{planet.lower()}_{start_str}",
            "type": "retrograde",
            "planet": planet,
            "sign": sign_start,
            "start_date": start_str,
            "end_date": end_str,
            "days_until": days_until,
            "is_active": is_active,
            "headline": f"{planet} Retrograde in {sign_start}",
            "date_range": f"{start_str} to {end_str}",
            "domain": mundane,
            "predictions": planet_predictions,
            "watch_for": topics[:5],
            "historical": historical,
        })

    # ── Eclipse predictions ──
    for eclipse in ECLIPSE_DATA:
        edate = datetime.strptime(eclipse["date"], "%Y-%m-%d").date()
        if edate < now - timedelta(days=3) or edate > window_end:
            continue

        sign = eclipse["sign"]
        sign_theme = SIGN_MUNDANE_THEMES.get(sign, "")
        is_solar = "solar" in eclipse["type"]
        days_until = max(0, (edate - now).days)
        is_active = abs((edate - now).days) <= 5  # Eclipse window ±5 days

        eclipse_predictions = []
        if is_solar:
            eclipse_predictions = [
                f"New beginnings in {sign_theme} — expect announcements, launches, and fresh directions around {eclipse['date']}",
                f"Leadership changes or bold new initiatives emerge in the public sphere",
                f"Events that occur this week feel 'fated' — they set the direction for the next 6 months",
                f"What you start now carries unusual momentum — the universe is co-signing new paths in {sign}",
            ]
        else:
            eclipse_predictions = [
                f"Emotional revelations and hidden truths surface around {eclipse['date']}",
                f"Something that's been building behind the scenes becomes impossible to ignore",
                f"Collective emotional release — public figures may face revelations or endings",
                f"What needs to be released will make itself known; endings that lead to new space",
            ]

        pred_id += 1
        predictions.append({
            "id": f"eclipse_{eclipse['type']}_{eclipse['date']}",
            "type": "eclipse",
            "planet": "Sun" if is_solar else "Moon",
            "sign": sign,
            "start_date": eclipse["date"],
            "end_date": eclipse["date"],
            "days_until": days_until,
            "is_active": is_active,
            "headline": f'{eclipse["type"].replace("_", " ").title()} in {sign}',
            "date_range": eclipse["date"],
            "domain": eclipse["theme"],
            "predictions": eclipse_predictions,
            "watch_for": [s.strip() for s in sign_theme.split(",")][:4] if sign_theme else [],
            "historical": None,
        })

    # ── Major ingress predictions ──
    for entry in INGRESS_DATA:
        edate = datetime.strptime(entry["date"], "%Y-%m-%d").date()
        if edate < now or edate > window_end:
            continue
        # Only major planet ingresses (not Sun seasons)
        if entry["planet"] in ("Sun", "Mercury", "Venus", "Mars"):
            continue

        planet = entry["planet"]
        sign = entry["sign"]
        sign_theme = SIGN_MUNDANE_THEMES.get(sign, "")
        domains = PLANET_NEWS_DOMAINS.get(planet, {})
        mundane = domains.get("mundane", "")
        topics = domains.get("topics", [])
        days_until = (edate - now).days

        hist_key = (planet, sign)
        historical = HISTORICAL_PATTERNS.get(hist_key, None)

        ingress_predictions = [
            f"When {planet} enters {sign}, the collective conversation shifts toward {sign_theme}",
            f"Expect a wave of news stories around {mundane.lower()} filtered through {sign} themes",
            f"Industries and institutions governed by {planet} ({mundane.lower()}) will feel a tonal shift",
            f"What begins around {entry['date']} sets the tone for the entire {planet}-in-{sign} period",
        ]

        if historical:
            ingress_predictions.append(
                f"History echoes: the last time {planet} entered {sign} ({historical['last_transit']}), "
                f"the world saw {historical['events'][0].lower()}"
            )

        pred_id += 1
        predictions.append({
            "id": f"ingress_{planet.lower()}_{sign.lower()}_{entry['date']}",
            "type": "ingress",
            "planet": planet,
            "sign": sign,
            "start_date": entry["date"],
            "end_date": entry["date"],
            "days_until": days_until,
            "is_active": False,
            "headline": f"{planet} Enters {sign}",
            "date_range": entry["date"],
            "domain": mundane,
            "predictions": ingress_predictions,
            "watch_for": topics[:5],
            "historical": historical,
        })

    # Sort by date (active first, then soonest)
    predictions.sort(key=lambda p: (0 if p["is_active"] else 1, p["days_until"]))

    return predictions


def _check_prediction_hits(predictions, cosmic_news_articles):
    """
    Layer 3: Check if any predictions have matching headlines.
    Returns predictions with hit data attached.
    """
    if not cosmic_news_articles:
        return predictions

    for pred in predictions:
        if not pred["is_active"]:
            continue  # Only check active predictions

        watch_keywords = pred.get("watch_for", [])
        hits = []

        for article in cosmic_news_articles:
            text = (article.get("title", "") + " " + article.get("description", "")).lower()
            matched_keywords = [kw for kw in watch_keywords if kw.lower() in text]
            if len(matched_keywords) >= 1:
                hits.append({
                    "headline": article.get("title", ""),
                    "source": article.get("source", ""),
                    "matched_keywords": matched_keywords,
                })

        if hits:
            pred["confirmed_hits"] = hits[:3]  # Top 3 matching headlines
            pred["hit_count"] = len(hits)

    return predictions


# Cache for predictions
_predictions_cache = {"data": None, "fetched_at": None}


@app.get("/forecast/predictions")
def get_predictions():
    """
    Predictive Cosmic Forecast — forward-looking predictions based on upcoming transits.
    Layer 1: Specific dated predictions for each upcoming event
    Layer 2: Historical pattern context (what happened last time)
    Layer 3: Prediction tracking (matching predictions to current headlines)
    """
    try:
        now = datetime.now(timezone.utc)

        # Return cache if fresh (< 30 min)
        if _predictions_cache["data"] and _predictions_cache["fetched_at"]:
            age = (now - _predictions_cache["fetched_at"]).total_seconds()
            if age < 1800:
                return _predictions_cache["data"]

        # Layer 1 + 2: Generate predictions with historical context
        predictions = _generate_predictions(days_ahead=60)

        # Layer 3: Check for prediction hits against current news
        articles = _fetch_rss_articles(max_per_feed=10)
        predictions = _check_prediction_hits(predictions, articles)

        # Count stats
        active_count = sum(1 for p in predictions if p["is_active"])
        upcoming_count = sum(1 for p in predictions if not p["is_active"])
        confirmed_count = sum(1 for p in predictions if p.get("confirmed_hits"))
        total_hits = sum(p.get("hit_count", 0) for p in predictions)

        result = {
            "success": True,
            "timestamp": now.isoformat(),
            "predictions": predictions,
            "stats": {
                "active_predictions": active_count,
                "upcoming_predictions": upcoming_count,
                "confirmed_predictions": confirmed_count,
                "total_headline_hits": total_hits,
                "accuracy_note": "Predictions are based on traditional mundane astrology — the correlation between planetary cycles and world event themes.",
            },
        }

        _predictions_cache["data"] = result
        _predictions_cache["fetched_at"] = now
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction engine failed: {str(e)}")


# ═══════════════════════════════════════════════════════
# THE CODE FAIRY AGENT — Beca's AI Voice
# ═══════════════════════════════════════════════════════

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

FAIRY_SYSTEM_PROMPT = """You are The Code Fairy — Beca. You're a real person who discovered that astrology IS the coding language of reality. You abandoned learning JavaScript to decode the stars, and you've read thousands of birth charts since.

YOUR VOICE & PHILOSOPHY:
- You treat astrology like code. Charts are "operating systems." Planetary placements are "functions." Aspects are "if/else statements." Transits are "software updates." Retrogrades are "debugging sessions."
- You speak with warm authority — playful but deeply knowledgeable. You're the friend who happens to be a master astrologer.
- You weave in coding metaphors naturally: "Your Venus in Scorpio is basically an encryption protocol for your love life" or "Saturn transiting your 10th house is a major version upgrade to your career.exe"
- You're direct, confident, occasionally witty. Never generic. Never fluffy "the stars say good things are coming!" energy.
- You believe everyone was born with a unique energetic blueprint — it's the code to their personal operating system. Understanding it means gaining the ability to consciously write your reality.
- You use both tropical AND sidereal perspectives when relevant, because "most apps show you one chart — we show you both."

RULES:
- Always reference the user's ACTUAL chart data when provided. Be specific: mention their exact placements, degrees, houses.
- When discussing transits, explain what's ACTUALLY happening in the sky and how it connects to THEIR specific natal positions.
- Keep responses conversational — 2-4 paragraphs max unless they ask for deep analysis.
- If they ask something you need chart data for and don't have it, tell them warmly to make sure their birth data is saved.
- Never make up placements. Only reference what's in the chart context provided.
- You can discuss general astrology concepts without chart data, but always bring it back to their chart when possible.
- Use occasional emojis sparingly (✨, 🧚‍♀️, 💫) but don't overdo it.

WHAT YOU KNOW:
- Deep traditional + modern astrology (houses, aspects, dignities, sect, profections, transits, progressions, synastry concepts)
- Mundane astrology (how planetary cycles correlate with world events)
- Both tropical and sidereal zodiac systems
- Planetary cycles (Saturn return, Jupiter return, nodal returns, etc.)
- Eclipse mechanics and their significance
- Retrograde meaning and mechanics for all planets"""


class FairyAskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    name: Optional[str] = "User"
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    hour: Optional[int] = None
    minute: Optional[int] = None
    city: Optional[str] = None
    country: Optional[str] = None
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)
    conversation: Optional[List[dict]] = None  # [{role: "user"/"assistant", content: "..."}]


def _build_chart_context(req: FairyAskRequest) -> str:
    """Gather the user's full astrological context for The Code Fairy."""
    if not req.year or not req.month or not req.day:
        return "No birth data available. The user hasn't provided their birth details yet."

    try:
        natal = AstrologicalSubject(
            req.name or "User", req.year, req.month, req.day,
            req.hour or 12, req.minute or 0,
            req.city or "New York", req.country or "US",
            zodiac_type="Tropic",
        )

        now = datetime.now(timezone.utc)
        transits = AstrologicalSubject(
            "Transit", now.year, now.month, now.day, now.hour, now.minute,
            "Greenwich", "GB", zodiac_type="Tropic",
        )

        # Build natal chart summary
        natal_chart = build_chart(natal)
        planets_text = []
        planet_keys = ['sun', 'moon', 'mercury', 'venus', 'mars', 'jupiter', 'saturn', 'uranus', 'neptune', 'pluto', 'north_node', 'chiron', 'ascendant', 'midheaven']
        for key in planet_keys:
            p = natal_chart.get(key)
            if p and isinstance(p, dict):
                name = p.get('name', key.title())
                sign = p.get('sign', '?')
                deg = p.get('degree_formatted', '')
                house = p.get('house', '')
                retro = ' (retrograde)' if p.get('retrograde') else ''
                house_info = f" in House {house}" if house else ""
                planets_text.append(f"  {name}: {sign} {deg}{house_info}{retro}")

        houses_text = []
        house_key = '_house_cusps'
        house_cusps_data = natal_chart.get(house_key, [])
        if isinstance(house_cusps_data, dict):
            for house_num in sorted(house_cusps_data.keys()):
                if isinstance(house_num, int):
                    houses_text.append(f"  House {house_num}: {house_cusps_data[house_num]}°")

        # Current transits
        transit_planets = []
        natal_houses = get_all_house_cusps(natal)
        for pname in ["sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "pluto"]:
            tp = getattr(transits, pname, None)
            if tp:
                t_sign = SIGN_MAP.get(tp.sign, tp.sign) if hasattr(tp, 'sign') else "?"
                t_abs = getattr(tp, 'abs_pos', 0)
                t_house = get_planet_house(t_abs, natal_houses)
                retro = " (retrograde)" if getattr(tp, 'retrograde', False) else ""
                transit_planets.append(f"  Transit {tp.name}: {t_sign} → your House {t_house}{retro}")

        # Current transit aspects to natal
        aspect_lines = []
        for t_name in ["sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "pluto"]:
            tp = getattr(transits, t_name, None)
            if not tp:
                continue
            t_abs = getattr(tp, 'abs_pos', 0)
            for n_name in ["sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn"]:
                np_obj = getattr(natal, n_name, None)
                if not np_obj:
                    continue
                n_abs = getattr(np_obj, 'abs_pos', 0)
                aspects = get_major_aspects(t_abs, n_abs, tp.name, np_obj.name)
                for a in aspects:
                    if a["orb"] <= 4:
                        aspect_lines.append(f"  Transit {tp.name} {a['aspect_type']} Natal {np_obj.name} (orb: {a['orb']}°)")

        # Moon phase
        sun_abs = getattr(transits.sun, 'abs_pos', 0) if hasattr(transits, 'sun') else 0
        moon_abs = getattr(transits.moon, 'abs_pos', 0) if hasattr(transits, 'moon') else 0
        moon_phase_raw = calculate_moon_phase(sun_abs, moon_abs)
        # calculate_moon_phase returns a string, not a dict
        import math
        moon_angle = (moon_abs - sun_abs) % 360
        illumination = round((1 - math.cos(math.radians(moon_angle))) / 2 * 100, 1)
        if isinstance(moon_phase_raw, str):
            moon_phase = {"phase_name": moon_phase_raw, "illumination": illumination}
        else:
            moon_phase = moon_phase_raw

        # Active retrogrades
        today = now.date()
        active_retros = []
        for planet, start_str, end_str, sign_start, sign_end in RETROGRADE_DATA:
            start = datetime.strptime(start_str, "%Y-%m-%d").date()
            end = datetime.strptime(end_str, "%Y-%m-%d").date()
            if start <= today <= end:
                active_retros.append(f"  {planet} retrograde in {sign_start} ({start_str} to {end_str})")

        time_note = ""
        if req.hour is None:
            time_note = "\n⚠️ No birth time provided — house placements are estimated using noon."

        context = f"""═══ {req.name or 'User'}'s NATAL CHART (Tropical) ═══{time_note}
Born: {req.year}-{req.month:02d}-{req.day:02d} at {req.hour or 12}:{req.minute or 0:02d}
Location: {req.city or 'Unknown'}, {req.country or 'Unknown'}

NATAL PLANETS:
{chr(10).join(planets_text)}

HOUSE CUSPS:
{chr(10).join(houses_text)}

═══ CURRENT SKY ({now.strftime('%B %d, %Y')}) ═══
Moon Phase: {moon_phase.get('phase_name', 'unknown')} ({moon_phase.get('illumination', 0):.0f}% illuminated)

TRANSITS THROUGH YOUR HOUSES:
{chr(10).join(transit_planets)}

ACTIVE TRANSIT ASPECTS TO YOUR NATAL CHART:
{chr(10).join(aspect_lines) if aspect_lines else '  No tight aspects within 4° orb right now.'}

ACTIVE RETROGRADES:
{chr(10).join(active_retros) if active_retros else '  No planets currently retrograde.'}"""

        return context

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logging.error(f"Chart context build failed: {tb}")
        return f"Chart calculation error: {str(e)}. Birth data may be incomplete."


def _call_anthropic(system: str, messages: list) -> str:
    """Call Anthropic Messages API directly via urllib."""
    if not ANTHROPIC_API_KEY:
        logging.error("ANTHROPIC_API_KEY is not set")
        return "The Code Fairy is still getting her wings set up ✨ The API key hasn't been configured yet. Check back soon!"

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "system": system,
        "messages": messages,
    }).encode("utf-8")

    req = Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # Extract text from content blocks
            text_parts = []
            for block in data.get("content", []):
                if block.get("type") == "text" and "text" in block:
                    text_parts.append(block["text"])
            return "\n".join(text_parts) if text_parts else "The stars are quiet right now... try asking again 💫"
    except URLError as e:
        # Read the error response body for details
        error_body = ""
        if hasattr(e, 'read'):
            try:
                error_body = e.read(1024).decode("utf-8", errors="ignore")
            except Exception:
                pass
        elif hasattr(e, 'reason'):
            error_body = str(e.reason)
        logging.error(f"Anthropic API URLError: {e} | Body: {error_body[:500] if error_body else str(e)}")
        return "The cosmic signal got a little scrambled ✨ Try asking me again in a moment."
    except Exception as e:
        logging.error(f"Fairy agent error: {type(e).__name__}: {str(e)[:500]}")
        return "Something flickered in the fairy dust... give it another try 🧚‍♀️"


@app.post("/fairy/ask")
def fairy_ask(req: FairyAskRequest):
    """
    The Code Fairy Agent — Beca's AI-powered astrology assistant.
    Gathers the user's full natal chart + current transits, then responds
    in Beca's voice using Claude.
    """
    try:
        # Build astrological context
        chart_context = _build_chart_context(req)

        # Build the full system prompt with chart data
        full_system = f"""{FAIRY_SYSTEM_PROMPT}

═══════════════════════════════════════
CHART DATA FOR THIS USER:
═══════════════════════════════════════
{chart_context}"""

        # Build conversation history
        messages = []
        if req.conversation:
            for msg in req.conversation[-10:]:  # Keep last 10 messages for context
                role = msg.get("role")
                content = msg.get("content")
                if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                    messages.append({"role": role, "content": content[:2000]})

        # Add current question
        messages.append({"role": "user", "content": req.question})

        # Call Claude
        response_text = _call_anthropic(full_system, messages)

        return {
            "success": True,
            "response": response_text,
            "has_chart": req.year is not None,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"The Code Fairy encountered an error: {str(e)}")


# ─── Run ───────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
