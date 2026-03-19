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
  GET  /health             — Health check
"""

import os
import logging
from datetime import datetime, timezone, timedelta
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
    lat: float | None = None
    lng: float | None = None


class MoodEntry(BaseModel):
    user_id: str
    date: str  # ISO date
    emotions: list[str]  # list of selected emotions
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

    return planets


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


def estimate_next_conjunction_date(natal_position, transit_position, planet_name):
    """Estimate when transit planet will be conjunct natal position.

    Args:
        natal_position: Natal planet absolute degree
        transit_position: Current transit planet absolute degree
        planet_name: Name of transit planet

    Returns:
        ISO format date string
    """
    # Calculate angular distance remaining for next conjunction
    distance = (natal_position - transit_position) % 360

    # Get daily motion
    daily_motion = PLANET_DAILY_MOTION.get(planet_name, 0.5)
    if daily_motion == 0:
        daily_motion = 0.5

    # Calculate days until exact
    days_remaining = distance / daily_motion

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
            sidereal_mode="FAGAN_BRADLEY",
        )

        return {
            "success": True,
            "timestamp": now.isoformat(),
            "tropical": build_chart(tropical),
            "sidereal": build_chart(sidereal),
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
            # (natal_planet_name, transit_planet_name, aspect_type, orb, description)
            ("saturn", "saturn", "conjunction", 2.0, "A major life restructuring cycle occurring approximately every 29.5 years."),
            ("jupiter", "jupiter", "conjunction", 1.5, "A growth and expansion cycle occurring approximately every 12 years."),
            ("saturn", "saturn", "square", 2.0, "A challenging learning phase occurring approximately every 7.4 years."),
            ("saturn", "saturn", "opposition", 2.0, "A peak manifestation of Saturn's lessons, occurring around age 14-15."),
            ("chiron", "chiron", "conjunction", 2.0, "A profound healing and integration cycle occurring approximately every 50 years."),
            ("uranus", "uranus", "opposition", 2.0, "A period of radical change and liberation occurring around age 42."),
            ("neptune", "neptune", "square", 2.0, "A dissolution and spiritual awakening phase occurring around age 41."),
            ("pluto", "pluto", "square", 3.0, "A period of deep transformation occurring approximately every 60-80 years."),
        ]

        cycles = []

        for natal_pname, transit_pname, aspect_type, orb_limit, description in cycles_to_check:
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

                # Determine status
                if orb <= orb_limit:
                    status = "active"
                elif orb <= orb_limit + 5:  # Approaching within 5 degrees beyond orb
                    status = "approaching"
                else:
                    status = "distant"

                # Estimate next exact date
                estimated_date = estimate_next_conjunction_date(natal_degree, transit_degree, transit_planet.name)

                # Format degree
                degree_info = format_degrees(natal_pos)

                cycles.append({
                    "name": f"{transit_planet.name} {aspect_type.title()}",
                    "type": aspect_type,
                    "transit_planet": transit_planet.name,
                    "natal_planet": natal_planet.name,
                    "natal_degree": f"{degree_info['degree_formatted']} {natal_sign}",
                    "transit_degree": f"{degree_info['degree_formatted']} {natal_sign}",
                    "orb": orb,
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


# ─── Run ───────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
