# Pimoroni Galactic Unicorn Flight Tracker

Display real-time overhead flight information on the 53×11 LED matrix of a [Pimoroni Galactic Unicorn](https://shop.pimoroni.com/products/space-unicorns?variant=40842033561683), written in MicroPython.

---

## Features

- Polls the free [OpenSky Network API](https://opensky-network.org/data/api) every 60 seconds for aircraft within a configurable radius of your location
- Enriches each flight with real-time route, airline, and aircraft type data from the [FlightAware AeroAPI](https://www.flightaware.com/commercial/aeroapi/)
- Scrolls per-plane details across the display: callsign · airline · aircraft type · origin › destination · altitude · speed
- Colour-coded display segments for quick reading at a glance
- Smart API cost controls:
  - FlightAware results cached to flash for 7 days — survives reboots
  - Configurable monthly call limit to guard against unexpected charges, after which will automatically shut down
- Button controls for scroll speed and manual refresh
- Structured logging to [Honeycomb](https://www.honeycomb.io) for remote monitoring and error visibility, with graceful degradation to serial output if unavailable

## Display colours

| Colour | Content |
|--------|---------|
| Cyan | Callsign |
| Orange | Airline name · aircraft type |
| White | Origin › destination |
| Yellow | Altitude (ft) · speed (knots) |
| Red | Errors / no planes overhead |

## Button controls

| Button | Action |
|--------|--------|
| A | Force immediate refresh |
| B | Increase scroll speed |
| C | Decrease scroll speed |
| D | Show IP address |
| Brightness wheel | Adjusts display brightness |

---

## Requirements

- [Pimoroni Galactic Unicorn](https://shop.pimoroni.com/products/space-unicorns?variant=40842033561683)
- Pimoroni MicroPython firmware (see Setup below)
- OpenSky Network account (free)
- FlightAware AeroAPI key (free tier: 1000 calls/month)
- Honeycomb account (free tier sufficient)

---

## Setup

### 1. Flash the firmware

Download the latest Pimoroni MicroPython `.uf2` from the [Pimoroni releases page](https://github.com/pimoroni/pimoroni-pico/releases) and flash it to your Galactic Unicorn:

1. Hold the **BOOTSEL** button while plugging in the USB cable
2. The Unicorn will appear as a USB drive
3. Drag the `.uf2` file onto it — it will reboot automatically

### 2. Configure secrets

Copy `secrets_template.py` to `secrets.py` and fill in your details:

```python
WIFI_SSID     = "your_wifi_network"
WIFI_PASSWORD = "your_wifi_password"

MY_LAT = 51.4769    # your latitude
MY_LON =  0.0000    # your longitude

OPENSKY_CLIENT_ID     = "your-opensky-client-id"
OPENSKY_CLIENT_SECRET = "your-opensky-client-secret"

AEROAPI_KEY = "your-flightaware-aeroapi-key"

HONEYCOMB_API_KEY = "your-honeycomb-api-key"
```

### 3. Get an OpenSky API client

1. Register at [opensky-network.org](https://opensky-network.org)
2. Go to **Account → API Clients → Create new client**
3. Copy the `client_id` and `client_secret` into `secrets.py`

OpenSky is free for personal, non-commercial use. See [Licence notes](#licence-notes) below.

### 4. Get a FlightAware AeroAPI key

1. Sign up at [flightaware.com/commercial/aeroapi](https://www.flightaware.com/commercial/aeroapi/)
2. Create an API key in the portal
3. Copy it into `secrets.py` as `AEROAPI_KEY`

> ⚠️ **FlightAware charges $0.005 per API call beyond the free tier.** The code caches results aggressively and enforces a configurable monthly call limit (`FA_MONTHLY_LIMIT` in `main.py`, default 1000) to minimise cost, but **you are responsible for any charges incurred. Use at your own risk.**

### 5. Set up Honeycomb logging

1. Sign up at [honeycomb.io](https://www.honeycomb.io) (free tier is sufficient)
2. Copy your API key into `secrets.py` as `HONEYCOMB_API_KEY`

The `flight_tracker` dataset will be created automatically on the first log event. 

> If `HONEYCOMB_API_KEY` is absent from `secrets.py`, the logger degrades silently to serial output only — the rest of the application is unaffected.

### 6. Deploy to the Galactic Unicorn

Install `mpremote` if needed:

```bash
pip install mpremote
```

Then copy the files to the device:

```bash
mpremote connect auto cp logger.py main.py secrets.py :
mpremote connect auto reset
```

The display will show the WiFi IP address on successful connection, then begin scrolling flight data.

---

## Configuration

Key constants at the top of `main.py` you may want to adjust:

| Constant | Default | Description |
|----------|---------|-------------|
| `RADIUS_KM` | `10` | Search radius around your location in km |
| `REFRESH_SECS` | `60` | How often to poll OpenSky for new planes |
| `FA_MONTHLY_LIMIT` | `1000` | Maximum FlightAware calls per calendar month |
| `FA_CACHE_SECS` | `7 days` | How long to cache successful FlightAware lookups |
| `FA_CACHE_MISS_SECS` | `4 hours` | How long to cache failed lookups before retrying |

Logger constants at the top of `logger.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `BATCH_SIZE` | `10` | Log entries to buffer before flushing to Honeycomb |
| `FLUSH_INTERVAL_S` | `30` | Maximum seconds between Honeycomb flushes |
| `MAX_BUFFER` | `20` | Hard cap on buffered entries — oldest dropped if exceeded |

---

## Monitoring

All log events are sent to Honeycomb in batches, including startup, WiFi and NTP status, API call counts, errors, and fatal exceptions. Each event includes `level`, `message`, and `device` fields.

To view logs, go to [ui.honeycomb.io](https://ui.honeycomb.io), select the `flight_tracker` dataset, and use **Query** to filter by level or message content. For example, to see only errors:

- **WHERE** `level` = `error`

Fatal exceptions (unhandled crashes) are flushed immediately rather than waiting for the next batch cycle, so they will always appear in Honeycomb even if the device does not recover.

---

## Development

To run without permanently deploying (useful for testing changes):

```bash
mpremote connect auto run main.py
```

To monitor serial output while the script runs:

```bash
mpremote connect auto repl
```

Then press `Ctrl+D` to soft-reboot and watch output from startup. Press `Ctrl+C` to interrupt.

The FlightAware cache is stored as `fa_cache.json` on the device. To inspect or clear it:

```bash
# View cache contents
mpremote connect auto cat fa_cache.json

# Delete the cache (forces fresh lookups)
mpremote connect auto rm :fa_cache.json
```

---

## Licence notes

**This project:** [MIT Licence](https://opensource.org/license/mit)

**OpenSky Network API:** Free for personal and non-commercial use. Any commercial or operational use requires a written licence from OpenSky Network. See their [terms of use](https://opensky-network.org/about/terms-of-use) for full details.

**FlightAware AeroAPI:** Commercial service — charges apply beyond the free tier. See [FlightAware's terms](https://www.flightaware.com/about/termsofuse) for details.

**Honeycomb:** Free tier supports up to 20 million events per month. See [Honeycomb's pricing](https://www.honeycomb.io/pricing) for details.

---

## Acknowledgements

- [Pimoroni](https://pimoroni.com) for the Galactic Unicorn hardware and MicroPython firmware
- [OpenSky Network](https://opensky-network.org) for the free ADS-B flight data API
- [FlightAware](https://www.flightaware.com) for the AeroAPI flight enrichment data
- [Honeycomb](https://www.honeycomb.io) for the logging and observability platform
- [Claude Sonnet 4.6](https://anthropic.com) for writing the initial version of this code and iterating on it through conversation
