import os
import time
import threading
import requests

from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, HTTPException
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


# ============================================================
# SUPERTREND SETTINGS
# ============================================================

ATR_LENGTH = 10
SUPERTREND_FACTOR = 1.0


# ============================================================
# SCANNER SETTINGS
# ============================================================

DEFAULT_MAX_SIGNALS = 5

# Jarak minimum antara request iTick
# Boleh dinaikkan jika masih banyak HTTP 429.
ITICK_REQUEST_DELAY = float(
    os.getenv("ITICK_REQUEST_DELAY", "1.0")
)

# Maksimum retry apabila HTTP 429
ITICK_MAX_RETRIES = 2


# ============================================================
# REQUEST PACING
# ============================================================

_itick_lock = threading.Lock()
_last_itick_request = 0.0


def wait_before_itick_request():
    """
    Pastikan request ke iTick tidak dihantar terlalu rapat.
    """

    global _last_itick_request

    with _itick_lock:

        now = time.monotonic()

        wait_time = ITICK_REQUEST_DELAY - (
            now - _last_itick_request
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
# BURSA UNIVERSE
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
# TIME FORMAT
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

def true_range(high, low, previous_close):

    if previous_close is None:

        return high - low

    return max(
        high - low,
        abs(high - previous_close),
        abs(low - previous_close)
    )


# ============================================================
# SUPERTREND CALCULATION
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

            timestamp = float(candle.get("t", 0))

            open_price = float(candle.get("o", 0))
            high = float(candle.get("h", 0))
            low = float(candle.get("l", 0))
            close = float(candle.get("c", 0))

            volume = candle.get("v", 0)

            parsed.append(
                {
                    "t": timestamp,
                    "o": open_price,
                    "h": high,
                    "l": low,
                    "c": close,
                    "v": volume
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

        tr = true_range(
            candle["h"],
            candle["l"],
            previous_close
        )

        trs.append(tr)

    # --------------------------------------------------------
    # WILDER ATR / RMA
    # --------------------------------------------------------

    atr_values = [None] * len(parsed)

    if len(trs) >= atr_length:

        first_atr = sum(
            trs[:atr_length]
        ) / atr_length

        atr_values[atr_length - 1] = first_atr

        for i in range(
            atr_length,
            len(trs)
        ):

            previous_atr = atr_values[i - 1]

            current_tr = trs[i]

            atr_values[i] = (
                (
                    previous_atr
                    * (atr_length - 1)
                )
                + current_tr
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
        #
        # 0 = NONE
        # 1 = FLIP
        # 2 = HIGH BREAK
        # 3 = FLIP + HIGH BREAK
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
# FETCH ITICK DAILY K-LINE
# ============================================================

def fetch_kline(
    symbol,
    limit=50
):

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
                    "K-line request error: "
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
        # NON-429 HTTP ERROR
        # ----------------------------------------------------

        if response.status_code != 200:

            return {
                "ok": False,
                "symbol": symbol,
                "error": (
                    f"HTTP {response.status_code}"
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
        # ITICK API ERROR
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

        if not isinstance(
            raw,
            list
        ):

            raw = []

        if not raw:

            return {
                "ok": False,
                "symbol": symbol,
                "error": (
                    "Tiada daily candle."
                )
            }

        # ----------------------------------------------------
        # SORT OLD → NEW
        # ----------------------------------------------------

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
        "error": "K-line request gagal."
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

    candles = fetched["candles"]

    calculated = calculate_supertrend(
        candles,
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
# TEST 5 SYMBOLS
# ============================================================

@app.get("/api/test/5")
def test_five():

    results = []

    for symbol in TEST_SYMBOLS:

        result = calculate_symbol(
            symbol
        )

        results.append(result)

    return {
        "requested_count":
            len(TEST_SYMBOLS),

        "results":
            results
    }


# ============================================================
# BURSA UNIVERSE SCANNER
#
# IMPORTANT:
# Scan satu demi satu.
#
# Bila jumpa signal:
#   terus masukkan ke scanner.
#
# Bila cukup max_signals:
#   STOP.
#
# Tidak perlu scan baki universe.
# ============================================================

@app.get("/api/test/universe")
def test_universe(
    max_signals: int = DEFAULT_MAX_SIGNALS
):

    # --------------------------------------------------------
    # VALIDATE
    # --------------------------------------------------------

    if max_signals < 1:

        max_signals = 1

    if max_signals > len(
        BURSA_UNIVERSE
    ):

        max_signals = len(
            BURSA_UNIVERSE
        )

    scanner = []

    errors = []

    scanned_count = 0

    stopped_early = False

    # --------------------------------------------------------
    # SCAN SEQUENTIAL
    # --------------------------------------------------------

    for symbol in BURSA_UNIVERSE:

        # ----------------------------------------------------
        # STOP BILA SUDAH CUKUP SIGNAL
        # ----------------------------------------------------

        if len(scanner) >= max_signals:

            stopped_early = True

            break

        scanned_count += 1

        result = calculate_symbol(
            symbol
        )

        # ----------------------------------------------------
        # ERROR
        # ----------------------------------------------------

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
        # SIGNAL SAH
        #
        # Kita gunakan signal 1, 2 atau 3.
        # NONE = 0 tidak masuk scanner.
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # STOP SEBAIK SAHAJA CUKUP
        # ----------------------------------------------------

        if len(scanner) >= max_signals:

            stopped_early = True

            break

    # --------------------------------------------------------
    # SIGNAL ONLY
    # --------------------------------------------------------

    signals_only = scanner.copy()

    # --------------------------------------------------------
    # RESPONSE
    # --------------------------------------------------------

    return {
        "requested_count":
            len(BURSA_UNIVERSE),

        "scanned_count":
            scanned_count,

        "max_signals":
            max_signals,

        "successful_signal_count":
            len(scanner),

        "scanner":
            scanner,

        "signals_only":
            signals_only,

        "error_count":
            len(errors),

        "errors":
            errors,

        "stopped_early":
            stopped_early,

        "message":
            (
                f"Scan berhenti selepas "
                f"{len(scanner)} signal "
                f"ditemui."
                if stopped_early
                else
                "Universe selesai discan."
            )
    }


# ============================================================
# HISTORICAL TEST
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

    return """
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

button:active {
    transform: scale(0.99);
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
    max="36"
    value="5"
>


<button onclick="scanUniverse()">
SCAN BURSA UNIVERSE
</button>


<button onclick="testHistory()">
TEST HISTORICAL SIGNAL
</button>


<div class="info">
Scanner akan scan kaunter satu demi satu.
Bila cukup jumlah signal yang dipilih,
scanner akan berhenti dan tidak meneruskan
request kepada baki kaunter.
</div>


<pre id="out">
Ready.
</pre>

</div>


<script>

async function scanUniverse() {

    const out =
        document.getElementById("out");

    const maxSignals =
        document.getElementById(
            "maxSignals"
        ).value;

    out.textContent =
        "Scanning Bursa Universe...\\n" +
        "Max Signals = " +
        maxSignals +
        "\\n\\n" +
        "Sila tunggu...";


    try {

        const response =
            await fetch(
                "/api/test/universe?max_signals="
                + encodeURIComponent(
                    maxSignals
                )
            );

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
            "SCAN ERROR\\n\\n" +
            error;

    }

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
            "HISTORY ERROR\\n\\n" +
            error;

    }

}

</script>

</body>

</html>
"""