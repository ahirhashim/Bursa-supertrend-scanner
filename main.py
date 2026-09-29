import os
import time
import threading
import requests

from datetime import datetime, timezone, timedelta

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
# DATE FORMAT
# ============================================================

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

        previous_close = None

        if i > 0:
            previous_close = parsed[i - 1]["c"]

        trs.append(
            true_range(
                candle["h"],
                candle["l"],
                previous_close
            )
        )

    # --------------------------------------------------------
    # WILDER ATR
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

        # ----------------------------------------------------
        # DIRECTION
        # -1 = BULL
        #  1 = BEAR
        # ----------------------------------------------------

        if previous_direction is None:

            if candle["c"] <= final_upper:
                direction = 1
            else:
                direction = -1

        else:

            if previous_direction == 1:

                if candle["c"] > final_upper:
                    direction = -1
                else:
                    direction = 1

            else:

                if candle["c"] < final_lower:
                    direction = 1
                else:
                    direction = -1

        # ----------------------------------------------------
        # SUPERTREND VALUE
        # ----------------------------------------------------

        if direction < 0:
            supertrend = final_lower
        else:
            supertrend = final_upper

        # ----------------------------------------------------
        # FLIP
        # ----------------------------------------------------

        flip = False

        if (
            previous_direction is not None
            and previous_direction > 0
            and direction < 0
        ):

            flip = True

        # ----------------------------------------------------
        # HIGH BREAK
        # ----------------------------------------------------

        high_break = False

        if i > 0:

            previous_high = parsed[i - 1]["h"]

            if (
                direction < 0
                and candle["c"] > previous_high
            ):

                high_break = True

        # ----------------------------------------------------
        # SIGNAL
        # ----------------------------------------------------

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
            "error":
                "ITICK_API_KEY belum diset."
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
                "error":
                    f"K-line request error: {error}"
            }

        # ----------------------------------------------------
        # 429
        # ----------------------------------------------------

        if response.status_code == 429:

            if attempt >= ITICK_MAX_RETRIES:

                return {
                    "ok": False,
                    "symbol": symbol,
                    "error":
                        "HTTP 429 selepas retry."
                }

            retry_after = response.headers.get(
                "Retry-After"
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
                max(sleep_seconds, 2.0),
                30.0
            )

            time.sleep(
                sleep_seconds
            )

            continue

        # ----------------------------------------------------
        # OTHER HTTP ERROR
        # ----------------------------------------------------

        if response.status_code != 200:

            return {
                "ok": False,
                "symbol": symbol,
                "error":
                    f"HTTP {response.status_code}"
            }

        try:

            data = response.json()

        except ValueError:

            return {
                "ok": False,
                "symbol": symbol,
                "error":
                    "K-line response bukan JSON."
            }

        if data.get("code") != 0:

            return {
                "ok": False,
                "symbol": symbol,
                "error":
                    str(data)
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
                "error":
                    "Tiada daily candle."
            }

        try:

            raw = sorted(
                raw,
                key=lambda x:
                    float(x.get("t", 0))
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
        "error":
            "K-line request gagal."
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
            "error":
                "Supertrend calculation gagal."
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
        "latest":
            format_result(latest),
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
# BATCH UNIVERSE SCAN
#
# start = kedudukan mula
# batch_size = berapa kaunter satu batch
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

    scanner = []
    errors = []

    for symbol in symbols:

        result = calculate_symbol(
            symbol
        )

        if not result.get("ok"):

            errors.append(
                {
                    "symbol": symbol,
                    "error":
                        result.get(
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

        signal = latest.get(
            "signal",
            0
        )

        if signal != 0:

            scanner.append(
                {
                    "symbol": symbol,
                    **latest
                }
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
        "scanned_count":
            end,
        "remaining_count":
            len(BURSA_UNIVERSE) - end,
        "done": done,
        "scanner": scanner,
        "signals_only":
            scanner.copy(),
        "error_count":
            len(errors),
        "errors": errors
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
        "results":
            results
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
        "results":
            results
    }


# ============================================================
# HOME PAGE
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
def home():

    return '''
<!DOCTYPE html>

<html>

<head>

<meta name="viewport"
      content="width=device-width,
               initial-scale=1.0">

<title>
Bursa Supertrend Scanner
</title>

<style>

body {
    margin: 0;
    padding: 20px;
    background: #101010;
    color: #eeeeee;
    font-family: Arial, sans-serif;
}

.container {
    max-width: 900px;
    margin: auto;
}

h1 {
    margin-bottom: 5px;
    font-size: 24px;
}

.subtitle {
    color: #999999;
    margin-bottom: 20px;
}

button {
    width: 100%;
    padding: 14px;
    margin-top: 10px;
    border: none;
    border-radius: 8px;
    background: #1f8f4d;
    color: white;
    font-size: 16px;
    font-weight: bold;
}

button:disabled {
    opacity: 0.5;
}

input {
    width: 100%;
    box-sizing: border-box;
    padding: 12px;
    margin-top: 8px;
    margin-bottom: 8px;
    border-radius: 8px;
    border: 1px solid #444444;
    background: #181818;
    color: white;
    font-size: 16px;
}

label {
    display: block;
    margin-top: 15px;
    color: #cccccc;
}

pre {
    margin-top: 20px;
    padding: 15px;
    background: #181818;
    border-radius: 8px;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 13px;
}

.info {
    margin-top: 15px;
    padding: 12px;
    background: #181818;
    border-radius: 8px;
    color: #bbbbbb;
    font-size: 13px;
}

.signal {
    margin-top: 8px;
    padding: 10px;
    background: #163b25;
    border-radius: 6px;
}

.error {
    color: #ff7777;
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
    onclick="scanUniverse()"
>
SCAN BURSA UNIVERSE
</button>


<button
    onclick="testHistory()"
>
TEST HISTORICAL SIGNAL
</button>


<div class="info">
Scanner akan scan 5 kaunter setiap batch.
Keputusan setiap batch akan dipaparkan dahulu
sebelum scanner sambung ke batch berikutnya.
</div>


<pre id="out">
Ready.
</pre>

</div>


<script>

async function scanUniverse() {

    const out =
        document.getElementById("out");

    const button =
        document.getElementById("scanButton");

    const maxSignals =
        parseInt(
            document.getElementById("maxSignals").value
        ) || 5;

    button.disabled = true;

    let start = 0;
    let totalSignals = 0;
    let allSignals = [];
    let allErrors = [];
    let scannedCount = 0;
    let done = false;

    out.textContent =
        "MULA SCAN...\n\n";

    try {

        while (
            !done &&
            totalSignals < maxSignals
        ) {

            out.textContent +=
                "--------------------------------\n" +
                "SCAN BATCH\n" +
                "Kaunter seterusnya: " +
                start +
                "\n\n";

            const response =
                await fetch(
                    "/api/test/universe" +
                    "?start=" +
                    start +
                    "&batch_size=5"
                );

            if (!response.ok) {

                throw new Error(
                    "HTTP " + response.status
                );

            }

            const data =
                await response.json();

            scannedCount =
                data.scanned_count;

            done =
                data.done;

            if (
                data.scanner &&
                data.scanner.length > 0
            ) {

                for (
                    const signal of data.scanner
                ) {

                    if (
                        totalSignals >= maxSignals
                    ) {
                        break;
                    }

                    allSignals.push(signal);

                    totalSignals++;

                    out.textContent +=
                        "SIGNAL #" +
                        totalSignals +
                        "\n" +
                        signal.symbol +
                        " | " +
                        signal.date +
                        " | RM " +
                        signal.close +
                        " | " +
                        signal.signal_name +
                        "\n\n";
                }

            } else {

                out.textContent +=
                    "Tiada signal dalam batch ini.\n\n";
            }

            if (
                data.errors &&
                data.errors.length > 0
            ) {

                for (
                    const error of data.errors
                ) {

                    allErrors.push(error);
                }

                out.textContent +=
                    "Error batch: " +
                    data.errors.length +
                    "\n";
            }

            out.textContent +=
                "Progress: " +
                scannedCount +
                " / " +
                data.requested_count +
                "\n" +
                "Signal: " +
                totalSignals +
                " / " +
                maxSignals +
                "\n\n";

            start =
                data.end;

            if (
                !done &&
                totalSignals < maxSignals
            ) {

                out.textContent +=
                    "Tunggu sekejap sebelum batch seterusnya...\n\n";

                await sleep(1500);
            }
        }

        out.textContent +=
            "\n================================\n" +
            "SCAN SELESAI\n" +
            "================================\n\n" +
            "Jumlah kaunter discan: " +
            scannedCount +
            "\n" +
            "Signal ditemui: " +
            totalSignals +
            "\n";

        if (
            totalSignals >= maxSignals
        ) {

            out.textContent +=
                "Status: CUKUP " +
                maxSignals +
                " SIGNAL - STOP\n";

        } else if (done) {

            out.textContent +=
                "Status: SEMUA UNIVERSE SELESAI\n";
        }

        out.textContent += "\n";

        if (
            allSignals.length > 0
        ) {

            out.textContent +=
                "SIGNAL DIJUMPAI:\n\n";

            allSignals.forEach(
                function(signal, index) {

                    out.textContent +=
                        (index + 1) +
                        ". " +
                        signal.symbol +
                        " | " +
                        signal.date +
                        " | RM " +
                        signal.close +
                        " | " +
                        signal.signal_name +
                        "\n";
                }
            );

        } else {

            out.textContent +=
                "Tiada signal ditemui.\n";
        }

        if (
            allErrors.length > 0
        ) {

            out.textContent +=
                "\n\nERROR / 429:\n";

            allErrors.forEach(
                function(error) {

                    out.textContent +=
                        error.symbol +
                        " -> " +
                        error.error +
                        "\n";
                }
            );
        }

    } catch (error) {

        out.textContent +=
            "\nSCAN ERROR\n\n" +
            error;
    }

    button.disabled = false;
}


function sleep(ms) {

    return new Promise(
        resolve =>
            setTimeout(resolve, ms)
    );
}


async function testHistory() {

    const out =
        document.getElementById("out");

    out.textContent =
        "Testing historical signals...";

    try {

        const response =
            await fetch(
                "/api/test/history"
            );

        if (!response.ok) {

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

    } catch (error) {

        out.textContent =
            "HISTORY ERROR\n\n" +
            error;
    }
}

</script>

</body>

</html>
'''
           