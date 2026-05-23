"""
logger.py  -  Lightweight batched logger for MicroPython / Pico W
=================================================================
Sends log entries to Honeycomb (https://honeycomb.io) via
HTTPS POST in batches to minimise TLS overhead.

Every log call also prints to serial so existing debug output is preserved.

Usage:
    import logger
    logger.info("boot complete")
    logger.warn("NTP attempt 2 failed: " + str(e))
    logger.error("OpenSky HTTP 401")
    logger.flush()          # force-send immediately (e.g. before a fatal exit)
    logger.flush_if_due()   # call each main-loop iteration - sends when batch is ready
"""

import utime
import ujson

try:
    import urequests
    _has_urequests = True
except ImportError:
    _has_urequests = False

try:
    from secrets import HONEYCOMB_API_KEY
except ImportError:
    HONEYCOMB_API_KEY = None

# ── Configuration ─────────────────────────────────────────────────────────────

HONEYCOMB_URL    = "https://api.honeycomb.io/1/batch/flight_tracker"
DEVICE_NAME      = "pico-flight"
BATCH_SIZE       = 10     # flush when this many entries are buffered
MAX_BUFFER       = 20     # hard cap - oldest entries dropped when exceeded
FLUSH_INTERVAL_S = 30     # also flush if this many seconds have passed

# ── Internal state ────────────────────────────────────────────────────────────

_buffer       = []
_last_flush_t = 0

# ── Helpers ───────────────────────────────────────────────────────────────────

def _timestamp():
    """Return an ISO-8601 UTC string, or a T+uptime fallback if NTP not synced."""
    t = utime.localtime()
    if t[0] < 2024:
        return "T+" + str(utime.time()) + "s"
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}Z".format(
        t[0], t[1], t[2], t[3], t[4], t[5]
    )

def _print(level, msg):
    print("[" + level.upper() + "] " + _timestamp() + " " + msg)

# ── Public API ────────────────────────────────────────────────────────────────

def log(level, msg):
    """Buffer a log entry and print it to serial immediately."""
    global _buffer
    _print(level, msg)
    entry = {
        "time": _timestamp(),
        "data": {
            "level":   level,
            "message": msg,
            "device":  DEVICE_NAME,
        }
    }
    _buffer.append(entry)
    if len(_buffer) > MAX_BUFFER:
        dropped = _buffer.pop(0)
        _print("warn", "Log buffer full - dropped oldest: " + dropped["data"]["message"][:40])

def info(msg):
    log("info", msg)

def warn(msg):
    log("warn", msg)

def error(msg):
    log("error", msg)

def flush():
    """Send all buffered entries to Honeycomb immediately. Silent on failure."""
    global _buffer, _last_flush_t
    if not _buffer:
        _last_flush_t = utime.time()
        return
    if not HONEYCOMB_API_KEY or not _has_urequests:
        _buffer = []
        _last_flush_t = utime.time()
        return
    payload = ujson.dumps(_buffer)
    _print("info", "Payload: " + payload)
    headers = {
        "X-Honeycomb-Team": HONEYCOMB_API_KEY,
        "Content-Type":     "application/json",
    }
    try:
        resp = urequests.post(HONEYCOMB_URL, data=payload, headers=headers)
        status = resp.status_code
        body = resp.text
        resp.close()
        if status == 200:
            _buffer = []
            _print("info", "Flushed to Honeycomb OK")
        else:
            _print("warn", "Honeycomb flush failed: " + str(status) + " " + body)
    except Exception as e:
        _print("warn", "Honeycomb flush exception: " + str(e))
        _last_flush_t = utime.time()

def flush_if_due():
    """Call once per main-loop iteration. Flushes when batch is full or interval elapsed."""
    now = utime.time()
    if len(_buffer) >= BATCH_SIZE or (now - _last_flush_t) >= FLUSH_INTERVAL_S:
        flush()
