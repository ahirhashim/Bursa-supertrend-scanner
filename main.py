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

ITICK_SYMBOL_URL = "https://api-free.itick.org/symbol/list"
ITICK_KLINE_URL = "https://api-free.itick.org/stock/kline"


# =========================================================
# SUPERTREND SETTINGS
# =========================================================

ATR_LENGTH = 10
SUPERTREND_FACTOR = 1.0


# =========================================================
# TEST UNIVERSE
# =========================================================

TEST_SYMBOLS = [
    "D&O",
    "SRIDGE",
    "DNEX",
    "ZETRIX",
    "INARI"
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
            max-width: 600px;
            margin: auto;
            background: #0d1b2d;
            padding: 20px;
            border-radius: 18px;
        }

        h1 {
            font-size: 22px;
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

            <h1>BURSA SUPERTREND SCANNER</h1>

            <p class="muted">
                iTick OHLC Verification • INARI • Daily
            </p>

            <button onclick="checkINARIOHLC()">
                CHECK INARI OHLC
            </button>

            <button onclick="testHistory()">
                TEST HISTORICAL SIGNAL
            </button>

            <pre id="out">Belum diuji.</pre>

        </div>


        <script>

        async function checkINARIOHLC() {

            const out =
                document.getElementById("out");

            out.textContent =
                "Sedang mengambil OHLC INARI daripada iTick...";


            try {

                const response =
                    await fetch("/api/test/inari-ohlc");

                const data =
                    await response.json();

                out.textContent =
                    JSON.stringify(data, null, 2);

            } catch (error) {

                out.textContent =
                    "Ralat sambungan: " + error;

            }

        }


        async function testHistory() {

            const out =
                document.getElementById("out");

            out.textContent =
                "Sedang mencari signal sejarah...";


            try {

                const response =
                    await fetch("/api/test/history");

                const data =
                    await response.json();

                out.textContent =
                    JSON.stringify(data, null, 2);

            } catch (error) {

                out.textContent =
                    "Ralat sambungan: " + error;

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
        "service": "bursa-supertrend-scanner"
    }


# =========================================================
# TRUE RANGE
# =========================================================

def true_range(high, low, previous_close):

    if previous_close is None:

        return high - low

    return max(
        high - low,
        abs(high - previous_close),
        abs(low - previous_close)
    )


# =========================================================
# RMA / WILDER ATR
# =========================================================

def calculate_atr(candles, length):

    tr_values = []


    for i, candle in enumerate(candles):

        high = float(candle["h"])
        low = float(candle["l"])


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


    atr_values = [None] * len(candles)


    if len(tr_values) < length:

        return atr_values


    first_atr = (
        sum(tr_values[:length])
        / length
    )


    atr_values[length - 1] = first_atr


    previous_atr = first_atr


    for i in range(length, len(tr_values)):

        current_atr = (
            (
                previous_atr * (length - 1)
            )
            + tr_values[i]
        ) / length


        atr_values[i] = current_atr

        previous_atr = current_atr


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


    for i, candle in enumerate(candles):

        high = float(candle["h"])
        low = float(candle["l"])
        close = float(candle["c"])


        atr = atr_values[i]


        result = dict(candle)

        result["atr"] = atr


        if atr is None:

            result["supertrend"] = None
            result["direction"] = None
            result["trend"] = None
            result["flip"] = False
            result["high_break"] = False
            result["signal"] = 0
            result["signal_name"] = "NONE"

            results.append(result)

            previous_close = close

            continue


        hl2 = (high + low) / 2.0


        basic_upper = (
            hl2 + factor * atr
        )


        basic_lower = (
            hl2 - factor * atr
        )


        # -------------------------------------------------
        # FINAL UPPER
        # -------------------------------------------------

        if previous_final_upper is None:

            final_upper = basic_upper

        else:

            if (
                basic_upper < previous_final_upper
                or (
                    previous_close is not None
                    and previous_close > previous_final_upper
                )
            ):

                final_upper = basic_upper

            else:

                final_upper = previous_final_upper


        # -------------------------------------------------
        # FINAL LOWER
        # -------------------------------------------------

        if previous_final_lower is None:

            final_lower = basic_lower

        else:

            if (
                basic_lower > previous_final_lower
                or (
                    previous_close is not None
                    and previous_close < previous_final_lower
                )
            ):

                final_lower = basic_lower

            else:

                final_lower = previous_final_lower


        # -------------------------------------------------
        # DIRECTION
        #
        # -1 = BULL
        #  1 = BEAR
        # -------------------------------------------------

        if previous_direction is None:

            if close <= final_upper:

                direction = 1

            else:

                direction = -1


        elif previous_direction == 1:

            if close > final_upper:

                direction = -1

            else:

                direction = 1


        else:

            if close < final_lower:

                direction = 1

            else:

                direction = -1


        # -------------------------------------------------
        # SUPERTREND
        # -------------------------------------------------

        if direction < 0:

            supertrend = final_lower

        else:

            supertrend = final_upper


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
            previous_direction is not None
            and previous_direction > 0
            and direction < 0
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
                and close > previous_high
            ):

                high_break = True


        # -------------------------------------------------
        # SIGNAL
        # -------------------------------------------------

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


        # -------------------------------------------------
        # SAVE
        # -------------------------------------------------

        result["basic_upper"] = basic_upper
        result["basic_lower"] = basic_lower
        result["final_upper"] = final_upper
        result["final_lower"] = final_lower

        result["supertrend"] = supertrend
        result["direction"] = direction
        result["trend"] = trend

        result["flip"] = flip
        result["high_break"] = high_break
        result["signal"] = signal
        result["signal_name"] = signal_name


        results.append(result)


        # -------------------------------------------------
        # UPDATE
        # -------------------------------------------------

        previous_final_upper = final_upper
        previous_final_lower = final_lower
        previous_direction = direction
        previous_close = close


    return results


# =========================================================
# FORMAT DATE
# =========================================================

def format_timestamp(timestamp):

    try:

        malaysia_tz = timezone(
            timedelta(hours=8)
        )

        dt = datetime.fromtimestamp(
            float(timestamp) / 1000,
            tz=malaysia_tz
        )

        return dt.strftime("%Y-%m-%d")

    except Exception:

        return str(timestamp)


# =========================================================
# FORMAT RESULT
# =========================================================

def format_result(candle):

    return {

        "date": format_timestamp(
            candle.get("t")
        ),

        "close": candle.get("c"),

        "high": candle.get("h"),

        "low": candle.get("l"),

        "atr10": (
            round(
                candle["atr"],
                6
            )
            if candle.get("atr") is not None
            else None
        ),

        "supertrend": (
            round(
                candle["supertrend"],
                6
            )
            if candle.get("supertrend") is not None
            else None
        ),

        "direction": candle.get(
            "direction"
        ),

        "trend": candle.get(
            "trend"
        ),

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


# =========================================================
# FETCH ONE SYMBOL
# =========================================================

def fetch_symbol_data(symbol):

    headers = {
        "accept": "application/json",
        "token": ITICK_API_KEY
    }


    # =====================================================
    # SYMBOL LOOKUP
    # =====================================================

    symbol_params = {
        "type": "stock",
        "region": "MY",
        "code": symbol
    }


    try:

        symbol_response = requests.get(
            ITICK_SYMBOL_URL,
            params=symbol_params,
            headers=headers,
            timeout=20
        )


        symbol_data = (
            symbol_response.json()
        )


    except requests.RequestException as error:

        return {
            "ok": False,
            "symbol": symbol,
            "error":
                f"Symbol lookup request error: {error}"
        }


    except ValueError:

        return {
            "ok": False,
            "symbol": symbol,
            "error":
                "Symbol lookup bukan JSON."
        }


    if symbol_response.status_code != 200:

        return {
            "ok": False,
            "symbol": symbol,
            "error":
                f"Symbol HTTP error: {symbol_response.status_code}"
        }


    if symbol_data.get("code") != 0:

        return {
            "ok": False,
            "symbol": symbol,
            "error":
                f"Symbol lookup error: {symbol_data}"
        }


    symbol_list = symbol_data.get(
        "data",
        []
    )


    if not isinstance(
        symbol_list,
        list
    ):

        symbol_list = []


    if not symbol_list:

        return {
            "ok": False,
            "symbol": symbol,
            "error":
                "Symbol tidak dijumpai."
        }


    symbol_info = symbol_list[0]


    actual_code = symbol_info.get("c")
    actual_name = symbol_info.get("n")
    actual_exchange = symbol_info.get("e")


    if not actual_code:

        return {
            "ok": False,
            "symbol": symbol,
            "error":
                "Symbol tiada code."
        }


    # =====================================================
    # KLINE
    # =====================================================

    kline_params = {

        "region": "MY",

        "exchange":
            actual_exchange or "",

        "code":
            actual_code,

        "kType":
            8,

        "limit":
            50
    }


    try:

        kline_response = requests.get(
            ITICK_KLINE_URL,
            params=kline_params,
            headers=headers,
            timeout=20
        )


        kline_data = (
            kline_response.json()
        )


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
                "K-line bukan JSON."
        }


    if kline_response.status_code != 200:

        return {
            "ok": False,
            "symbol": symbol,
            "error":
                f"K-line HTTP error: {kline_response.status_code}"
        }


    if kline_data.get("code") != 0:

        return {
            "ok": False,
            "symbol": symbol,
            "error":
                f"K-line error: {kline_data}"
        }


    raw = kline_data.get(
        "data",
        []
    )


    if isinstance(raw, list):

        candles = raw

    else:

        candles = []


    if not candles:

        return {
            "ok": False,
            "symbol": symbol,
            "name": actual_name,
            "exchange": actual_exchange,
            "error":
                "Tiada daily candle."
        }


    # =====================================================
    # SORT OLD -> NEW
    # =====================================================

    try:

        candles = sorted(
            candles,
            key=lambda x:
                float(x.get("t", 0))
        )

    except Exception:

        pass


    # =====================================================
    # CALCULATE
    # =====================================================

    calculated = calculate_supertrend(
        candles,
        ATR_LENGTH,
        SUPERTREND_FACTOR
    )


    if not calculated:

        return {
            "ok": False,
            "symbol": symbol,
            "error":
                "Supertrend tidak dapat dikira."
        }


    latest = calculated[-1]


    # =====================================================
    # HISTORICAL SIGNALS
    # =====================================================

    historical_signals = []


    for candle in calculated:

        if candle.get(
            "signal",
            0
        ) != 0:

            historical_signals.append(
                format_result(candle)
            )


    # =====================================================
    # RETURN
    # =====================================================

    return {

        "ok": True,

        "symbol":
            actual_code,

        "name":
            actual_name,

        "exchange":
            actual_exchange,

        "latest":
            format_result(latest),

        "historical_signals":
            historical_signals,

        # Simpan candle mentah untuk verification.
        "raw_candles":
            candles
    }


# =========================================================
# INARI OHLC VERIFICATION
# =========================================================

@app.get("/api/test/inari-ohlc")
def test_inari_ohlc():

    if not ITICK_API_KEY:

        raise HTTPException(
            status_code=500,
            detail=
                "ITICK_API_KEY belum diset dalam Render Environment."
        )


    result = fetch_symbol_data(
        "INARI"
    )


    if not result.get("ok"):

        raise HTTPException(
            status_code=502,
            detail=result
        )


    candles = result.get(
        "raw_candles",
        []
    )


    selected = []


    # =====================================================
    # AMBIL CANDLE SEKITAR 30 JULAI
    # =====================================================

    for candle in candles:

        date_string = format_timestamp(
            candle.get("t")
        )


        if (
            "2026-07-25"
            <= date_string
            <=
            "2026-08-05"
        ):

            selected.append({

                "date":
                    date_string,

                "open":
                    candle.get("o"),

                "high":
                    candle.get("h"),

                "low":
                    candle.get("l"),

                "close":
                    candle.get("c"),

                "volume":
                    candle.get("v"),

                "timestamp":
                    candle.get("t")

            })


    return {

        "ok": True,

        "stage":
            "inari_ohlc_verification",

        "symbol":
            "INARI",

        "exchange":
            result.get(
                "exchange"
            ),

        "timeframe":
            "1D",

        "range":
            "2026-07-25 to 2026-08-05",

        "count":
            len(selected),

        "candles":
            selected

    }


# =========================================================
# TEST HISTORICAL SIGNAL
# =========================================================

@app.get("/api/test/history")
def test_history():

    if not ITICK_API_KEY:

        raise HTTPException(
            status_code=500,
            detail=
                "ITICK_API_KEY belum diset dalam Render Environment."
        )


    results = []


    for symbol in TEST_SYMBOLS:

        result = fetch_symbol_data(
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
                result.get(
                    "symbol"
                ),

            "name":
                result.get(
                    "name"
                ),

            "signal_count":
                len(historical),

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