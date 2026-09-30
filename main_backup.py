import os
import time
import threading
import sqlite3
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

ITICK_REQUEST_DELAY = float(
    os.getenv("ITICK_REQUEST_DELAY", "1.0")
)

ITICK_MAX_RETRIES = 2

# ============================================================
# SUPERTREND
# ============================================================
ATR_LENGTH = 10
SUPERTREND_FACTOR = 1.0

# ============================================================
# BATCH SETTINGS
# ============================================================
DEFAULT_BATCH_SIZE = 5
DEFAULT_MAX_SIGNALS = 5

# ============================================================
# RECENT SIGNAL SETTINGS
# ============================================================
RECENT_SIGNAL_DAYS = 5

# ============================================================
# DATABASE
# ============================================================
# Local development:
#     ./data/recent_signals.db
#
# Render Persistent Disk:
#     set DATA_DIR=/var/data
#
# IMPORTANT:
# Render filesystem is ephemeral unless persistent disk
# or managed datastore is used.
# ============================================================
DATA_DIR = os.getenv("DATA_DIR", "./data")

os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(
    DATA_DIR,
    "recent_signals.db"
)

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

        conn.execute(
            """
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
            """
        )

        conn.commit()
        conn.close()


init_database()

# ============================================================
# ITICK REQUEST PACING
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
# TEST SYMBOLS
# ============================================================
TEST_SYMBOLS = [
    "D&O",
    "SRIDGE",
    "DNEX",
    "ZETRIX",
    "INARI"
]

# ============================================================
# BURSA UNIVERSE - 36
# ============================================================
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
# DATE / TIME
# ============================================================
def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def format_timestamp(timestamp):

    try:

        ts = float(timestamp)

        dt = datetime.fromtimestamp(
            ts / 1000,
            tz=timezone.utc
        )

        malaysia = dt.astimezone(
            timezone(timedelta(hours=8))
        )

        return malaysia.strftime("%Y-%m-%d")

    except Exception:

        return str(timestamp)


# ============================================================
# RECENT SIGNAL DATABASE
# ============================================================
def cleanup_expired_signals():

    now = iso_now()

    with _db_lock:

        conn = get_db()

        conn.execute(
            """
            DELETE FROM recent_signals
            WHERE expires_at <= ?
            """,
            (now,)
        )

        conn.commit()
        conn.close()


def save_recent_signal(signal):

    cleanup_expired_signals()

    saved_time = utc_now()

    expires_time = (
        saved_time
        + timedelta(days=RECENT_SIGNAL_DAYS)
    )

    with _db_lock:

        conn = get_db()

        conn.execute(
            """
            INSERT INTO recent_signals (
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

            ON CONFLICT(symbol)
            DO UPDATE SET
                signal_date = excluded.signal_date,
                close = excluded.close,
                high = excluded.high,
                low = excluded.low,
                signal_name = excluded.signal_name,
                saved_at = excluded.saved_at,
                expires_at = excluded.expires_at
            """,
            (
                signal.get("symbol"),
                signal.get("date"),
                signal.get("close"),
                signal.get("high"),
                signal.get("low"),
                signal.get("signal_name"),
                saved_time.isoformat(),
                expires_time.isoformat()
            )
        )

        conn.commit()
        conn.close()


def get_recent_signals():

    cleanup_expired_signals()

    with _db_lock:

        conn = get_db()

        rows = conn.execute(
            """
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
            """
        ).fetchall()

        conn.close()

    results = []

    for row in rows:

        results.append(
            {
                "symbol": row["symbol"],
                "date": row["signal_date"],
                "close": row["close"],
                "high": row["high"],
                "low": row["low"],
                "signal_name": row["signal_name"],
                "saved_at": row["saved_at"],
                "expires_at": row["expires_at"]
            }
        )

    return results


def get_recent_symbols():

    cleanup_expired_signals()

    with _db_lock:

        conn = get_db()

        rows = conn.execute(
            """
            SELECT symbol
            FROM recent_signals
            """
        ).fetchall()

        conn.close()

    return {
        row["symbol"]
        for row in rows
    }


def remove_recent_signal(symbol):

    with _db_lock:

        conn = get_db()

        conn.execute(
            """
            DELETE FROM recent_signals
            WHERE symbol = ?
            """,
            (symbol,)
        )

        conn.commit()
        conn.close()


# ============================================================
# TRUE RANGE
# ============================================================
def true_range(
    high,
    low,
    previous_close
):

    if previous_close is None:

        return high - low

    return max(
        high - low,
        abs(high - previous_close),
        abs(low - previous_close)
    )


# ============================================================
# SUPERTREND
# ============================================================
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

            parsed.append(
                {
                    "t": float(candle.get("t", 0)),
                    "o": float(candle.get("o", 0)),
                    "h": float(candle.get("h", 0)),
                    "l": float(candle.get("l", 0)),
                    "c": float(candle.get("c", 0)),
                    "v": candle.get("v", 0)
                }
            )

        except Exception:

            continue

    if len(parsed) < atr_length:

        return []

    # --------------------------------------------------------
    # TRUE RANGE
    # --------------------------------------------------------
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

    # --------------------------------------------------------
    # ATR - WILDER / RMA
    # --------------------------------------------------------
    atr_values = [None] * len(parsed)

    first_atr = (
        sum(trs[:atr_length])
        / atr_length
    )

    atr_values[atr_length - 1] = first_atr

    for i in range(
        atr_length,
        len(trs)
    ):

        previous_atr = atr_values[i - 1]

        atr_values[i] = (
            (
                previous_atr
                * (atr_length - 1)
            )
            + trs[i]
        ) / atr_length

    # --------------------------------------------------------
    # SUPERTREND
    # --------------------------------------------------------
    result = []

    final_upper = None
    final_lower = None
    previous_direction = None

    for i, candle in enumerate(parsed):

        atr = atr_values[i]

        if atr is None:

            result.append(
                {
                    **candle,
                    "atr": None,
                    "supertrend": None,
                    "direction": None,
                    "flip": False,
                    "high_break": False,
                    "signal": 0,
                    "signal_name": "NONE"
                }
            )

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

            previous_close = (
                parsed[i - 1]["c"]
            )

            previous_final_upper = (
                final_upper
            )

            previous_final_lower = (
                final_lower
            )

            if (
                basic_upper
                < previous_final_upper
                or previous_close
                > previous_final_upper
            ):

                final_upper = basic_upper

            else:

                final_upper = (
                    previous_final_upper
                )

            if (
                basic_lower
                > previous_final_lower
                or previous_close
                < previous_final_lower
            ):

                final_lower = basic_lower

            else:

                final_lower = (
                    previous_final_lower
                )

        if previous_direction is None:

            direction = (
                1
                if candle["c"] <= final_upper
                else -1
            )

        else:

            if previous_direction == 1:

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
            and candle["c"]
            > parsed[i - 1]["h"]
        )

        if flip and high_break:

            signal = 3
            signal_name = (
                "FLIP + HIGH BREAK"
            )

        elif flip:

            signal = 1
            signal_name = "FLIP"

        elif high_break:

            signal = 2
            signal_name = "HIGH BREAK"

        else:

            signal = 0
            signal_name = "NONE"

        result.append(
            {
                **candle,
                "atr": atr,
                "supertrend": supertrend,
                "direction": direction,
                "flip": flip,
                "high_break": high_break,
                "signal": signal,
                "signal_name": signal_name
            }
        )

        previous_direction = direction

    return result


# ============================================================
# FORMAT RESULT
# ============================================================
def format_result(candle):

    direction = candle.get(
        "direction"
    )

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
        "supertrend": candle.get(
            "supertrend"
        ),
        "direction": direction,
        "trend": trend,
        "flip": candle.get(
            "flip",
            False
        ),
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
# FETCH KLINE
# ============================================================
def fetch_kline(
    symbol,
    limit=50
):

    if not ITICK_API_KEY:

        return {
            "ok": False,
            "symbol": symbol,
            "error": (
                "ITICK_API_KEY belum diset."
            )
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

    for attempt in range(
        ITICK_MAX_RETRIES + 1
    ):

        wait_before_itick_request()

        try:

            response = requests.get(
                ITICK_KLINE_URL,
                params=params,
                headers=headers,
                timeout=20
            )

        except requests.RequestException as error:

            return {
                "ok": False,
                "symbol": symbol,
                "error": (
                    f"K-line request error: "
                    f"{error}"
                )
            }

        # ----------------------------------------------------
        # RATE LIMIT
        # ----------------------------------------------------
        if response.status_code == 429:

            if attempt >= ITICK_MAX_RETRIES:

                return {
                    "ok": False,
                    "symbol": symbol,
                    "error": (
                        "HTTP 429 selepas retry."
                    )
                }

            retry_after = (
                response.headers.get(
                    "Retry-After"
                )
            )

            if retry_after:

                try:

                    sleep_seconds = float(
                        retry_after
                    )

                except Exception:

                    sleep_seconds = (
                        2.0
                        * (2 ** attempt)
                    )

            else:

                sleep_seconds = (
                    2.0
                    * (2 ** attempt)
                )

            sleep_seconds = min(
                max(
                    sleep_seconds,
                    2.0
                ),
                30.0
            )

            time.sleep(
                sleep_seconds
            )

            continue

        # ----------------------------------------------------
        # HTTP ERROR
        # ----------------------------------------------------
        if response.status_code != 200:

            return {
                "ok": False,
                "symbol": symbol,
                "error": (
                    f"HTTP "
                    f"{response.status_code}"
                )
            }

        # ----------------------------------------------------
        # JSON
        # ----------------------------------------------------
        try:

            data = response.json()

        except ValueError:

            return {
                "ok": False,
                "symbol": symbol,
                "error": (
                    "K-line response "
                    "bukan JSON."
                )
            }

        # ----------------------------------------------------
        # ITICK RESPONSE
        # ----------------------------------------------------
        if data.get("code") != 0:

            return {
                "ok": False,
                "symbol": symbol,
                "error": str(data)
            }

        raw = data.get(
            "data",
            []
        )

        if not isinstance(raw, list):

            raw = []

        if not raw:

            return {
                "ok": False,
                "symbol": symbol,
                "error": (
                    "Tiada daily candle."
                )
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

    return {
        "ok": False,
        "symbol": symbol,
        "error": (
            "K-line request gagal."
        )
    }


# ============================================================
# CALCULATE SYMBOL
# ============================================================
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
                "Supertrend "
                "calculation gagal."
            )
        }

    latest = calculated[-1]

    historical_signals = []

    for candle in calculated:

        if candle.get(
            "signal",
            0
        ) != 0:

            historical_signals.append(
                format_result(candle)
            )

    return {
        "ok": True,
        "symbol": symbol,
        "latest": format_result(
            latest
        ),
        "historical_signals":
            historical_signals
    }


# ============================================================
# HEALTH
# ============================================================
@app.get("/api/health")
def health():

    return {
        "ok": True,
        "service":
            "bursa-supertrend-scanner"
    }


# ============================================================
# RECENT SIGNALS
# ============================================================
@app.get("/api/recent-signals")
def recent_signals():

    signals = get_recent_signals()

    return {
        "ok": True,
        "days": RECENT_SIGNAL_DAYS,
        "count": len(signals),
        "signals": signals
    }


# ============================================================
# BATCH UNIVERSE SCAN
# ============================================================
@app.get("/api/test/universe")
def test_universe(
    start: int = 0,
    batch_size: int = DEFAULT_BATCH_SIZE
):

    if start < 0:

        start = 0

    if batch_size < 1:

        batch_size = 1

    if batch_size > 10:

        batch_size = 10

    end = min(
        start + batch_size,
        len(BURSA_UNIVERSE)
    )

    symbols = BURSA_UNIVERSE[
        start:end
    ]

    # --------------------------------------------------------
    # Recent signals that should be skipped
    # --------------------------------------------------------
    saved_symbols = (
        get_recent_symbols()
    )

    scanner = []
    errors = []
    skipped = []

    for symbol in symbols:

        # ----------------------------------------------------
        # SKIP RECENT SIGNAL
        # ----------------------------------------------------
        if symbol in saved_symbols:

            skipped.append(
                {
                    "symbol": symbol,
                    "reason":
                        "RECENT SIGNAL"
                }
            )

            continue

        # ----------------------------------------------------
        # NORMAL SCAN
        # ----------------------------------------------------
        result = calculate_symbol(
            symbol
        )

        if not result.get("ok"):

            errors.append(
                {
                    "symbol": symbol,
                    "error": result.get(
                        "error",
                        "Unknown error"
                    )
                }
            )

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

            scanner.append(
                signal
            )

            # -----------------------------------------------
            # AUTO SAVE SIGNAL
            # -----------------------------------------------
            save_recent_signal(
                signal
            )

    next_start = end

    done = (
        next_start
        >= len(BURSA_UNIVERSE)
    )

    return {
        "start": start,
        "end": end,
        "batch_size": len(symbols),
        "requested_count":
            len(BURSA_UNIVERSE),
        "scanned_count": end,
        "remaining_count":
            len(BURSA_UNIVERSE) - end,
        "done": done,
        "scanner": scanner,
        "signals_only":
            scanner.copy(),
        "skipped_count":
            len(skipped),
        "skipped": skipped,
        "error_count":
            len(errors),
        "errors": errors
    }


# ============================================================
# RESCAN SAVED
# ============================================================
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

            errors.append(
                {
                    "symbol": symbol,
                    "error": result.get(
                        "error",
                        "Unknown error"
                    )
                }
            )

            continue

        latest = result.get(
            "latest",
            {}
        )

        # ----------------------------------------------------
        # STILL SIGNAL
        # ----------------------------------------------------
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

            results.append(
                {
                    "symbol": symbol,
                    "status":
                        "SIGNAL STILL ACTIVE",
                    "signal": signal
                }
            )

        # ----------------------------------------------------
        # NO LONGER SIGNAL
        # ----------------------------------------------------
        else:

            remove_recent_signal(
                symbol
            )

            results.append(
                {
                    "symbol": symbol,
                    "status":
                        "SIGNAL CLEARED"
                }
            )

    return {
        "ok": True,
        "rescanned_count":
            len(saved),
        "results": results,
        "error_count":
            len(errors),
        "errors": errors,
        "recent_signals":
            get_recent_signals()
    }


# ============================================================
# TEST 5
# ============================================================
@app.get("/api/test/5")
def test_five():

    results = []

    for symbol in TEST_SYMBOLS:

        results.append(
            calculate_symbol(symbol)
        )

    return {
        "requested_count":
            len(TEST_SYMBOLS),
        "results": results
    }


# ============================================================
# HISTORICAL
# ============================================================
@app.get("/api/test/history")
def test_history():

    results = []

    for symbol in TEST_SYMBOLS:

        result = calculate_symbol(
            symbol
        )

        if result.get("ok"):

            results.append(
                {
                    "symbol": symbol,
                    "historical_signals":
                        result.get(
                            "historical_signals",
                            []
                        )
                }
            )

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

<title>
Bursa Supertrend Scanner
</title>

<style>

body{
    margin:0;
    padding:20px;
    background:#101010;
    color:#eeeeee;
    font-family:Arial,sans-serif;
}

.container{
    max-width:900px;
    margin:auto;
}

h1{
    margin-bottom:5px;
    font-size:24px;
}

.subtitle{
    color:#999999;
    margin-bottom:20px;
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
    font-weight:bold;
}

button:disabled{
    opacity:.5;
}

.secondary{
    background:#333333;
}

.danger{
    background:#7b3030;
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
    font-size:16px;
}

label{
    display:block;
    margin-top:15px;
    color:#cccccc;
}

pre{
    margin-top:20px;
    padding:15px;
    background:#181818;
    border-radius:8px;
    overflow-x:auto;
    white-space:pre-wrap;
    word-break:break-word;
    font-size:13px;
}

.card{
    margin-top:20px;
    padding:15px;
    background:#181818;
    border-radius:10px;
}

.card-title{
    font-size:18px;
    font-weight:bold;
    margin-bottom:10px;
}

.info{
    margin-top:15px;
    padding:12px;
    background:#181818;
    border-radius:8px;
    color:#bbbbbb;
    font-size:13px;
}

.signal{
    padding:12px;
    margin-top:8px;
    border-radius:8px;
    background:#222222;
    border-left:4px solid #1f8f4d;
}

.signal-title{
    font-size:16px;
    font-weight:bold;
}

.signal-detail{
    color:#bbbbbb;
    margin-top:5px;
    font-size:13px;
}

.empty{
    color:#888888;
    font-size:14px;
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
======================================================= -->

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
     SCANNER
======================================================= -->

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

<b>SCAN 5</b>
akan scan 5 kaunter sahaja dan berhenti.

<br><br>

<b>SCAN NEXT 5</b>
akan sambung batch berikutnya.

<br><br>

Kaunter yang masih berada dalam
<b>RECENT SIGNALS</b>
akan di-skip secara automatik.

<br><br>

<b>RESCAN SAVED</b>
digunakan jika mahu periksa semula signal yang telah disimpan.

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

let scannedCount = 0;

let scanDone = false;


// ========================================================
// FORMAT NUMBER
// ========================================================
function formatPrice(value){

    if(value === null || value === undefined){
        return "-";
    }

    return Number(value).toFixed(3);
}


// ========================================================
// LOAD RECENT SIGNALS
// ========================================================
async function loadRecentSignals(){

    const box =
        document.getElementById(
            "recentSignals"
        );

    box.textContent =
        "Loading...";

    try{

        const response =
            await fetch(
                "/api/recent-signals"
            );

        if(!response.ok){

            throw new Error(
                "HTTP " +
                response.status
            );
        }

        const data =
            await response.json();

        if(
            !data.signals ||
            data.signals.length === 0
        ){

            box.innerHTML =
                '<div class="empty">' +
                'Tiada Recent Signal.' +
                '</div>';

            return;
        }

        let html = "";

        data.signals.forEach(
            function(signal){

                html +=
                    '<div class="signal">' +

                    '<div class="signal-title">' +
                    signal.symbol +
                    ' | RM ' +
                    formatPrice(
                        signal.close
                    ) +
                    '</div>' +

                    '<div class="signal-detail">' +
                    signal.date +
                    ' | ' +
                    signal.signal_name +
                    '</div>' +

                    '<div class="signal-detail">' +
                    'Expired: ' +
                    formatExpiry(
                        signal.expires_at
                    ) +
                    '</div>' +

                    '</div>';
            }
        );

        box.innerHTML = html;

    }
    catch(error){

        box.textContent =
            "ERROR\n\n" +
            error;
    }
}


// ========================================================
// FORMAT EXPIRY
// ========================================================
function formatExpiry(value){

    if(!value){
        return "-";
    }

    try{

        const d =
            new Date(value);

        return d.toLocaleString(
            "ms-MY",
            {
                dateStyle:"medium",
                timeStyle:"short"
            }
        );

    }
    catch(error){

        return value;
    }
}


// ========================================================
// FIRST BATCH
// ========================================================
async function scanFirstBatch(){

    nextStart = 0;

    totalSignals = 0;

    allSignals = [];

    allErrors = [];

    scannedCount = 0;

    scanDone = false;

    const out =
        document.getElementById(
            "out"
        );

    out.textContent =
        "MULA SCAN 5...\n\n";

    await scanOneBatch();
}


// ========================================================
// NEXT BATCH
// ========================================================
async function scanNextBatch(){

    if(scanDone){

        return;
    }

    const maxSignals =
        parseInt(
            document.getElementById(
                "maxSignals"
            ).value
        ) || 5;

    if(
        totalSignals >=
        maxSignals
    ){

        outMessage(
            "\nMAX SIGNALS " +
            maxSignals +
            " SUDAH DICAPAI.\n"
        );

        updateButtons();

        return;
    }

    await scanOneBatch();
}


// ========================================================
// SCAN ONE BATCH
// ========================================================
async function scanOneBatch(){

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

    const maxSignals =
        parseInt(
            document.getElementById(
                "maxSignals"
            ).value
        ) || 5;

    scanButton.disabled = true;

    nextButton.disabled = true;

    out.textContent +=
        "--------------------------------\n" +
        "SCAN BATCH\n" +
        "Kaunter: " +
        nextStart +
        " → " +
        (nextStart + 5) +
        "\n\n";

    try{

        const response =
            await fetch(
                "/api/test/universe" +
                "?start=" +
                nextStart +
                "&batch_size=5"
            );

        if(!response.ok){

            throw new Error(
                "HTTP " +
                response.status
            );
        }

        const data =
            await response.json();

        scannedCount =
            data.scanned_count;

        scanDone =
            data.done;


        // ------------------------------------------------
        // SIGNAL
        // ------------------------------------------------
        if(
            data.scanner &&
            data.scanner.length > 0
        ){

            for(
                const signal
                of data.scanner
            ){

                if(
                    totalSignals >=
                    maxSignals
                ){

                    break;
                }

                allSignals.push(
                    signal
                );

                totalSignals++;

                out.textContent +=
                    "SIGNAL #" +
                    totalSignals +
                    "\n" +

                    signal.symbol +
                    " | " +
                    signal.date +
                    " | RM " +
                    formatPrice(
                        signal.close
                    ) +
                    " | " +
                    signal.signal_name +
                    "\n\n";
            }

        }
        else{

            out.textContent +=
                "Tiada signal " +
                "baru dalam batch ini.\n\n";
        }


        // ------------------------------------------------
        // SKIPPED
        // ------------------------------------------------
        if(
            data.skipped &&
            data.skipped.length > 0
        ){

            out.textContent +=
                "SKIP RECENT SIGNAL: " +
                data.skipped.length +
                "\n";

            data.skipped.forEach(
                function(item){

                    out.textContent +=
                        item.symbol +
                        " -> RECENT SIGNAL\n";
                }
            );

            out.textContent += "\n";
        }


        // ------------------------------------------------
        // ERRORS
        // ------------------------------------------------
        if(
            data.errors &&
            data.errors.length > 0
        ){

            data.errors.forEach(
                function(error){

                    allErrors.push(
                        error
                    );
                }
            );

            out.textContent +=
                "Error batch: " +
                data.errors.length +
                "\n\n";
        }


        // ------------------------------------------------
        // PROGRESS
        // ------------------------------------------------
        out.textContent +=
            "--------------------------------\n" +

            "Progress: " +
            scannedCount +
            " / " +
            data.requested_count +
            "\n" +

            "Signal: " +
            totalSignals +
            " / " +
            maxSignals +
            "\n";


        nextStart =
            data.end;


        // ------------------------------------------------
        // STOP CONDITIONS
        // ------------------------------------------------
        if(
            totalSignals >=
            maxSignals
        ){

            scanDone = true;

            out.textContent +=
                "\nMAX SIGNALS " +
                maxSignals +
                " DICAPAI.\n" +
                "SCAN DIHENTIKAN.\n";

        }
        else if(data.done){

            scanDone = true;

            out.textContent +=
                "\nSEMUA UNIVERSE SELESAI.\n";

        }
        else{

            out.textContent +=
                "\nBATCH INI SELESAI.\n" +
                "Tekan SCAN NEXT 5 " +
                "untuk sambung.\n";
        }


        // ------------------------------------------------
        // ALL SIGNALS
        // ------------------------------------------------
        out.textContent +=
            "\n================================\n" +
            "SIGNAL DIJUMPAI SETAKAT INI\n" +
            "================================\n";

        if(
            allSignals.length > 0
        ){

            allSignals.forEach(
                function(
                    signal,
                    index
                ){

                    out.textContent +=
                        (index + 1) +
                        ". " +
                        signal.symbol +
                        " | " +
                        signal.date +
                        " | RM " +
                        formatPrice(
                            signal.close
                        ) +
                        " | " +
                        signal.signal_name +
                        "\n";
                }
            );

        }
        else{

            out.textContent +=
                "Tiada signal ditemui.\n";
        }


        // ------------------------------------------------
        // ERRORS
        // ------------------------------------------------
        if(
            allErrors.length > 0
        ){

            out.textContent +=
                "\nERROR / 429:\n";

            allErrors.forEach(
                function(error){

                    out.textContent +=
                        error.symbol +
                        " -> " +
                        error.error +
                        "\n";
                }
            );
        }


        // ------------------------------------------------
        // REFRESH RECENT SIGNALS
        // ------------------------------------------------
        await loadRecentSignals();

    }
    catch(error){

        out.textContent +=
            "\nSCAN ERROR\n\n" +
            error;
    }

    scanButton.disabled = false;

    updateButtons();
}


// ========================================================
// BUTTON STATE
// ========================================================
function updateButtons(){

    const scanButton =
        document.getElementById(
            "scanButton"
        );

    const nextButton =
        document.getElementById(
            "nextButton"
        );

    const maxSignals =
        parseInt(
            document.getElementById(
                "maxSignals"
            ).value
        ) || 5;

    scanButton.disabled = false;

    if(
        scanDone ||
        totalSignals >= maxSignals
    ){

        nextButton.disabled = true;

    }
    else{

        nextButton.disabled = false;
    }
}


// ========================================================
// OUTPUT MESSAGE
// ========================================================
function outMessage(
    message
){

    document.getElementById(
        "out"
    ).textContent +=
        message;
}


// ========================================================
// RESCAN SAVED
// ========================================================
async function rescanSaved(){

    const out =
        document.getElementById(
            "out"
        );

    out.textContent =
        "RESCAN SAVED...\n\n" +
        "Sila tunggu...\n";

    try{

        const response =
            await fetch(
                "/api/rescan-saved"
            );

        if(!response.ok){

            throw new Error(
                "HTTP " +
                response.status
            );
        }

        const data =
            await response.json();

        out.textContent =
            "================================\n" +
            "RESCAN SAVED\n" +
            "================================\n\n" +

            "Jumlah saved sebelum rescan: " +
            data.rescanned_count +
            "\n\n";


        if(
            data.results &&
            data.results.length > 0
        ){

            data.results.forEach(
                function(item){

                    out.textContent +=
                        item.symbol +
                        " -> " +
                        item.status +
                        "\n";

                    if(
                        item.signal
                    ){

                        out.textContent +=
                            "   " +
                            item.signal.signal_name +
                            " | RM " +
                            formatPrice(
                                item.signal.close
                            ) +
                            " | " +
                            item.signal.date +
                            "\n";
                    }
                }
            );

        }
        else{

            out.textContent +=
                "Tiada saved signal.\n";
        }


        if(
            data.errors &&
            data.errors.length > 0
        ){

            out.textContent +=
                "\nERROR:\n";

            data.errors.forEach(
                function(error){

                    out.textContent +=
                        error.symbol +
                        " -> " +
                        error.error +
                        "\n";
                }
            );
        }


        out.textContent +=
            "\n================================\n" +
            "RECENT SIGNALS TERKINI\n" +
            "================================\n";

        if(
            data.recent_signals &&
            data.recent_signals.length > 0
        ){

            data.recent_signals.forEach(
                function(
                    signal,
                    index
                ){

                    out.textContent +=
                        (index + 1) +
                        ". " +
                        signal.symbol +
                        " | " +
                        signal.date +
                        " | RM " +
                        formatPrice(
                            signal.close
                        ) +
                        " | " +
                        signal.signal_name +
                        "\n";
                }
            );

        }
        else{

            out.textContent +=
                "Tiada Recent Signal.\n";
        }


        await loadRecentSignals();

    }
    catch(error){

        out.textContent =
            "RESCAN SAVED ERROR\n\n" +
            error;
    }
}


// ========================================================
// HISTORICAL
// ========================================================
async function testHistory(){

    const out =
        document.getElementById(
            "out"
        );

    out.textContent =
        "Testing historical signals...";

    try{

        const response =
            await fetch(
                "/api/test/history"
            );

        if(!response.ok){

            throw new Error(
                "HTTP " +
                response.status
            );
        }

        const data =
            await response.json();

        out.textContent =
            JSON.stringify(
                data,
                null,
                2
            );

    }
    catch(error){

        out.textContent =
            "HISTORY ERROR\n\n" +
            error;
    }
}


// ========================================================
// INITIAL LOAD
// ========================================================
loadRecentSignals();

</script>

</body>

</html>
'''
