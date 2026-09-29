import os
import requests

from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse


app = FastAPI(title="Bursa Supertrend Scanner")


# =========================================================
# ITICK SETTINGS
# =========================================================

ITICK_API_KEY = os.getenv("ITICK_API_KEY", "")

ITICK_KLINE_URL = "https://api-free.itick.org/stock/kline"


# =========================================================
# SUPERTREND SETTINGS
# =========================================================

ATR_LENGTH = 10
SUPERTREND_FACTOR = 1.0


# =========================================================
# BURSA UNIVERSE
# =========================================================

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


# =========================================================
# HOME PAGE
# =========================================================

@app.get("/", response_class=HTMLResponse)
def home():

    return """
    <!doctype html>

    <html>

    <head>

        <meta name="viewport"
              content="width=device-width,initial-scale=1">

        <title>Bursa Supertrend Scanner</title>

        <style>

        body {
            font-family: Arial;
            background: #07111f;
            color: #eef5ff;
            margin: 0;
            padding: 20px;
        }

        .card {
            max-width: 650px;
            margin: auto;
            background: #0d1b2d;
            padding: 20px;
            border-radius: 18px;
        }

        h1 {
            font-size: 22px;
            margin-bottom: 8px;
        }

        .muted {
            color: #9db0c8;
            font-size: 13px;
        }

        button {
            width: 100%;
            padding: 13px;
            border: 0;
            border-radius: 10px;
            background: #247cff;
            color: white;
            font-weight: bold;
            font-size: 15px;
            margin-top: 8px;
        }

        pre {
            white-space: pre-wrap;
            background: #071524;
            padding: 12px;
            border-radius: 10px;
            margin-top: 14px;
            overflow-wrap: break-word;
            font-size: 12px;
        }

        </style>

    </head>


    <body>

        <div class="card">

            <h1>
                BURSA SUPERTREND SCANNER
            </h1>

            <p class="muted">
                Bursa Universe • Daily • ATR 10 / Factor 1.0
            </p>


            <button onclick="testUniverse()">
                SCAN BURSA UNIVERSE
            </button>


            <button onclick="testHistory()">
                TEST HISTORICAL SIGNAL
            </button>


            <pre id="out">
Belum scan.
            </pre>

        </div>


        <script>

        async function testUniverse() {

            const out =
                document.getElementById("out");


            out.textContent =
                "Sedang scan Bursa Universe...";


            try {

                const response =
                    await fetch(
                        "/api/test/universe"
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
                    "Ralat sambungan: "
                    + error;

            }

        }


        async function testHistory() {

            const out =
                document.getElementById("out");


            out.textContent =
                "Sedang mencari signal sejarah...";


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
                    "Ralat sambungan: "
                    + error;

            }

        }

        </script>

    </body>

    </html>
    """


# =========================================================
# HEALTH
# =========================================================

@app.get("/api/health")
def health():

    return {

        "ok": True,

        "service":
            "bursa-supertrend-scanner"

    }


# =========================================================
# TRUE RANGE
# =========================================================

def true_range(
    high,
    low,
    previous_close
):

    if previous_close is None:

        return high - low


    return max(

        high - low,

        abs(
            high - previous_close
        ),

        abs(
            low - previous_close
        )

    )


# =========================================================
# WILDER ATR / RMA
# =========================================================

def calculate_atr(
    candles,
    length
):

    tr_values = []


    for i, candle in enumerate(candles):

        high = float(
            candle["h"]
        )

        low = float(
            candle["l"]
        )


        if i == 0:

            previous_close = None

        else:

            previous_close = float(
                candles[i - 1]["c"]
            )


        tr = true_range(

            high,

            low,

            previous_close

        )


        tr_values.append(tr)


    atr_values = [
        None
    ] * len(candles)


    if len(tr_values) < length:

        return atr_values


    first_atr = (

        sum(
            tr_values[:length]
        )

        / length

    )


    atr_values[
        length - 1
    ] = first_atr


    previous_atr = first_atr


    for i in range(
        length,
        len(tr_values)
    ):

        current_atr = (

            (
                previous_atr
                * (length - 1)
            )

            + tr_values[i]

        ) / length


        atr_values[i] = (
            current_atr
        )


        previous_atr = (
            current_atr
        )


    return atr_values


# =========================================================
# SUPERTREND
# =========================================================

def calculate_supertrend(
    candles,
    atr_length,
    factor
):

    atr_values = calculate_atr(

        candles,

        atr_length

    )


    results = []


    previous_final_upper = None

    previous_final_lower = None

    previous_direction = None

    previous_close = None


    for i, candle in enumerate(
        candles
    ):

        high = float(
            candle["h"]
        )

        low = float(
            candle["l"]
        )

        close = float(
            candle["c"]
        )


        atr = atr_values[i]


        result = dict(candle)


        result["atr"] = atr


        if atr is None:

            result[
                "supertrend"
            ] = None

            result[
                "direction"
            ] = None

            result[
                "trend"
            ] = None

            result[
                "flip"
            ] = False

            result[
                "high_break"
            ] = False

            result[
                "signal"
            ] = 0

            result[
                "signal_name"
            ] = "NONE"


            results.append(
                result
            )


            previous_close = (
                close
            )


            continue


        hl2 = (
            high + low
        ) / 2.0


        basic_upper = (

            hl2
            + factor * atr

        )


        basic_lower = (

            hl2
            - factor * atr

        )


        # -------------------------------------------------
        # FINAL UPPER
        # -------------------------------------------------

        if (
            previous_final_upper
            is None
        ):

            final_upper = (
                basic_upper
            )

        else:

            if (

                basic_upper
                < previous_final_upper

                or (

                    previous_close
                    is not None

                    and
                    previous_close
                    > previous_final_upper

                )

            ):

                final_upper = (
                    basic_upper
                )

            else:

                final_upper = (
                    previous_final_upper
                )


        # -------------------------------------------------
        # FINAL LOWER
        # -------------------------------------------------

        if (
            previous_final_lower
            is None
        ):

            final_lower = (
                basic_lower
            )

        else:

            if (

                basic_lower
                > previous_final_lower

                or (

                    previous_close
                    is not None

                    and
                    previous_close
                    < previous_final_lower

                )

            ):

                final_lower = (
                    basic_lower
                )

            else:

                final_lower = (
                    previous_final_lower
                )


        # -------------------------------------------------
        # DIRECTION
        #
        # -1 = BULL
        #  1 = BEAR
        # -------------------------------------------------

        if (
            previous_direction
            is None
        ):

            if (
                close
                <= final_upper
            ):

                direction = 1

            else:

                direction = -1


        elif (
            previous_direction
            == 1
        ):

            if (
                close
                > final_upper
            ):

                direction = -1

            else:

                direction = 1


        else:

            if (
                close
                < final_lower
            ):

                direction = 1

            else:

                direction = -1


        # -------------------------------------------------
        # SUPERTREND
        # -------------------------------------------------

        if direction < 0:

            supertrend = (
                final_lower
            )

        else:

            supertrend = (
                final_upper
            )


        # -------------------------------------------------
        # TREND
        # -------------------------------------------------

        if direction < 0:

            trend = "BULL"

        else:

            trend = "BEAR"


        # -------------------------------------------------
        # FLIP
        # -------------------------------------------------

        flip = False


        if (

            previous_direction
            is not None

            and
            previous_direction
            > 0

            and
            direction
            < 0

        ):

            flip = True


        # -------------------------------------------------
        # HIGH BREAK
        # -------------------------------------------------

        high_break = False


        if i > 0:

            previous_high = float(
                candles[i - 1]["h"]
            )


            if (

                direction < 0

                and
                close
                > previous_high

            ):

                high_break = True


        # -------------------------------------------------
        # SIGNAL
        # -------------------------------------------------

        if (
            flip
            and
            high_break
        ):

            signal = 3

            signal_name = (
                "FLIP + HIGH BREAK"
            )


        elif flip:

            signal = 1

            signal_name = (
                "FLIP"
            )


        elif high_break:

            signal = 2

            signal_name = (
                "HIGH BREAK"
            )


        else:

            signal = 0

            signal_name = (
                "NONE"
            )


        # -------------------------------------------------
        # SAVE
        # -------------------------------------------------

        result[
            "basic_upper"
        ] = basic_upper

        result[
            "basic_lower"
        ] = basic_lower

        result[
            "final_upper"
        ] = final_upper

        result[
            "final_lower"
        ] = final_lower

        result[
            "supertrend"
        ] = supertrend

        result[
            "direction"
        ] = direction

        result[
            "trend"
        ] = trend

        result[
            "flip"
        ] = flip

        result[
            "high_break"
        ] = high_break

        result[
            "signal"
        ] = signal

        result[
            "signal_name"
        ] = signal_name


        results.append(
            result
        )


        # -------------------------------------------------
        # UPDATE
        # -------------------------------------------------

        previous_final_upper = (
            final_upper
        )

        previous_final_lower = (
            final_lower
        )

        previous_direction = (
            direction
        )

        previous_close = (
            close
        )


    return results


# =========================================================
# FORMAT TIMESTAMP — MALAYSIA UTC+8
# =========================================================

def format_timestamp(
    timestamp
):

    try:

        malaysia_tz = timezone(
            timedelta(hours=8)
        )


        dt = datetime.fromtimestamp(

            float(timestamp) / 1000,

            tz=malaysia_tz

        )


        return dt.strftime(
            "%Y-%m-%d"
        )


    except Exception:

        return str(timestamp)


# =========================================================
# FORMAT RESULT
# =========================================================

def format_result(
    candle
):

    return {

        "date":
            format_timestamp(
                candle.get("t")
            ),

        "close":
            candle.get("c"),

        "high":
            candle.get("h"),

        "low":
            candle.get("l"),

        "atr10":

            round(
                candle["atr"],
                6
            )

            if candle.get("atr")
            is not None

            else None,

        "supertrend":

            round(
                candle["supertrend"],
                6
            )

            if candle.get(
                "supertrend"
            )
            is not None

            else None,

        "direction":
            candle.get(
                "direction"
            ),

        "trend":
            candle.get(
                "trend"
            ),

        "flip":
            candle.get(
                "flip",
                False
            ),

        "high_break":
            candle.get(
                "high_break",
                False
            ),

        "signal":
            candle.get(
                "signal",
                0
            ),

        "signal_name":
            candle.get(
                "signal_name",
                "NONE"
            )

    }


# =========================================================
# FETCH KLINE DIRECT
# =========================================================

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

        "accept":
            "application/json",

        "token":
            ITICK_API_KEY

    }


    params = {

        "region":
            "MY",

        "exchange":
            "MYX",

        "code":
            symbol,

        "kType":
            8,

        "limit":
            limit

    }


    try:

        response = requests.get(

            ITICK_KLINE_URL,

            params=params,

            headers=headers,

            timeout=20

        )


        data = response.json()


    except requests.RequestException as error:

        return {

            "ok": False,

            "symbol": symbol,

            "error":
                f"K-line request error: {error}"

        }


    except ValueError:

        return {

            "ok": False,

            "symbol": symbol,

            "error":
                "K-line response bukan JSON."

        }


    if response.status_code != 200:

        return {

            "ok": False,

            "symbol": symbol,

            "error":
                f"HTTP {response.status_code}"

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


    if not isinstance(
        raw,
        list
    ):

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
                float(
                    x.get(
                        "t",
                        0
                    )
                )

        )

    except Exception:

        pass


    return {

        "ok": True,

        "symbol": symbol,

        "candles": raw

    }


# =========================================================
# CALCULATE ONE SYMBOL
# =========================================================

def calculate_symbol(
    symbol
):

    fetched = fetch_kline(
        symbol,
        50
    )


    if not fetched.get("ok"):

        return fetched


    candles = fetched[
        "candles"
    ]


    calculated = (
        calculate_supertrend(

            candles,

            ATR_LENGTH,

            SUPERTREND_FACTOR

        )
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

        if (
            candle.get(
                "signal",
                0
            ) != 0
        ):

            historical_signals.append(

                format_result(
                    candle
                )

            )


    return {

        "ok": True,

        "symbol": symbol,

        "latest":
            format_result(
                latest
            ),

        "historical_signals":
            historical_signals

    }


# =========================================================
# TEST 5 COUNTERS
# =========================================================

@app.get("/api/test/5")
def test_five():

    if not ITICK_API_KEY:

        raise HTTPException(

            status_code=500,

            detail=
                "ITICK_API_KEY belum diset."

        )


    scanner = []


    for symbol in TEST_SYMBOLS:

        result = calculate_symbol(
            symbol
        )


        if not result.get("ok"):

            scanner.append({

                "symbol":
                    symbol,

                "status":
                    "ERROR",

                "error":
                    result.get(
                        "error"
                    )

            })

            continue


        latest = result[
            "latest"
        ]


        scanner.append({

            "symbol":
                symbol,

            "close":
                latest["close"],

            "supertrend":
                latest["supertrend"],

            "trend":
                latest["trend"],

            "flip":
                latest["flip"],

            "high_break":
                latest["high_break"],

            "signal":
                latest["signal"],

            "signal_name":
                latest["signal_name"]

        })


    return {

        "ok": True,

        "stage":
            "universe_5_complete",

        "timeframe":
            "1D",

        "settings": {

            "atr_length":
                ATR_LENGTH,

            "factor":
                SUPERTREND_FACTOR

        },

        "count":
            len(TEST_SYMBOLS),

        "scanner":
            scanner

    }


# =========================================================
# BURSA UNIVERSE SCANNER
# =========================================================

@app.get("/api/test/universe")
def test_universe():

    if not ITICK_API_KEY:

        raise HTTPException(

            status_code=500,

            detail=
                "ITICK_API_KEY belum diset."

        )


    scanner = []

    signals_only = []

    errors = []


    for symbol in BURSA_UNIVERSE:

        result = calculate_symbol(
            symbol
        )


        if not result.get("ok"):

            error_item = {

                "symbol":
                    symbol,

                "error":
                    result.get(
                        "error"
                    )

            }


            errors.append(
                error_item
            )


            continue


        latest = result[
            "latest"
        ]


        item = {

            "symbol":
                symbol,

            "close":
                latest["close"],

            "supertrend":
                latest["supertrend"],

            "trend":
                latest["trend"],

            "flip":
                latest["flip"],

            "high_break":
                latest["high_break"],

            "signal":
                latest["signal"],

            "signal_name":
                latest["signal_name"]

        }


        scanner.append(
            item
        )


        # -------------------------------------------------
        # CURRENT SIGNAL ONLY
        # -------------------------------------------------

        if (
            latest.get(
                "signal",
                0
            ) != 0
        ):

            signals_only.append(
                item
            )


    return {

        "ok": True,

        "stage":
            "universe_complete",

        "timeframe":
            "1D",

        "settings": {

            "atr_length":
                ATR_LENGTH,

            "factor":
                SUPERTREND_FACTOR

        },

        "requested_count":
            len(BURSA_UNIVERSE),

        "successful_count":
            len(scanner),

        "error_count":
            len(errors),

        "scanner":
            scanner,

        "signals_only":
            signals_only,

        "errors":
            errors

    }


# =========================================================
# HISTORICAL TEST — 5 COUNTERS
# =========================================================

@app.get("/api/test/history")
def test_history():

    if not ITICK_API_KEY:

        raise HTTPException(

            status_code=500,

            detail=
                "ITICK_API_KEY belum diset."

        )


    results = []


    for symbol in TEST_SYMBOLS:

        result = calculate_symbol(
            symbol
        )


        if not result.get("ok"):

            results.append({

                "symbol":
                    symbol,

                "status":
                    "ERROR",

                "error":
                    result.get(
                        "error"
                    )

            })

            continue


        historical = result.get(

            "historical_signals",

            []

        )


        results.append({

            "symbol":
                symbol,

            "signal_count":
                len(
                    historical
                ),

            "signals":
                historical

        })


    return {

        "ok": True,

        "stage":
            "historical_test_complete",

        "timeframe":
            "1D",

        "settings": {

            "atr_length":
                ATR_LENGTH,

            "factor":
                SUPERTREND_FACTOR,

            "candles_per_symbol":
                50

        },

        "symbols":
            TEST_SYMBOLS,

        "results":
            results

    }