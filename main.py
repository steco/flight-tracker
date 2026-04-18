"""
flight_tracker.py  –  Galactic Unicorn Overhead Flight Tracker
================================================================
Connects to Wi-Fi, polls the free OpenSky Network API for aircraft
in a bounding box around YOUR_LAT / YOUR_LON, then scrolls the
details of each plane across the 53x11 LED matrix.

SETUP
-----
1. Flash the latest Pimoroni MicroPython UF2 onto your Galactic Unicorn
   (from https://github.com/pimoroni/unicorn/releases)
2. Edit the constants below (Wi-Fi, location, radius, OpenSky credentials).
3. Copy this file to your Galactic Unicorn as  main.py  (via Thonny or mpremote).

FREE API  - register at https://opensky-network.org then visit your Account
page to create an API client and get your CLIENT_ID and CLIENT_SECRET.
Authentication uses OAuth2 client credentials (username/password no longer works).

DISPLAY
-------
  White  scrolling banner while waiting / between refreshes
  Cyan   callsign
  Yellow altitude (ft) and speed (kts)
  Green  heading arrow + degrees, distance from you
  Orange country of registration
  Red    error messages / "No planes overhead"

BUTTONS (on the back of the board)
  A  - force immediate refresh
  B  - increase scroll speed
  C  - decrease scroll speed
  Brightness wheel works automatically
"""

import network
import urequests
import utime
import math
import json
from galactic import GalacticUnicorn
from picographics import PicoGraphics, DISPLAY_GALACTIC_UNICORN as DISPLAY

# ─────────────────────────────────────────────────────────────────────────────
#  USER CONFIGURATION  –  edit secrets.py, not this file
# ─────────────────────────────────────────────────────────────────────────────
try:
    from secrets import (
        WIFI_SSID, WIFI_PASSWORD,
        MY_LAT, MY_LON,
        OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET,
        AEROAPI_KEY,
    )
except ImportError:
    raise SystemExit("secrets.py not found – copy secrets_template.py and fill it in")

RADIUS_KM = 10      # Half-width of the search bounding box in km

REFRESH_SECS = 60   # How often to re-poll the flight data API

# ─────────────────────────────────────────────────────────────────────────────
#  Derived constants  (do not edit)
# ─────────────────────────────────────────────────────────────────────────────

DEG_PER_KM_LAT = 1.0 / 111.0
DEG_PER_KM_LON = 1.0 / (111.0 * math.cos(math.radians(MY_LAT)))

LAT_MIN = MY_LAT - RADIUS_KM * DEG_PER_KM_LAT
LAT_MAX = MY_LAT + RADIUS_KM * DEG_PER_KM_LAT
LON_MIN = MY_LON - RADIUS_KM * DEG_PER_KM_LON
LON_MAX = MY_LON + RADIUS_KM * DEG_PER_KM_LON

# OpenSky OAuth2 token endpoint
TOKEN_URL = (
    "https://auth.opensky-network.org"
    "/auth/realms/opensky-network"
    "/protocol/openid-connect/token"
)

# Refresh the Bearer token this many seconds before it actually expires
TOKEN_REFRESH_MARGIN = 60

# ─────────────────────────────────────────────────────────────────────────────
#  Hardware init
# ─────────────────────────────────────────────────────────────────────────────

gu = GalacticUnicorn()
graphics = PicoGraphics(DISPLAY)
wlan = network.WLAN(network.STA_IF)

DELAY_MS = 50
MIN_DELAY_MS     = 5
MAX_DELAY_MS     = 200

WIDTH  = GalacticUnicorn.WIDTH   # 53
HEIGHT = GalacticUnicorn.HEIGHT  # 11

gu.set_brightness(0.5)

BLACK  = graphics.create_pen(0,   0,   0)
WHITE  = graphics.create_pen(255, 255, 255)
CYAN   = graphics.create_pen(0,   220, 220)
YELLOW = graphics.create_pen(255, 220, 0)
GREEN  = graphics.create_pen(80,  255, 80)
RED    = graphics.create_pen(255, 60,  60)
ORANGE = graphics.create_pen(255, 140, 0)

graphics.set_font("bitmap8")

# ─────────────────────────────────────────────────────────────────────────────
#  Wi-Fi
# ─────────────────────────────────────────────────────────────────────────────

def connect_wifi():
    wlan.active(True)
    if not wlan.isconnected():
        scroll_message("Connecting...", WHITE)
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        timeout = 20
        while not wlan.isconnected() and timeout > 0:
            utime.sleep(1)
            timeout -= 1
    if wlan.isconnected():
        display_wan_status()
        return True
    scroll_message("WiFi FAIL", RED)
    return False

def display_wan_status():
    ip = wlan.ifconfig()[0]
    scroll_message("WiFi OK  " + ip, GREEN)

# ─────────────────────────────────────────────────────────────────────────────
#  Display helpers
# ─────────────────────────────────────────────────────────────────────────────

def measure_text(text):
    return graphics.measure_text(text, scale=1)

def clear():
    graphics.set_pen(BLACK)
    graphics.clear()

def adjust_scroll_speed():
    global DELAY_MS

    if gu.is_pressed(GalacticUnicorn.SWITCH_B):
         DELAY_MS = min(MAX_DELAY_MS, DELAY_MS + 1)
         utime.sleep_ms(50)
    if gu.is_pressed(GalacticUnicorn.SWITCH_C):
         DELAY_MS = max(MIN_DELAY_MS, DELAY_MS - 1)
         utime.sleep_ms(50)

def scroll_message(msg, colour=WHITE, loops=1):
    """Scroll a single-colour message. Returns early if button A is pressed."""
    text_width = measure_text(msg)
    for _ in range(loops):
        x = WIDTH
        while x > -text_width:
            clear()
            graphics.set_pen(colour)
            graphics.text(msg, x, 2, scale=1)
            gu.update(graphics)
            x -= 1
            utime.sleep_ms(DELAY_MS)
            
            adjust_scroll_speed()
            
            if gu.is_pressed(GalacticUnicorn.SWITCH_A):
                return

def scroll_segments(segments):
    """
    Scroll a list of (text, colour_pen) tuples as one joined banner.
    Returns True if button A was pressed (caller should force a refresh).
    """
    full_text   = "  ".join(t for t, _ in segments) + "   "
    total_width = measure_text(full_text)
    x = WIDTH

    while x > -total_width:
        clear()
        draw_x = x
        for text, colour in segments:
            graphics.set_pen(colour)
            graphics.text(text, draw_x, 2, scale=1)
            draw_x += measure_text(text) + measure_text("  ")
        gu.update(graphics)
        x -= 1
        utime.sleep_ms(DELAY_MS)

        adjust_scroll_speed()

        if gu.is_pressed(GalacticUnicorn.SWITCH_A):
            return True
    return False

# ─────────────────────────────────────────────────────────────────────────────
#  OAuth2 token manager
# ─────────────────────────────────────────────────────────────────────────────

_access_token  = None
_token_expires = 0   # utime.time() value at which the cached token becomes stale

def _fetch_token():
    """POST client credentials to OpenSky's token endpoint and cache the result."""
    global _access_token, _token_expires

    scroll_message("Auth...", ORANGE)

    body = (
        "grant_type=client_credentials"
        "&client_id=" + OPENSKY_CLIENT_ID +
        "&client_secret=" + OPENSKY_CLIENT_SECRET
    )
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        resp = urequests.post(TOKEN_URL, data=body, headers=headers)
        status = resp.status_code
        if status != 200:
            resp.close()
            return False, "Token HTTP " + str(status)

        data = resp.json()
        resp.close()

        _access_token  = data["access_token"]
        
        expires_in     = data.get("expires_in", 1800)   # OpenSky tokens last ~30 min
        _token_expires = utime.time() + expires_in - TOKEN_REFRESH_MARGIN
        return True, None

    except Exception as e:
        return False, str(e)

def get_auth_headers():
    """
    Return {"Authorization": "Bearer <token>"}, fetching a fresh token if needed.
    Returns (headers_dict, None) on success or (None, error_string) on failure.
    """
    if _access_token is None or utime.time() >= _token_expires:
        ok, err = _fetch_token()
        if not ok:
            return None, err
    return {"Authorization": "Bearer " + _access_token}, None

# ─────────────────────────────────────────────────────────────────────────────
#  OpenSky flight data
# ─────────────────────────────────────────────────────────────────────────────

def fetch_planes():
    """
    Query OpenSky for airborne aircraft in the configured bounding box.
    Returns (list_of_plane_dicts, None) or (None, error_string).

    Each dict contains: icao24, callsign, country, lat, lon,
                        altitude_m, velocity_ms, heading
    """
    global _access_token

    headers, err = get_auth_headers()
    if err:
        return None, err

    url = (
        "https://opensky-network.org/api/states/all"
        "?lamin=" + str(round(LAT_MIN, 4)) +
        "&lamax=" + str(round(LAT_MAX, 4)) +
        "&lomin=" + str(round(LON_MIN, 4)) +
        "&lomax=" + str(round(LON_MAX, 4))
    )

    try:
        resp = urequests.get(url, headers=headers)

        if resp.status_code == 401:
            # Token was rejected - clear it so _fetch_token() runs next time
            _access_token = None
            resp.close()
            return None, "Token rejected, retry"

        if resp.status_code != 200:
            resp.close()
            return None, "HTTP " + str(resp.status_code)

        data   = resp.json()
        resp.close()
        states = data.get("states") or []

        planes = []
        for s in states:
            # OpenSky state vector field indices:
            # 0  icao24            unique transponder address
            # 1  callsign          flight number / registration
            # 2  origin_country
            # 5  longitude (deg)
            # 6  latitude  (deg)
            # 7  baro_altitude (m)
            # 8  on_ground   (bool)
            # 9  velocity    (m/s)
            # 10 true_track  (deg, clockwise from north)
            if s[8]:
                continue   # skip anything on the ground
            planes.append({
                "icao24":     (s[0] or "??????").strip(),
                "callsign":   (s[1] or "N/A").strip(),
                "country":    (s[2] or "?").strip(),
                "lon":         s[5],
                "lat":         s[6],
                "altitude_m":  s[7]  if s[7]  is not None else 0,
                "velocity_ms": s[9]  if s[9]  is not None else 0,
                "heading":     s[10] if s[10] is not None else 0,
            })
        return planes, None

    except Exception as e:
        return None, str(e)

# ─────────────────────────────────────────────────────────────────────────────
#  Unit conversion helpers
# ─────────────────────────────────────────────────────────────────────────────

def m_to_ft(m):
    return int(m * 3.28084)

def ms_to_kts(ms):
    return int(ms * 1.94384)

def heading_to_arrow(hdg):
    arrows = ["^", "/", ">", "\\", "v", "\\", "<", "/"]
    # Use simpler ASCII arrows for reliable rendering on the bitmap font
    compass = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return compass[int((hdg + 22.5) / 45) % 8]

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# ─────────────────────────────────────────────────────────────────────────────
#  Airline names
# ─────────────────────────────────────────────────────────────────────────────
AIRLINE_NAMES = {
    # UK carriers
    "BAW": "British Airways", "EZY": "easyJet",        "TOM": "TUI",
    "EXS": "Jet2",            "VIR": "Virgin Atlantic", "LOG": "Loganair",
    "SHT": "BA Shuttle",      "CFE": "BA CityFlyer",    "ENT": "Air Transat",
    "VGI": "Virgin Atlantic", "EFW": "BA Euroflyer",    "WUK": "Wizz Air UK",
    # European majors you'll commonly see over Kent
    "RYR": "Ryanair",         "WZZ": "Wizz Air",        "EWG": "Eurowings",
    "AFR": "Air France",      "DLH": "Lufthansa",       "KLM": "KLM",
    "IBE": "Iberia",          "VLG": "Vueling",         "TAP": "TAP Portugal",
    "BEL": "Brussels Air",    "AUA": "Austrian",        "SWR": "Swiss",
    "NAX": "Norwegian",       "SAS": "Scandinavian",    "FIN": "Finnair",
    "AZA": "ITA Airways",     "TRA": "Transavia",       "TVF": "Transavia FR",
    "NOS": "Neos",            "TCX": "Thomas Cook",     "FHY": "Freebird",
    "ITY": "ITA Airways",     "NOS": "Norwegian",       "PTN": "Platoon Aviation",
    "NBT": "Norse Atlantic Airways",
    # Middle East
    "UAE": "Emirates",        "ETD": "Etihad",          "QTR": "Qatar",
    "THY": "Turkish",         "ELY": "El Al",           "SVA": "Saudi",
    # North America
    "AAL": "American",        "UAL": "United",          "DAL": "Delta",
    "ACA": "Air Canada",      "WJA": "WestJet",
    # Asia / Other
    "SIA": "Singapore Air",   "CPA": "Cathay Pacific",  "ANA": "ANA",
    "JAL": "Japan Airlines",  "QFA": "Qantas",          "ETH": "Ethiopian",
    "MSR": "EgyptAir",        "RAM": "Royal Air Maroc", "CES": "China Eastern Airlines",
    # Cargo
    "FDX": "FedEx",           "UPS": "UPS",             "BCS": "European Air",
    "DHL": "DHL Air",
}

def airline_name(code):
    if not code:
        return None
    name = AIRLINE_NAMES.get(code.upper())
    if name is None:
        print("Unknown airline code: " + code)
        return code.upper()
    return name

# ─────────────────────────────────────────────────────────────────────────────
#  FlightAware AeroAPI  -  real-time route, airline, origin, destination
# ─────────────────────────────────────────────────────────────────────────────

# Cache: callsign -> (flight_info_dict, cached_at_unix)
# Keyed by callsign rather than icao24 as AeroAPI is callsign-based.
_fa_cache = {}
FA_CACHE_SECS = 600   # 10 minutes - route won't change mid-flight

def fetch_flightaware(callsign):
    """
    Query FlightAware AeroAPI for the current flight with this callsign.
    Returns a dict with keys: origin, destination, airline  (all may be None).
    Returns None on any network/API failure.

    AeroAPI endpoint: GET /flights/{ident}
    Docs: https://flightaware.com/commercial/aeroapi/documentation
    """
    if not AEROAPI_KEY or AEROAPI_KEY == "YOUR_AEROAPI_KEY":
        return None

    url = "https://aeroapi.flightaware.com/aeroapi/flights/" + callsign
    headers = {"x-apikey": AEROAPI_KEY}

    try:
        print("FlightAware call: " + callsign)
        resp = urequests.get(url, headers=headers)

        if resp.status_code != 200:
            resp.close()
            return None
        data = resp.json()
        resp.close()

        # AeroAPI returns a "flights" list; first entry is the most recent/active
        flights = data.get("flights")
        if not flights:
            return None
        f = flights[0]

        origin      = f.get("origin") or {}
        destination = f.get("destination") or {}

        return {
            "origin":      origin.get("name"),
            "destination": destination.get("name"),
            "airline":     airline_name(f.get("operator") or f.get("airline_iata")),
            "type":        f.get("aircraft_type")
        }

    except Exception as e:
        print("Exception: " + str(e))
        return None

def get_flightaware_cached(callsign, now_unix):
    """Return cached FlightAware info for callsign, fetching fresh if stale."""
    if callsign in _fa_cache:
        info, cached_at = _fa_cache[callsign]
        if now_unix - cached_at < FA_CACHE_SECS:
            return info
    info = fetch_flightaware(callsign)
    _fa_cache[callsign] = (info, now_unix)
    return info

# ─────────────────────────────────────────────────────────────────────────────
#  Build colour-coded display segments for one plane
# ─────────────────────────────────────────────────────────────────────────────

def plane_segments(plane, now_unix):
    callsign  = plane["callsign"] or plane["icao24"]
    alt_ft    = m_to_ft(plane["altitude_m"])
    speed_kts = ms_to_kts(plane["velocity_ms"])
    hdg       = int(plane["heading"])
    direction = heading_to_arrow(hdg)

    # Append route info from FlightAware if available
    info = get_flightaware_cached(callsign, now_unix)
    
    segments = []

    org = None
    dst = None
    airline = None

    if info:
        org = info.get("origin")
        dst = info.get("destination")
        airline = info.get("airline")
        aircraft_type = info.get("type")
    
    segments.append((callsign, CYAN))

    if airline:
        segments.append((airline, ORANGE))

    if aircraft_type:
        segments.append((aircraft_type, ORANGE))

    if org and dst:
        segments.append((org + " > " + dst, WHITE))
    elif org:
        segments.append(("from " + org, WHITE))
    elif dst:
        segments.append(("to " + dst, WHITE))
    
    segments.append((str(alt_ft) + " ft",     YELLOW))
    segments.append((str(speed_kts) + " knots", YELLOW))

    return segments

# ─────────────────────────────────────────────────────────────────────────────
#  Main loop
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("Starting...")
    if not connect_wifi():
        scroll_message("Check WiFi settings", RED, loops=3)
        return

    last_refresh = -REFRESH_SECS   # ensure an immediate fetch on startup
    planes  = []
    err_msg = None

    while True:
        now = utime.time()

        # ── Refresh flight data ───────────────────────────────────────────
        force = gu.is_pressed(GalacticUnicorn.SWITCH_A)
        if force or (now - last_refresh) >= REFRESH_SECS:
            scroll_message("Fetching...", ORANGE)
            result, err = fetch_planes()
            if result is not None:
                planes  = result
                err_msg = None
            else:
                err_msg = err
            last_refresh = utime.time()

        # ── Display WAN config (IP address)────────────────────────────────
        if gu.is_pressed(GalacticUnicorn.SWITCH_D):
            display_wan_status()

        # ── Render ────────────────────────────────────────────────────────
        if err_msg:
            scroll_message("Err: " + err_msg, RED)

        elif not planes:
            scroll_message("No planes overhead", WHITE)

        else:
            for plane in planes:
                if scroll_segments(plane_segments(plane, now)):
                    last_refresh = -REFRESH_SECS
                    break

main()
