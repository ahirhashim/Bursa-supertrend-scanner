import os
import time
import threading
import sqlite3
import json
from datetime import datetime, timezone, timedelta

import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# ============================================================
# APP
# ============================================================
app = FastAPI(title="Bursa Supertrend Scanner")

# ============================================================
# ITICK
# ============================================================
ITICK_API_KEY = os.getenv("ITICK_API_KEY", "")
ITICK_KLINE_URL = "https://api-free.itick.org/stock/kline"
ITICK_BATCH_QUOTE_URL = "https://api-free.itick.org/stock/quotes"

# iTick Base = 5 calls/minute.
# 20 saat antara request memberi ruang yang lebih selamat.
# PAKSA 20 SAAT ANTARA SETIAP REQUEST ITICK.
ITICK_REQUEST_DELAY = 20.0

ITICK_MAX_RETRIES = 0

# ============================================================
# SUPERTREND
# ============================================================
ATR_LENGTH = 10
SUPERTREND_FACTOR = 1.0

# ============================================================
# SCANNER
# ============================================================
DEFAULT_BATCH_SIZE = 5
RECENT_SIGNAL_DAYS = 5
VOLUME_BATCH_SIZE = 10

# ============================================================
# DATABASE
# ============================================================
DATA_DIR = os.getenv("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "recent_signals.db")
_db_lock = threading.Lock()


def get_db():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    with _db_lock:
        conn = get_db()

        conn.execute("""
            CREATE TABLE IF NOT EXISTS recent_signals (
                symbol TEXT PRIMARY KEY,
                signal_date TEXT NOT NULL,
                close REAL,
                high REAL,
                low REAL,
                signal_name TEXT,
                saved_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS volume_snapshot (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                updated_at TEXT NOT NULL,
                ranking_json TEXT NOT NULL
            )
        """)

        conn.commit()
        conn.close()


init_database()

# ============================================================
# UNIVERSE
# ============================================================
TEST_SYMBOLS = [
    "D&O",
    "SRIDGE",
    "DNEX",
    "ZETRIX",
    "INARI"
]

BURSA_UNIVERSE = [
    "D&O",
    "SRIDGE",
    "DNEX",
    "ZETRIX",
    "INARI",
    "FRONTKN",
    "JCY",
    "GREATEC",
    "NOTION",
    "SNS",
    "VSTECS",
    "AEMULUS",
    "MICROLN",
    "JHM",
    "TOPGLOV",
    "SUPERMX",
    "HARTA",
    "KOSSAN",
    "DXN",
    "ARMADA",
    "VELESTO",
    "CAPITALA",
    "MRCB",
    "BJCORP",
    "TANCO",
    "JAKS",
    "WCT",
    "IJM",
    "MAHSING",
    "TM",
    "AXIATA",
    "MAXIS",
    "DIALOG",
    "GENM",
    "YTLPOWR",
    "VS"
]

# ============================================================
# REQUEST PACING
# ============================================================
_itick_lock = threading.Lock()
_last_itick_request = 0.0


def wait_before_itick_request():
    global _last_itick_request

    with _itick_lock:
        now = time.monotonic()

        wait_time = (
            ITICK_REQUEST_DELAY
            - (now - _last_itick_request)
        )

        if wait_time > 0:
            time.sleep(wait_time)

        _last_itick_request = time.monotonic()


# ============================================================
# TIME
# ============================================================
def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def malaysia_now_string():
    malaysia = timezone(timedelta(hours=8))
    return datetime.now(malaysia).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def format_timestamp(timestamp):
    try:
        dt = datetime.fromtimestamp(
            float(timestamp) / 1000,
            tz=timezone.utc
        )

        malaysia = dt.astimezone(
            timezone(timedelta(hours=8))
        )

        return malaysia.strftime("%Y-%m-%d")

    except Exception:
        return str(timestamp)


# ============================================================
# RECENT SIGNALS
# ============================================================
def cleanup_expired_signals():
    with _db_lock:
        conn = get_db()

        conn.execute(
            "DELETE FROM recent_signals WHERE expires_at <= ?",
            (iso_now(),)
        )

        conn.commit()
        conn.close()


def save_recent_signal(signal):
    cleanup_expired_signals()

    saved = utc_now()
    expires = saved + timedelta(
        days=RECENT_SIGNAL_DAYS
    )

    with _db_lock:
        conn = get_db()

        conn.execute("""
            INSERT INTO recent_signals
            (
                symbol,
                signal_date,
                close,
                high,
                low,
                signal_name,
                saved_at,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(symbol) DO UPDATE SET
                signal_date=excluded.signal_date,
                close=excluded.close,
                high=excluded.high,
                low=excluded.low,
                signal_name=excluded.signal_name,
                saved_at=excluded.saved_at,
                expires_at=excluded.expires_at
        """, (
            signal.get("symbol"),
            signal.get("date"),
            signal.get("close"),
            signal.get("high"),
            signal.get("low"),
            signal.get("signal_name"),
            saved.isoformat(),
            expires.isoformat()
        ))

        conn.commit()
        conn.close()


def get_recent_signals():
    cleanup_expired_signals()

    with _db_lock:
        conn = get_db()

        rows = conn.execute("""
            SELECT
                symbol,
                signal_date,
                close,
                high,
                low,
                signal_name,
                saved_at,
                expires_at
            FROM recent_signals
            ORDER BY saved_at DESC
        """).fetchall()

        conn.close()

    return [dict(row) for row in rows]


def get_recent_symbols():
    return {
        row["symbol"]
        for row in get_recent_signals()
    }


def remove_recent_signal(symbol):
    with _db_lock:
        conn = get_db()

        conn.execute(
            "DELETE FROM recent_signals WHERE symbol = ?",
            (symbol,)
        )

        conn.commit()
        conn.close()


# ============================================================
# VOLUME SNAPSHOT
# ============================================================
def save_volume_snapshot(ranking):
    updated_at = malaysia_now_string()

    with _db_lock:
        conn = get_db()

        conn.execute("""
            INSERT INTO volume_snapshot
            (
                id,
                updated_at,
                ranking_json
            )
            VALUES (1, ?, ?)

            ON CONFLICT(id) DO UPDATE SET
                updated_at=excluded.updated_at,
                ranking_json=excluded.ranking_json
        """, (
            updated_at,
            json.dumps(ranking)
        ))

        conn.commit()
        conn.close()

    return updated_at


def get_volume_snapshot():
    with _db_lock:
        conn = get_db()

        row = conn.execute("""
            SELECT
                updated_at,
                ranking_json
            FROM volume_snapshot
            WHERE id = 1
        """).fetchone()

        conn.close()

    if not row:
        return {
            "ok": False,
            "updated_at": None,
            "ranking": []
        }

    try:
        ranking = json.loads(
            row["ranking_json"]
        )
    except Exception:
        ranking = []

    return {
        "ok": True,
        "updated_at": row["updated_at"],
        "ranking": ranking
    }


# ============================================================
# SUPERTREND ENGINE
# ============================================================
def true_range(high, low, previous_close):
    if previous_close is None:
        return high - low

    return max(
        high - low,
        abs(high - previous_close),
        abs(low - previous_close)
    )


def calculate_supertrend(
    candles,
    atr_length=10,
    factor=1.0
):
    if not candles:
        return []

    parsed = []

    for candle in candles:
        try:
            parsed.append({
                "t": float(candle.get("t", 0)),
                "o": float(candle.get("o", 0)),
                "h": float(candle.get("h", 0)),
                "l": float(candle.get("l", 0)),
                "c": float(candle.get("c", 0)),
                "v": candle.get("v", 0)
            })

        except Exception:
            continue

    if len(parsed) < atr_length:
        return []

    trs = []

    for i, candle in enumerate(parsed):
        previous_close = (
            parsed[i - 1]["c"]
            if i > 0
            else None
        )

        trs.append(
            true_range(
                candle["h"],
                candle["l"],
                previous_close
            )
        )

    atr_values = [None] * len(parsed)

    atr_values[atr_length - 1] = (
        sum(trs[:atr_length])
        / atr_length
    )

    for i in range(
        atr_length,
        len(trs)
    ):
        atr_values[i] = (
            (
                atr_values[i - 1]
                * (atr_length - 1)
            )
            + trs[i]
        ) / atr_length

    result = []

    final_upper = None
    final_lower = None
    previous_direction = None

    for i, candle in enumerate(parsed):

        atr = atr_values[i]

        if atr is None:
            result.append({
                **candle,
                "atr": None,
                "supertrend": None,
                "direction": None,
                "flip": False,
                "high_break": False,
                "signal": 0,
                "signal_name": "NONE"
            })

            continue

        hl2 = (
            candle["h"]
            + candle["l"]
        ) / 2.0

        basic_upper = (
            hl2
            + factor * atr
        )

        basic_lower = (
            hl2
            - factor * atr
        )

        if final_upper is None:
            final_upper = basic_upper
            final_lower = basic_lower

        else:
            previous_close = parsed[i - 1]["c"]

            previous_final_upper = final_upper
            previous_final_lower = final_lower

            if (
                basic_upper < previous_final_upper
                or previous_close > previous_final_upper
            ):
                final_upper = basic_upper
            else:
                final_upper = previous_final_upper

            if (
                basic_lower > previous_final_lower
                or previous_close < previous_final_lower
            ):
                final_lower = basic_lower
            else:
                final_lower = previous_final_lower

        if previous_direction is None:
            direction = (
                1
                if candle["c"] <= final_upper
                else -1
            )

        elif previous_direction == 1:
            direction = (
                -1
                if candle["c"] > final_upper
                else 1
            )

        else:
            direction = (
                1
                if candle["c"] < final_lower
                else -1
            )

        supertrend = (
            final_lower
            if direction < 0
            else final_upper
        )

        flip = (
            previous_direction is not None
            and previous_direction > 0
            and direction < 0
        )

        high_break = (
            i > 0
            and direction < 0
            and candle["c"] > parsed[i - 1]["h"]
        )

        if flip and high_break:
            signal = 3
            signal_name = "FLIP + HIGH BREAK"

        elif flip:
            signal = 1
            signal_name = "FLIP"

        elif high_break:
            signal = 2
            signal_name = "HIGH BREAK"

        else:
            signal = 0
            signal_name = "NONE"

        result.append({
            **candle,
            "atr": atr,
            "supertrend": supertrend,
            "direction": direction,
            "flip": flip,
            "high_break": high_break,
            "signal": signal,
            "signal_name": signal_name
        })

        previous_direction = direction

    return result


def format_result(candle):
    direction = candle.get("direction")

    if direction == -1:
        trend = "BULL"

    elif direction == 1:
        trend = "BEAR"

    else:
        trend = "NA"

    return {
        "date": format_timestamp(
            candle.get("t", 0)
        ),
        "close": candle.get("c"),
        "high": candle.get("h"),
        "low": candle.get("l"),
        "atr10": candle.get("atr"),
        "supertrend": candle.get("supertrend"),
        "direction": direction,
        "trend": trend,
        "flip": candle.get("flip", False),
        "high_break": candle.get(
            "high_break",
            False
        ),
        "signal": candle.get(
            "signal",
            0
        ),
        "signal_name": candle.get(
            "signal_name",
            "NONE"
        )
    }


# ============================================================
# SINGLE STOCK KLINE
# ============================================================
def fetch_kline(symbol, limit=50):

    if not ITICK_API_KEY:
        return {
            "ok": False,
            "symbol": symbol,
            "error": "ITICK_API_KEY belum diset."
        }

    headers = {
        "accept": "application/json",
        "token": ITICK_API_KEY
    }

    params = {
        "region": "MY",
        "exchange": "MYX",
        "code": symbol,
        "kType": 8,
        "limit": limit
    }

    wait_before_itick_request()

    try:
        response = requests.get(
            ITICK_KLINE_URL,
            params=params,
            headers=headers,
            timeout=30
        )

    except requests.RequestException as error:
        return {
            "ok": False,
            "symbol": symbol,
            "error": (
                f"K-line request error: {error}"
            )
        }

    if response.status_code == 429:
        return {
            "ok": False,
            "symbol": symbol,
            "error": "HTTP 429 - iTick rate limit."
        }

    if response.status_code != 200:
        return {
            "ok": False,
            "symbol": symbol,
            "error": (
                f"HTTP {response.status_code}"
            )
        }

    try:
        data = response.json()

    except ValueError:
        return {
            "ok": False,
            "symbol": symbol,
            "error": "K-line response bukan JSON."
        }

    if data.get("code") != 0:
        return {
            "ok": False,
            "symbol": symbol,
            "error": str(data)
        }

    raw = data.get("data", [])

    if not isinstance(raw, list):
        raw = []

    if not raw:
        return {
            "ok": False,
            "symbol": symbol,
            "error": "Tiada daily candle."
        }

    try:
        raw = sorted(
            raw,
            key=lambda x: float(
                x.get("t", 0)
            )
        )

    except Exception:
        pass

    return {
        "ok": True,
        "symbol": symbol,
        "candles": raw
    }


def calculate_symbol(symbol):

    fetched = fetch_kline(
        symbol,
        50
    )

    if not fetched.get("ok"):
        return fetched

    calculated = calculate_supertrend(
        fetched["candles"],
        ATR_LENGTH,
        SUPERTREND_FACTOR
    )

    if not calculated:
        return {
            "ok": False,
            "symbol": symbol,
            "error": (
                "Supertrend calculation gagal."
            )
        }

    latest = calculated[-1]

    historical_signals = [
        format_result(c)
        for c in calculated
        if c.get("signal", 0) != 0
    ]

    return {
        "ok": True,
        "symbol": symbol,
        "latest": format_result(latest),
        "historical_signals": historical_signals
    }


# ============================================================
# BATCH DAILY VOLUME
# ============================================================
def fetch_daily_volume_batch(symbols):

    if not ITICK_API_KEY:
        return {
            "ok": False,
            "error": "ITICK_API_KEY belum diset.",
            "results": []
        }

    headers = {
        "accept": "application/json",
        "token": ITICK_API_KEY
    }

    params = {
        "region": "MY",
        "exchange": "MYX",
        "codes": ",".join(symbols)
    }

    wait_before_itick_request()

    try:
        response = requests.get(
            ITICK_BATCH_QUOTE_URL,
            headers=headers,
            params=params,
            timeout=30
        )

    except requests.RequestException as error:
        return {
            "ok": False,
            "error": str(error),
            "results": []
        }

    print(
        "QUOTE HTTP STATUS:",
        response.status_code
    )

    print(
        "QUOTE RAW RESPONSE:",
        response.text[:1000]
    )

    if response.status_code == 429:
        return {
            "ok": False,
            "error": (
                "HTTP 429 - iTick rate limit."
            ),
            "results": []
        }

    if response.status_code != 200:
        return {
            "ok": False,
            "error": (
                f"HTTP {response.status_code}"
            ),
            "results": []
        }

    try:
        data = response.json()

    except ValueError:
        return {
            "ok": False,
            "error": (
                "Batch quote response bukan JSON."
            ),
            "results": []
        }

    if data.get("code") != 0:
        return {
            "ok": False,
            "error": str(data),
            "results": []
        }

    payload = data.get("data", {})

    if not isinstance(payload, dict):
    return {
        "ok": False,
        "error": "Format data K-Line tidak dijangka.",
        "results": []
    }

    results = []

    for symbol in symbols:

    candles = payload.get(symbol, [])

    if not candles:
        results.append({
            "symbol": symbol,
            "ok": False,
            "error": "Tiada daily candle dalam batch."
        })
        continue

    try:
        latest = max(
            candles,
            key=lambda x: float(x.get("t", 0) or 0)
        )

        volume = float(latest.get("v", 0) or 0)

    except Exception:
        volume = 0.0

    results.append({
        "symbol": symbol,
        "ok": True,
        "volume": volume
    })

    return {
    "ok": True,
    "results": results
    }


# ============================================================
# BUILD NEW VOLUME SNAPSHOT
# ============================================================
def build_volume_ranking():

    all_results = []
    errors = []

    volume_universe = BURSA_UNIVERSE[:20]
    total = len(volume_universe)
    

    for start in range(
        0,
        total,
        VOLUME_BATCH_SIZE
    ):

        batch = volume_universe[
            start:start + VOLUME_BATCH_SIZE 
        ]
        
        print(
            "VOLUME BATCH:",
            start,
            "->",
            start + len(batch)
        )

        result = fetch_daily_volume_batch(
            batch
        )

        if result.get("ok"):

            for item in result.get(
                "results",
                []
            ):

                if item.get("ok"):
                    all_results.append(item)

                else:
                    errors.append(item)

        else:
            errors.append({
                "batch": batch,
                "error": result.get(
                    "error",
                    "Unknown volume error"
                )
            })

    ranking = sorted(
        all_results,
        key=lambda x: float(
            x.get("volume", 0)
        ),
        reverse=True
    )

    for index, item in enumerate(
        ranking,
        start=1
    ):
        item["rank"] = index

    updated_at = None

    if ranking:
        updated_at = save_volume_snapshot(
            ranking
        )

    return {
        "ok": bool(ranking),
        "count": len(ranking),
        "ranking": ranking,
        "updated_at": updated_at,
        "error_count": len(errors),
        "errors": errors
    }


# ============================================================
# SCANNER ENGINE
# ============================================================
def scan_symbols(
    symbols,
    start=0,
    batch_size=5
):

    symbols = [
        s
        for s in symbols
        if s in BURSA_UNIVERSE
    ]

    end = min(
        start + batch_size,
        len(symbols)
    )

    batch = symbols[start:end]

    recent_symbols = get_recent_symbols()

    scanner = []
    errors = []
    skipped = []

    for symbol in batch:

        if symbol in recent_symbols:

            skipped.append({
                "symbol": symbol,
                "reason": "RECENT SIGNAL"
            })

            continue

        result = calculate_symbol(
            symbol
        )

        if not result.get("ok"):

            errors.append({
                "symbol": symbol,
                "error": result.get(
                    "error",
                    "Unknown error"
                )
            })

            continue

        latest = result.get(
            "latest",
            {}
        )

        if latest.get(
            "signal",
            0
        ) != 0:

            signal = {
                "symbol": symbol,
                **latest
            }

            scanner.append(signal)

            save_recent_signal(
                signal
            )

    return {
        "start": start,
        "end": end,
        "batch_size": len(batch),
        "requested_count": len(symbols),
        "scanned_count": end,
        "remaining_count": (
            len(symbols) - end
        ),
        "done": (
            end >= len(symbols)
        ),
        "scanner": scanner,
        "signals_only": scanner.copy(),
        "skipped_count": len(skipped),
        "skipped": skipped,
        "error_count": len(errors),
        "errors": errors
    }


# ============================================================
# API
# ============================================================
@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": (
            "bursa-supertrend-scanner"
        )
    }


@app.get("/api/recent-signals")
def recent_signals():

    signals = get_recent_signals()

    return {
        "ok": True,
        "days": RECENT_SIGNAL_DAYS,
        "count": len(signals),
        "signals": signals
    }


# ------------------------------------------------------------
# READ SAVED VOLUME
# ------------------------------------------------------------
@app.get("/api/volume-ranking")
def volume_ranking():

    snapshot = get_volume_snapshot()

    return {
        "ok": snapshot["ok"],
        "count": len(
            snapshot["ranking"]
        ),
        "updated_at": snapshot[
            "updated_at"
        ],
        "ranking": snapshot[
            "ranking"
        ],
        "errors": []
    }


# ------------------------------------------------------------
# MANUAL UPDATE DAILY VOLUME
# ------------------------------------------------------------
@app.get("/api/update-volume")
def update_volume():

    result = build_volume_ranking()

    return {
        "ok": result["ok"],
        "count": result["count"],
        "updated_at": result[
            "updated_at"
        ],
        "ranking": result[
            "ranking"
        ],
        "error_count": result[
            "error_count"
        ],
        "errors": result[
            "errors"
        ]
    }


# ------------------------------------------------------------
# NORMAL UNIVERSE SCAN
# ------------------------------------------------------------
@app.get("/api/test/universe")
def test_universe(
    start: int = 0,
    batch_size: int = DEFAULT_BATCH_SIZE
):

    if start < 0:
        start = 0

    batch_size = min(
        max(batch_size, 1),
        10
    )

    return scan_symbols(
        BURSA_UNIVERSE,
        start,
        batch_size
    )


# ------------------------------------------------------------
# SCAN SAVED VOLUME
# ------------------------------------------------------------
@app.get("/api/test/volume-scan")
def volume_scan(
    start: int = 0,
    batch_size: int = DEFAULT_BATCH_SIZE,
    symbols: str = ""
):

    if symbols.strip():

        ordered = [
            s.strip().upper()
            for s in symbols.split(",")
            if s.strip()
        ]

        ordered = [
            s
            for s in ordered
            if s in BURSA_UNIVERSE
        ]

    else:

        snapshot = get_volume_snapshot()

        ordered = [
            item["symbol"]
            for item in snapshot[
                "ranking"
            ]
            if item.get("symbol")
        ]

    if not ordered:
        return {
            "ok": False,
            "error": (
                "Belum ada Daily Volume "
                "snapshot. Tekan UPDATE DAILY VOLUME dahulu."
            ),
            "scanner": []
        }

    batch_size = min(
        max(batch_size, 1),
        10
    )

    return scan_symbols(
        ordered,
        start,
        batch_size
    )


# ------------------------------------------------------------
# RESCAN SAVED
# ------------------------------------------------------------
@app.get("/api/rescan-saved")
def rescan_saved():

    saved = get_recent_signals()

    results = []
    errors = []

    for item in saved:

        symbol = item["symbol"]

        result = calculate_symbol(
            symbol
        )

        if not result.get("ok"):

            errors.append({
                "symbol": symbol,
                "error": result.get(
                    "error",
                    "Unknown error"
                )
            })

            continue

        latest = result.get(
            "latest",
            {}
        )

        if latest.get(
            "signal",
            0
        ) != 0:

            signal = {
                "symbol": symbol,
                **latest
            }

            save_recent_signal(
                signal
            )

            results.append({
                "symbol": symbol,
                "status": (
                    "SIGNAL STILL ACTIVE"
                ),
                "signal": signal
            })

        else:

            remove_recent_signal(
                symbol
            )

            results.append({
                "symbol": symbol,
                "status": (
                    "SIGNAL CLEARED"
                )
            })

    return {
        "ok": True,
        "rescanned_count": len(saved),
        "results": results,
        "error_count": len(errors),
        "errors": errors,
        "recent_signals": (
            get_recent_signals()
        )
    }


# ------------------------------------------------------------
# TEST
# ------------------------------------------------------------
@app.get("/api/test/5")
def test_five():

    return {
        "requested_count": len(
            TEST_SYMBOLS
        ),
        "results": [
            calculate_symbol(s)
            for s in TEST_SYMBOLS
        ]
    }


@app.get("/api/test/history")
def test_history():

    results = []

    for symbol in TEST_SYMBOLS:

        result = calculate_symbol(
            symbol
        )

        if result.get("ok"):

            results.append({
                "symbol": symbol,
                "historical_signals":
                    result.get(
                        "historical_signals",
                        []
                    )
            })

        else:
            results.append(result)

    return {
        "results": results
    }


# ============================================================
# HOME PAGE
# ============================================================
@app.get(
    "/",
    response_class=HTMLResponse
)
def home():

    return r'''
<!DOCTYPE html>
<html>

<head>

<meta
name="viewport"
content="width=device-width, initial-scale=1.0"
>

<title>Bursa Supertrend Scanner</title>

<style>

body{
    margin:0;
    padding:20px;
    background:#101010;
    color:#eeeeee;
    font-family:Arial,sans-serif
}

.container{
    max-width:900px;
    margin:auto
}

h1{
    margin-bottom:5px;
    font-size:24px
}

.subtitle{
    color:#999999;
    margin-bottom:20px
}

button{
    width:100%;
    padding:14px;
    margin-top:10px;
    border:none;
    border-radius:8px;
    background:#1f8f4d;
    color:white;
    font-size:16px;
    font-weight:bold
}

button:disabled{
    opacity:.5
}

.secondary{
    background:#333333
}

.danger{
    background:#7b3030
}

.volume{
    background:#1769aa
}

input{
    width:100%;
    box-sizing:border-box;
    padding:12px;
    margin-top:8px;
    margin-bottom:8px;
    border-radius:8px;
    border:1px solid #444;
    background:#181818;
    color:white;
    font-size:16px
}

label{
    display:block;
    margin-top:15px;
    color:#cccccc
}

pre{
    margin-top:20px;
    padding:15px;
    background:#181818;
    border-radius:8px;
    overflow-x:auto;
    white-space:pre-wrap;
    word-break:break-word;
    font-size:13px
}

.card{
    margin-top:20px;
    padding:15px;
    background:#181818;
    border-radius:10px
}

.card-title{
    font-size:18px;
    font-weight:bold;
    margin-bottom:10px
}

.info{
    margin-top:15px;
    padding:12px;
    background:#181818;
    border-radius:8px;
    color:#bbbbbb;
    font-size:13px
}

.signal{
    padding:12px;
    margin-top:8px;
    border-radius:8px;
    background:#222222;
    border-left:4px solid #1f8f4d
}

.signal-title{
    font-size:16px;
    font-weight:bold
}

.signal-detail{
    color:#bbbbbb;
    margin-top:5px;
    font-size:13px
}

.rank{
    padding:9px;
    margin-top:5px;
    background:#222222;
    border-radius:7px;
    font-size:13px
}

.empty{
    color:#888888;
    font-size:14px
}

</style>

</head>

<body>

<div class="container">

<h1>
BURSA SUPERTREND SCANNER
</h1>

<div class="subtitle">
Bursa Universe • Daily • ATR 10 / Factor 1.0
</div>


<!-- ======================================================
     RECENT SIGNALS
====================================================== -->

<div class="card">

<div class="card-title">
RECENT SIGNALS
</div>

<div id="recentSignals">
Loading...
</div>

<button
class="secondary"
onclick="loadRecentSignals()"
>
REFRESH RECENT SIGNALS
</button>

<button
class="danger"
onclick="rescanSaved()"
>
RESCAN SAVED
</button>

</div>


<!-- ======================================================
     DAILY VOLUME SNAPSHOT
====================================================== -->

<div class="card">

<div class="card-title">
DAILY VOLUME SNAPSHOT
</div>

<button
class="volume"
id="updateVolumeButton"
onclick="updateDailyVolume()"
>
UPDATE DAILY VOLUME
</button>

<div
id="volumeStatus"
class="info"
>
Belum ada Daily Volume snapshot.
</div>

<div id="volumeList">
</div>

</div>


<!-- ======================================================
     SAVED VOLUME SCANNER
====================================================== -->

<div class="card">

<div class="card-title">
SCAN SAVED VOLUME
</div>

<button
class="volume"
id="savedVolumeButton"
onclick="startSavedVolumeScan()"
>
SCAN SAVED VOLUME → SCAN 5
</button>

</div>


<!-- ======================================================
     NORMAL SCANNER
====================================================== -->

<div class="card">

<div class="card-title">
BURSA UNIVERSE SCANNER
</div>

<label>
MAX SIGNALS
</label>

<input
id="maxSignals"
type="number"
min="1"
max="20"
value="5"
>

<button
id="scanButton"
onclick="scanFirstBatch()"
>
SCAN 5
</button>

<button
id="nextButton"
onclick="scanNextBatch()"
disabled
>
SCAN NEXT 5
</button>

<button
class="secondary"
onclick="testHistory()"
>
TEST HISTORICAL SIGNAL
</button>

</div>


<div class="info">

<b>UPDATE DAILY VOLUME</b>
akan membuat satu snapshot ranking untuk
36 kaunter.

<br><br>

Selepas snapshot disimpan,
<b>SCAN SAVED VOLUME</b>
menggunakan ranking tersebut tanpa
meminta volume sekali lagi daripada iTick.

<br><br>

<b>RECENT SIGNALS</b>
akan disimpan selama
<b>5 hari</b>.

</div>


<pre id="out">
Ready.
</pre>

</div>


<script>

let nextStart = 0;
let totalSignals = 0;
let allSignals = [];
let allErrors = [];
let scanDone = false;

let volumeOrder = [];


function price(v){

    if(
        v === null ||
        v === undefined
    ){
        return "-"
    }

    return Number(v).toFixed(3)
}


function fmtExpiry(v){

    try{

        return new Date(v).toLocaleString(
            "ms-MY",
            {
                dateStyle:"medium",
                timeStyle:"short"
            }
        )

    }catch(e){

        return v || "-"

    }

}


/* ======================================================
   RECENT SIGNALS
====================================================== */

async function loadRecentSignals(){

    const box =
        document.getElementById(
            "recentSignals"
        );

    box.textContent =
        "Loading...";

    try{

        const r =
            await fetch(
                "/api/recent-signals"
            );

        if(!r.ok){
            throw new Error(
                "HTTP " + r.status
            );
        }

        const d =
            await r.json();

        if(
            !d.signals.length
        ){

            box.innerHTML =
                '<div class="empty">' +
                'Tiada Recent Signal.' +
                '</div>';

            return;
        }

        box.innerHTML =
            d.signals.map(
                s =>
                '<div class="signal">' +

                '<div class="signal-title">' +
                s.symbol +
                ' | RM ' +
                price(s.close) +
                '</div>' +

                '<div class="signal-detail">' +
                s.date +
                ' | ' +
                s.signal_name +
                '</div>' +

                '<div class="signal-detail">' +
                'Expired: ' +
                fmtExpiry(
                    s.expires_at
                ) +
                '</div>' +

                '</div>'
            ).join("");

    }catch(e){

        box.textContent =
            "ERROR\n\n" + e;

    }

}


/* ======================================================
   VOLUME SNAPSHOT DISPLAY
====================================================== */

async function loadSavedVolume(){

    const status =
        document.getElementById(
            "volumeStatus"
        );

    const list =
        document.getElementById(
            "volumeList"
        );

    try{

        const r =
            await fetch(
                "/api/volume-ranking"
            );

        if(!r.ok){
            throw new Error(
                "HTTP " + r.status
            );
        }

        const d =
            await r.json();

        if(
            !d.ranking ||
            !d.ranking.length
        ){

            status.textContent =
                "Belum ada Daily Volume snapshot.";

            list.innerHTML = "";

            return;
        }

        volumeOrder =
            d.ranking.map(
                x => x.symbol
            );

        status.textContent =
            "Snapshot: " +
            d.updated_at +
            " | " +
            d.ranking.length +
            " / 36 kaunter.";

        list.innerHTML =
            d.ranking
            .slice(0,10)
            .map(
                x =>
                '<div class="rank">' +

                '#' +
                x.rank +
                ' ' +
                x.symbol +
                ' | Volume ' +
                Number(
                    x.volume
                ).toLocaleString() +

                '</div>'
            )
            .join("");

    }catch(e){

        status.textContent =
            "Volume snapshot error: " + e;

    }

}


/* ======================================================
   UPDATE DAILY VOLUME
====================================================== */

async function updateDailyVolume(){

    const button =
        document.getElementById(
            "updateVolumeButton"
        );

    const out =
        document.getElementById(
            "out"
        );

    button.disabled = true;

    out.textContent =
        "UPDATE DAILY VOLUME\n\n" +
        "Meminta volume 36 kaunter " +
        "dalam 4 batch...\n\n" +
        "Sila tunggu.\n";

    try{

        const r =
            await fetch(
                "/api/update-volume"
            );

        if(!r.ok){
            throw new Error(
                "HTTP " + r.status
            );
        }

        const d =
            await r.json();

        if(!d.ok){

            out.textContent +=
                "\nUPDATE GAGAL\n\n" +
                JSON.stringify(
                    d.errors,
                    null,
                    2
                );

            return;
        }

        volumeOrder =
            d.ranking.map(
                x => x.symbol
            );

        out.textContent +=
            "\nUPDATE BERJAYA\n\n" +

            "Snapshot time: " +
            d.updated_at +
            "\n" +

            "Ranking: " +
            d.count +
            " / 36\n\n";

        d.ranking
            .slice(0,10)
            .forEach(
                x => {

                    out.textContent +=
                        "#" +
                        x.rank +
                        " " +
                        x.symbol +
                        " | Volume " +
                        Number(
                            x.volume
                        ).toLocaleString() +
                        "\n";

                }
            );

        if(
            d.errors &&
            d.errors.length
        ){

            out.textContent +=
                "\nVolume errors: " +
                d.errors.length +
                "\n";

        }

        await loadSavedVolume();

    }catch(e){

        out.textContent +=
            "\nUPDATE ERROR\n\n" +
            e;

    }finally{

        button.disabled = false;

    }

}


/* ======================================================
   SCAN CONTROL
====================================================== */

function resetScan(){

    nextStart = 0;
    totalSignals = 0;
    allSignals = [];
    allErrors = [];
    scanDone = false;

}


/* ======================================================
   SAVED VOLUME SCAN
====================================================== */

async function startSavedVolumeScan(){

    resetScan();

    const out =
        document.getElementById(
            "out"
        );

    const button =
        document.getElementById(
            "savedVolumeButton"
        );

    button.disabled = true;

    try{

        const r =
            await fetch(
                "/api/volume-ranking"
            );

        if(!r.ok){
            throw new Error(
                "HTTP " + r.status
            );
        }

        const d =
            await r.json();

        if(
            !d.ranking ||
            !d.ranking.length
        ){

            out.textContent =
                "TIADA SAVED VOLUME.\n\n" +
                "Tekan UPDATE DAILY VOLUME " +
                "dahulu.";

            return;
        }

        volumeOrder =
            d.ranking.map(
                x => x.symbol
            );

        out.textContent =
            "SCAN SAVED VOLUME\n\n" +

            "Snapshot: " +
            d.updated_at +
            "\n\n" +

            "Ranking tersedia: " +
            volumeOrder.length +
            " / 36\n\n";

        await scanOneBatch(
            true
        );

    }catch(e){

        out.textContent =
            "SAVED VOLUME SCAN ERROR\n\n" +
            e;

    }finally{

        button.disabled = false;

    }

}


/* ======================================================
   NORMAL SCAN
====================================================== */

async function scanFirstBatch(){

    resetScan();

    document.getElementById(
        "out"
    ).textContent =
        "MULA SCAN 5...\n\n";

    await scanOneBatch(
        false
    );

}


async function scanNextBatch(){

    const max =
        parseInt(
            document.getElementById(
                "maxSignals"
            ).value
        ) || 5;

    if(
        scanDone ||
        totalSignals >= max
    ){
        return;
    }

    await scanOneBatch(
        false
    );

}


/* ======================================================
   SCAN ONE BATCH
====================================================== */

async function scanOneBatch(
    savedVolume
){

    const out =
        document.getElementById(
            "out"
        );

    const scanButton =
        document.getElementById(
            "scanButton"
        );

    const nextButton =
        document.getElementById(
            "nextButton"
        );

    const savedButton =
        document.getElementById(
            "savedVolumeButton"
        );

    const max =
        parseInt(
            document.getElementById(
                "maxSignals"
            ).value
        ) || 5;


    scanButton.disabled = true;
    nextButton.disabled = true;
    savedButton.disabled = true;


    let endpoint =
        savedVolume
        ? "/api/test/volume-scan"
        : "/api/test/universe";


    let query;


    if(savedVolume){

        query =
            endpoint +
            "?start=" +
            nextStart +
            "&batch_size=5&symbols=" +
            encodeURIComponent(
                volumeOrder.join(",")
            );

    }else{

        query =
            endpoint +
            "?start=" +
            nextStart +
            "&batch_size=5";

    }


    out.textContent +=
        "--------------------------------\n" +

        (
            savedVolume
            ? "SAVED VOLUME SCAN BATCH\n"
            : "UNIVERSE SCAN BATCH\n"
        ) +

        "Kaunter: " +
        nextStart +
        " → " +
        (nextStart + 5) +
        "\n\n";


    try{

        const r =
            await fetch(
                query
            );

        if(!r.ok){
            throw new Error(
                "HTTP " + r.status
            );
        }

        const d =
            await r.json();

        if(d.ok === false){

            throw new Error(
                d.error ||
                "Scanner error"
            );

        }


        if(
            d.scanner &&
            d.scanner.length
        ){

            for(
                const s
                of d.scanner
            ){

                if(
                    totalSignals >= max
                ){
                    break;
                }

                totalSignals++;

                allSignals.push(
                    s
                );

                out.textContent +=
                    "SIGNAL #" +
                    totalSignals +
                    "\n" +

                    s.symbol +
                    " | " +
                    s.date +
                    " | RM " +
                    price(
                        s.close
                    ) +
                    " | " +
                    s.signal_name +
                    "\n\n";

            }

        }else{

            out.textContent +=
                "Tiada signal baru " +
                "dalam batch ini.\n\n";

        }


        if(
            d.skipped &&
            d.skipped.length
        ){

            out.textContent +=
                "SKIP RECENT SIGNAL: " +
                d.skipped.length +
                "\n";

            d.skipped.forEach(
                x => {

                    out.textContent +=
                        x.symbol +
                        " -> RECENT SIGNAL\n";

                }
            );

            out.textContent += "\n";

        }


        if(
            d.errors &&
            d.errors.length
        ){

            d.errors.forEach(
                x =>
                    allErrors.push(x)
            );

            out.textContent +=
                "Error batch: " +
                d.errors.length +
                "\n\n";

        }


        out.textContent +=
            "--------------------------------\n" +

            "Progress: " +
            d.scanned_count +
            " / " +
            d.requested_count +
            "\n" +

            "Signal: " +
            totalSignals +
            " / " +
            max +
            "\n";


        nextStart =
            d.end;


        if(
            totalSignals >= max
        ){

            scanDone = true;

            out.textContent +=
                "\nMAX SIGNALS " +
                max +
                " DICAPAI.\n" +
                "SCAN DIHENTIKAN.\n";

        }

        else if(
            d.done
        ){

            scanDone = true;

            out.textContent +=
                "\nSEMUA SUSUNAN SELESAI.\n";

        }

        else{

            out.textContent +=
                "\nBATCH INI SELESAI.\n" +
                "Teruskan scan seterusnya...\n";

            await scanOneBatch(
                savedVolume
            );

            return;

        }


        out.textContent +=
            "\n================================\n" +
            "SIGNAL DIJUMPAI SETAKAT INI\n" +
            "================================\n";


        if(
            allSignals.length
        ){

            allSignals.forEach(
                (s,i) => {

                    out.textContent +=
                        (i + 1) +
                        ". " +
                        s.symbol +
                        " | " +
                        s.date +
                        " | RM " +
                        price(
                            s.close
                        ) +
                        " | " +
                        s.signal_name +
                        "\n";

                }
            );

        }else{

            out.textContent +=
                "Tiada signal ditemui.\n";

        }


        if(
            allErrors.length
        ){

            out.textContent +=
                "\nERROR / 429:\n";

            allErrors.forEach(
                x => {

                    out.textContent +=
                        x.symbol +
                        " -> " +
                        x.error +
                        "\n";

                }
            );

        }


        await loadRecentSignals();


    }catch(e){

        out.textContent +=
            "\nSCAN ERROR\n\n" +
            e;

    }


    scanButton.disabled = false;
    savedButton.disabled = false;

    nextButton.disabled =
        scanDone ||
        totalSignals >= max;

}


/* ======================================================
   RESCAN SAVED
====================================================== */

async function rescanSaved(){

    const out =
        document.getElementById(
            "out"
        );

    out.textContent =
        "RESCAN SAVED...\n\n" +
        "Sila tunggu...\n";


    try{

        const r =
            await fetch(
                "/api/rescan-saved"
            );

        if(!r.ok){
            throw new Error(
                "HTTP " + r.status
            );
        }

        const d =
            await r.json();

        out.textContent +=
            "Jumlah saved sebelum rescan: " +
            d.rescanned_count +
            "\n\n";


        d.results.forEach(
            x => {

                out.textContent +=
                    x.symbol +
                    " -> " +
                    x.status +
                    "\n";

                if(x.signal){

                    out.textContent +=
                        "   " +
                        x.signal.signal_name +
                        " | RM " +
                        price(
                            x.signal.close
                        ) +
                        " | " +
                        x.signal.date +
                        "\n";

                }

            }
        );


        await loadRecentSignals();


    }catch(e){

        out.textContent =
            "RESCAN SAVED ERROR\n\n" +
            e;

    }

}


/* ======================================================
   HISTORY
====================================================== */

async function testHistory(){

    const out =
        document.getElementById(
            "out"
        );

    out.textContent =
        "Testing historical signals...";


    try{

        const r =
            await fetch(
                "/api/test/history"
            );

        out.textContent =
            JSON.stringify(
                await r.json(),
                null,
                2
            );

    }catch(e){

        out.textContent =
            "HISTORY ERROR\n\n" +
            e;

    }

}


/* ======================================================
   PAGE LOAD
====================================================== */

loadRecentSignals();
loadSavedVolume();

</script>

</body>

</html>
'''
