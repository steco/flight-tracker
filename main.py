"""
Flight Tracker -  Galactic Unicorn Overhead Flight Tracker
================================================================
Connects to Wi-Fi, polls the free OpenSky Network API for aircraft
in a bounding box around YOUR_LAT / YOUR_LON, then scrolls the
details of each plane across the 53x11 LED matrix.

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
import ntptime
import math
import gc
import ujson
from galactic import GalacticUnicorn
from picographics import PicoGraphics, DISPLAY_GALACTIC_UNICORN as DISPLAY
import logger

# ─────────────────────────────────────────────────────────────────────────────
#  USER CONFIGURATION  -  edit secrets.py, not this file
# ─────────────────────────────────────────────────────────────────────────────
try:
    from secrets import (
        WIFI_SSID, WIFI_PASSWORD,
        MY_LAT, MY_LON,
        OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET,
        AEROAPI_KEY,
    )
except ImportError:
    raise SystemExit("secrets.py not found - copy secrets_template.py and fill it in")

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

# FlightAware cache TTLs (in seconds)
FA_CACHE_SECS      = 7 * 24 * 60 * 60   # 7 days  - route data doesn't change
FA_CACHE_MISS_SECS = 4 * 60 * 60         # 4 hours - retry failed lookups

# Path on the Pico filesystem where the FA cache is persisted
CACHE_FILE = "fa_cache.json"

# Maximum FlightAware API calls per calendar month (free tier limit)
FA_MONTHLY_LIMIT = 1000

# ─────────────────────────────────────────────────────────────────────────────
#  Hardware init
# ─────────────────────────────────────────────────────────────────────────────

gu = GalacticUnicorn()
graphics = PicoGraphics(DISPLAY)
wlan = network.WLAN(network.STA_IF)

DELAY_MS     = 50
MIN_DELAY_MS = 5
MAX_DELAY_MS = 200

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
#  Wi-Fi and NTP
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
        sync_ntp()
        display_wan_status()
        return True
    logger.error("WiFi connect failed - check SSID/password in secrets.py")
    scroll_message("WiFi FAIL", RED)
    return False

def sync_ntp():
    """Sync the Pico's RTC to real UTC time via NTP. Retries up to 3 times."""
    for attempt in range(3):
        try:
            ntptime.settime()
            t = utime.localtime()
            logger.info("NTP sync OK - {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
                t[0], t[1], t[2], t[3], t[4], t[5]
            ))
            return True
        except Exception as e:
            logger.warn("NTP attempt " + str(attempt + 1) + " failed: " + str(e))
            utime.sleep(2)
    # Non-fatal - log it and carry on. Cache TTLs will be wrong but
    # everything else will still work.
    logger.error("NTP sync failed after 3 attempts - timestamps may be unreliable")
    scroll_message("NTP FAIL", RED)
    return False

def display_wan_status():
    ip = wlan.ifconfig()[0]
    logger.info("WiFi OK - IP: " + ip)
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
_token_expires = 0

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
            logger.error("OpenSky token request failed - HTTP " + str(status))
            return False, "Token HTTP " + str(status)

        data = resp.json()
        resp.close()

        _access_token  = data["access_token"]
        expires_in     = data.get("expires_in", 1800)
        _token_expires = utime.time() + expires_in - TOKEN_REFRESH_MARGIN
        logger.info("OpenSky token refreshed - expires in " + str(expires_in) + "s")
        return True, None

    except Exception as e:
        logger.error("OpenSky token exception: " + str(e))
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
            _access_token = None
            resp.close()
            logger.warn("OpenSky token rejected (401) - will re-auth on next poll")
            return None, "Token rejected, retry"

        if resp.status_code != 200:
            resp.close()
            logger.error("OpenSky states/all HTTP " + str(resp.status_code))
            return None, "HTTP " + str(resp.status_code)

        data   = resp.json()
        resp.close()
        states = data.get("states") or []

        planes = []
        for s in states:
            if s[8]:
                continue
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
        logger.info("OpenSky poll OK - " + str(len(planes)) + " planes")
        return planes, None

    except Exception as e:
        logger.error("OpenSky fetch exception: " + str(e))
        return None, str(e)

# ─────────────────────────────────────────────────────────────────────────────
#  Unit conversion helpers
# ─────────────────────────────────────────────────────────────────────────────

def m_to_ft(m):
    return int(m * 3.28084)

def ms_to_kts(ms):
    return int(ms * 1.94384)

def heading_to_arrow(hdg):
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
    "HLE": "Air Ambulance",   "SYG": "Ascend Airways",  "UBT": "Norse Atlantic UK",
    "AUR": "Aurigny",         "AWC": "Titan Airways",   "RRR": "Royal Air Force",
    "RUK": "Ryanair UK",      "BAF": "Bristow Helicopters",
    # European
    "RYR": "Ryanair",         "WZZ": "Wizz Air",        "EWG": "Eurowings",
    "AFR": "Air France",      "DLH": "Lufthansa",       "KLM": "KLM",
    "IBE": "Iberia",          "VLG": "Vueling",         "TAP": "TAP Portugal",
    "BEL": "Brussels Air",    "AUA": "Austrian",        "SWR": "Swiss",
    "NAX": "Norwegian",       "SAS": "Scandinavian",    "FIN": "Finnair",
    "AZA": "ITA Airways",     "TRA": "Transavia",       "TVF": "Transavia FR",
    "NOS": "Neos",            "TCX": "Thomas Cook",     "FHY": "Freebird",
    "ITY": "ITA Airways",     "NOZ": "Norwegian",       "PTN": "Platoon Aviation",
    "NBT": "Norse Atlantic",  "SXS": "SunExpress",      "EJU": "easyJet Europe",
    "HOP": "Air France Hop",  "AHY": "Finnair",         "BTI": "airBaltic",
    "CFG": "Condor",          "EIN": "Aer Lingus",      "EZS": "easyJet Switzerland",
    "KMM": "KM Malta",        "LHX": "Lufthansa City",  "LXJ": "Flexjet",
    "NSZ": "Norwegian Sweden","VJH": "VistaJet",        "AEE": "Aegean Airlines",
    "OAW": "Helvetic Airways","ICE": "Icelandair",      "CTN": "Croatia Airlines",
    "TSC": "Air Transat",     "CHG": "Challenge Airlines","AWG": "Animawings",
    "SEH": "Skyeurope",       "NJE": "NetJets Europe",  "NJU": "NetJets",
    "VJT": "VistaJet",        "CCA": "Air China",       "CND": "Aero Continente",
    "CMB": "Chamberlain Aviation","RYS": "Buzz",         "RWD": "Rwandair",
    "RXI": "Riyadh Air",      "SRD": "Surinam Airways", "URO": "Uro Aviation",
    "PGT": "Pegasus Airlines","CTM": "French Air Force", "SCO": "Air Scotland",
    "TAY": "TNT Airways",     "THA": "Thai Airways",    "MMD": "Air Malta",
    "LCO": "Loch Lomond Seaplanes","LHB": "Lufthansa",  "ECA": "EasyJet",
    "EDW": "Edelweiss Air",   "FCA": "First Choice",    "FFC": "Air Finland",
    "FJO": "Freedom Airlines","FPO": "Flight Precision","GAC": "Global Aviation",
    "GCK": "GeckoAir",        "GEC": "Lufthansa Cargo", "HGO": "Hageland Aviation",
    "JCO": "Jetsgo",          "JFA": "Jet4you",         "NCR": "National Airlines",
    "NPT": "Nile Air",        "OCN": "Ocean Airlines",  "OMA": "Oman Air",
    "PVD": "Providencia",     "QNT": "Quantum Air",     "RJB": "Royal Jet",
    "SAH": "Yemenia",         "SFS": "Safi Airways",    "SUA": "Sun Air",
    "SXN": "Saxonair",        "TAR": "Tunisair",        "XRO": "Xtra Airways",
    "VPC": "Viapontica",      "AIH": "Airest",          "BBB": "TUI Belgium",
    "CKS": "Conair",          "CLF": "Clifden Air",     "DNU": "Danube Wings",
    "RUK": "Ryanair UK",      "SFS": "Safi Airways",    "IBS": "Iberia Express",
    "AJT": "AJet",            "WMT": "Wizz Air Malta",  "BOX": "AeroLogic",
    # Middle East
    "UAE": "Emirates",        "ETD": "Etihad",          "QTR": "Qatar",
    "THY": "Turkish",         "ELY": "El Al",           "SVA": "Saudi",
    "RJA": "Royal Jordanian", "KAC": "Kuwait Airways",
    # North America
    "AAL": "American",        "UAL": "United",          "DAL": "Delta",
    "ACA": "Air Canada",      "WJA": "WestJet",         "EVA": "EVA Air",
    "GTI": "Atlas Air",
    # Asia / Other
    "SIA": "Singapore Air",   "CPA": "Cathay Pacific",  "ANA": "ANA",
    "JAL": "Japan Airlines",  "QFA": "Qantas",          "ETH": "Ethiopian",
    "MSR": "EgyptAir",        "RAM": "Royal Air Maroc", "CES": "China Eastern",
    "AIC": "Air India",       "CSN": "China Southern",  "MAS": "Malaysia Airlines",
    "IGO": "IndiGo",          "KAL": "Korean Air",      "BBC": "Biman Bangladesh Airlines",
    "LBT": "Nouvelair",       "TUA": "Turkmenistan Airlines",
    "AAR": "Asiana Airlines",
    # Cargo
    "FDX": "FedEx",           "UPS": "UPS",             "BCS": "European Air",
    "DHL": "DHL Air",
}

def airline_name(code):
    if not code:
        return None
    name = AIRLINE_NAMES.get(code.upper())
    if name is None:
        logger.info("Unknown airline code: " + code)
        return code.upper()
    return name

# ─────────────────────────────────────────────────────────────────────────────
#  FlightAware cache  -  persisted to flash with real Unix timestamps
# ─────────────────────────────────────────────────────────────────────────────

# In-memory cache: callsign -> (info_dict_or_None, cached_at_unix)
_fa_cache = {}

# Monthly API call counter - persisted in the same file as the cache
# Resets automatically when the calendar month changes
_fa_call_count = 0    # calls made this calendar month
_fa_call_month = 0    # month (1-12) the counter belongs to

# Set of non-commercial callsigns we've already logged, to suppress repeat prints
_non_commercial_seen = set()

def load_cache():
    """Load the persisted FA cache and monthly call counter from flash on startup."""
    global _fa_call_count, _fa_call_month
    try:
        with open(CACHE_FILE, "r") as f:
            raw = ujson.load(f)
        for k, v in raw.items():
            if k == "__meta__":
                continue   # skip the metadata entry
            # v is [info, cached_at_unix]  — real Unix timestamps survive restarts
            _fa_cache[k] = (v[0], v[1])
        # Load monthly counter from the metadata entry
        meta = raw.get("__meta__", {})
        _fa_call_count = meta.get("call_count", 0)
        _fa_call_month = meta.get("call_month", 0)
        logger.info("Loaded " + str(len(_fa_cache)) + " FA cache entries from flash")
        logger.info("FA calls this month: " + str(_fa_call_count) + "/" + str(FA_MONTHLY_LIMIT))
    except Exception as e:
        logger.warn("Cache load failed: " + str(e) + " (starting fresh)")

def save_cache():
    try:
        with open(CACHE_FILE, "w") as f:
            f.write('{"__meta__":{"call_count":')
            f.write(str(_fa_call_count))
            f.write(',"call_month":')
            f.write(str(_fa_call_month))
            f.write('}')
            for k, (info, cached_at) in _fa_cache.items():
                f.write(',')
                f.write(ujson.dumps(k))
                f.write(':[')
                f.write(ujson.dumps(info))
                f.write(',')
                f.write(str(cached_at))
                f.write(']')
            f.write('}')
    except Exception as e:
        logger.error("Cache save failed: " + str(e))

def _is_commercial_callsign(callsign):
    """
    Returns True only for callsigns that look like scheduled airline flights
    (3-letter ICAO operator code followed by digits/letters, e.g. BAW123, EZY4BV).
    Filters out registrations like G-ABCD, N12345, and military callsigns.
    """
    if len(callsign) < 4:
        return False
    if not callsign[:3].isalpha():
        return False
    if not any(c.isdigit() for c in callsign[3:]):
        return False
    return True

def fetch_flightaware(callsign):
    """
    Query FlightAware AeroAPI for the current flight with this callsign.
    Returns a dict with keys: origin, destination, airline, type  (all may be None).
    Returns None on any network/API failure or if the monthly call limit is reached.

    AeroAPI endpoint: GET /flights/{ident}
    Docs: https://flightaware.com/commercial/aeroapi/documentation
    """
    if not AEROAPI_KEY or AEROAPI_KEY == "YOUR_AEROAPI_KEY":
        return None

    url = "https://aeroapi.flightaware.com/aeroapi/flights/" + callsign + "?max_pages=1"
    headers = {"x-apikey": AEROAPI_KEY}

    try:
        logger.info("FlightAware call " + str(_fa_call_count) + "/" + str(FA_MONTHLY_LIMIT) + ": " + callsign)
        resp = urequests.get(url, headers=headers)

        if resp.status_code != 200:
            resp.close()
            logger.warn("FlightAware HTTP " + str(resp.status_code) + " for " + callsign)
            return None

        raw = resp.text
        resp.close()
        logger.info("FlightAware response size: " + str(len(raw)) + " bytes for " + callsign)
        data = ujson.loads(raw)
        
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
            "type":        f.get("aircraft_type"),
        }

    except Exception as e:
        logger.error("FlightAware exception for " + callsign + ": " + str(e))
        return None

def get_flightaware_cached(callsign, now_unix):
    """
    Return cached FlightAware info for callsign, fetching fresh if stale.
    Non-commercial callsigns are silently skipped after the first log message.
    Cache is persisted to flash so TTLs survive reboots.
    """
    global _fa_call_count, _fa_call_month

    if not _is_commercial_callsign(callsign):
        if callsign not in _non_commercial_seen:
            logger.info("Non-commercial callsign - skipping FA lookup: " + callsign)
            _non_commercial_seen.add(callsign)
        return None

    if callsign in _fa_cache:
        info, cached_at = _fa_cache[callsign]
        ttl = FA_CACHE_MISS_SECS if info is None else FA_CACHE_SECS
        if now_unix - cached_at < ttl:
            return info

    # Reset counter if we're in a new calendar month
    current_month = utime.localtime()[1]
    if current_month != _fa_call_month:
        logger.info("New month - resetting FA call counter (was " + str(_fa_call_count) + ")")
        _fa_call_count = 0
        _fa_call_month = current_month
        save_cache()

    # Enforce monthly limit
    if _fa_call_count >= FA_MONTHLY_LIMIT:
        logger.warn("FA monthly limit reached (" + str(FA_MONTHLY_LIMIT) + ") - skipping: " + callsign)
        return None

    _fa_call_count += 1
    info = fetch_flightaware(callsign)
    _fa_cache[callsign] = (info, now_unix)
    save_cache()   # persist immediately after every new fetch
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

    org = None
    dst = None
    airline = None
    aircraft_type = None

    if info:
        org = info.get("origin")
        dst = info.get("destination")
        airline = info.get("airline")
        aircraft_type = info.get("type")

    segments = []

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

    segments.append((str(alt_ft) + " ft",      YELLOW))
    segments.append((str(speed_kts) + " knots", YELLOW))

    return segments

# ─────────────────────────────────────────────────────────────────────────────
#  Main loop
# ─────────────────────────────────────────────────────────────────────────────

def main():
    logger.info("Flight tracker starting")
    if not connect_wifi():
        scroll_message("Check WiFi settings", RED, loops=3)
        return

    # Load persisted cache now that we have a real clock from NTP
    load_cache()

    last_refresh = -REFRESH_SECS
    planes  = []
    err_msg = None

    while True:
        now = utime.time()

        if not wlan.isconnected():
            logger.warn("WiFi dropped - reconnecting")
            connect_wifi()
            
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

        # ── Display WAN config (IP address) ───────────────────────────────
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

        # ── Memory management ─────────────────────────────────────────────
        gc.collect()
        if gc.mem_free() < 40000:
            # Evict the oldest 5 entries rather than clearing everything
            if _fa_cache:
                oldest = sorted(_fa_cache.items(), key=lambda x: x[1][1])[:5]
                for k, _ in oldest:
                    del _fa_cache[k]
                save_cache()
                gc.collect()
                logger.warn("Low memory - evicted 5 oldest FA cache entries, free: " + str(gc.mem_free()))

        # ── Ship any buffered log entries ─────────────────────────────────
        logger.flush_if_due()


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point  -  top-level exception catch ensures fatal exits are logged
# ─────────────────────────────────────────────────────────────────────────────

try:
    main()
except Exception as e:
    logger.error("FATAL: main() exited with exception: " + str(e))
    logger.flush()   # force-send immediately - don't wait for the batch cycle
    try:
        scroll_message("FATAL: " + str(e), RED, loops=5)
    except Exception:
        pass  # display may itself be broken - don't mask the original error
    raise   # re-raise so the REPL/watchdog sees the full traceback
