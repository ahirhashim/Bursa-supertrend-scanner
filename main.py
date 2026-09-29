import os
import requests
from datetime import datetime, timezone

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
            max-width: 560px;
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
                D&O Supertrend test • Daily • ATR 10 / Factor 1.0
            </p>

            <button onclick="testDO()">
                TEST D&O SUPERTREND
            </button>

            <pre id="out">Belum diuji.</pre>

        </div>


        <script>

        async function testDO() {

            const out =
                document.getElementById("out");

            out.textContent =
                "Sedang mengambil data D&O dan mengira Supertrend...";


            try {

                const response =
                    await fetch("/api/test/do");

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

            previous_close = float(candles[i - 1]["c"])


        tr = true_range(
            high,
            low,
            previous_close
        )

        tr_values.append(tr)


    atr_values = [None] * len(candles)


    if len(tr_values) < length:

        return atr_values


    # -----------------------------------------------------
    # INITIAL RMA = SMA OF FIRST LENGTH TRUE RANGES
    # -----------------------------------------------------

    first_atr = sum(
        tr_values[:length]
    ) / length

    atr_values[length - 1] = first_atr


    # -----------------------------------------------------
    # WILDER RMA
    # -----------------------------------------------------

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

def calculate_supertrend(candles, atr_length, factor):

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


        # -------------------------------------------------
        # NOT ENOUGH DATA FOR ATR
        # -------------------------------------------------

        if atr is None:

            result["basic_upper"] = None
            result["basic_lower"] = None
            result["final_upper"] = None
            result["final_lower"] = None
            result["supertrend"] = None
            result["direction"] = None

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
        # FINAL UPPER BAND
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
        # FINAL LOWER BAND
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
        # TradingView convention:
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
        # SUPERTREND VALUE
        # -------------------------------------------------

        if direction < 0:

            supertrend = final_lower

        else:

            supertrend = final_upper


        # -------------------------------------------------
        # SAVE RESULT
        # -------------------------------------------------

        result["basic_upper"] = basic_upper
        result["basic_lower"] = basic_lower
        result["final_upper"] = final_upper
        result["final_lower"] = final_lower
        result["supertrend"] = supertrend
        result["direction"] = direction


        if direction < 0:

            result["trend"] = "BULL"

        else:

            result["trend"] = "BEAR"


        results.append(result)


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

        dt = datetime.fromtimestamp(
            float(timestamp) / 1000,
            tz=timezone.utc
        )

        return dt.strftime("%Y-%m-%d")

    except Exception:

        return str(timestamp)


# =========================================================
# TEST D&O
# =========================================================

@app.get("/api/test/do")
def test_do():

    # -----------------------------------------------------
    # CHECK API KEY
    # -----------------------------------------------------

    if not ITICK_API_KEY:

        raise HTTPException(
            status_code=500,
            detail="ITICK_API_KEY belum diset dalam Render Environment."
        )


    headers = {
        "accept": "application/json",
        "token": ITICK_API_KEY
    }


    # =====================================================
    # STEP 1
    # LOOKUP SYMBOL D&O
    # =====================================================

    symbol_params = {
        "type": "stock",
        "region": "MY",
        "code": "D&O"
    }


    try:

        symbol_response = requests.get(
            ITICK_SYMBOL_URL,
            params=symbol_params,
            headers=headers,
            timeout=20
        )


        print("")
        print("==============================================")
        print("iTick SYMBOL LOOKUP D&O")
        print("URL:", symbol_response.url)
        print(
            "HTTP STATUS:",
            symbol_response.status_code
        )
        print("RAW SYMBOL RESPONSE:")
        print(symbol_response.text[:10000])
        print("==============================================")


        symbol_data = symbol_response.json()


    except requests.RequestException as error:

        raise HTTPException(
            status_code=502,
            detail=f"Gagal menghubungi iTick symbol list: {error}"
        )


    except ValueError:

        raise HTTPException(
            status_code=502,
            detail="iTick symbol list memberi respons bukan JSON."
        )


    # -----------------------------------------------------
    # CHECK SYMBOL HTTP STATUS
    # -----------------------------------------------------

    if symbol_response.status_code != 200:

        raise HTTPException(
            status_code=502,
            detail=f"iTick symbol list HTTP error: {symbol_data}"
        )


    # -----------------------------------------------------
    # CHECK ITICK RESPONSE CODE
    # -----------------------------------------------------

    if symbol_data.get("code") != 0:

        raise HTTPException(
            status_code=502,
            detail=f"iTick symbol lookup error: {symbol_data}"
        )


    symbol_list = symbol_data.get("data", [])


    if not isinstance(symbol_list, list):

        symbol_list = []


    # =====================================================
    # SYMBOL NOT FOUND
    # =====================================================

    if not symbol_list:

        return {
            "ok": False,
            "stage": "symbol_lookup",
            "message": "iTick tidak memulangkan symbol untuk D&O.",
            "requested_symbol": "D&O",
            "region": "MY",
            "symbol_lookup": [],
            "count": 0,
            "candles": []
        }


    # =====================================================
    # GET SYMBOL INFORMATION
    # =====================================================

    symbol_info = symbol_list[0]

    actual_code = symbol_info.get("c")
    actual_name = symbol_info.get("n")
    actual_exchange = symbol_info.get("e")


    print(
        "FOUND SYMBOL CODE:",
        actual_code
    )

    print(
        "FOUND SYMBOL NAME:",
        actual_name
    )

    print(
        "FOUND EXCHANGE:",
        actual_exchange
    )


    # =====================================================
    # CHECK SYMBOL CODE
    # =====================================================

    if not actual_code:

        return {
            "ok": False,
            "stage": "symbol_lookup",
            "message": "iTick pulangkan symbol tetapi tiada code.",
            "symbol_lookup": symbol_list,
            "count": 0,
            "candles": []
        }


    # =====================================================
    # STEP 2
    # SINGLE STOCK K-LINE
    # =====================================================

    kline_params = {
        "region": "MY",
        "exchange": actual_exchange or "",
        "code": actual_code,
        "kType": 8,

        # Ambil lebih banyak candle supaya
        # ATR(10) / Supertrend mempunyai
        # data permulaan yang mencukupi.
        "limit": 50
    }


    try:

        kline_response = requests.get(
            ITICK_KLINE_URL,
            params=kline_params,
            headers=headers,
            timeout=20
        )


        print("")
        print("==============================================")
        print("iTick SINGLE KLINE D&O")
        print("URL:", kline_response.url)
        print(
            "HTTP STATUS:",
            kline_response.status_code
        )
        print("RAW KLINE RESPONSE:")
        print(kline_response.text[:10000])
        print("==============================================")


        kline_data = kline_response.json()


    except requests.RequestException as error:

        raise HTTPException(
            status_code=502,
            detail=f"Gagal menghubungi iTick K-line: {error}"
        )


    except ValueError:

        raise HTTPException(
            status_code=502,
            detail="iTick K-line memberi respons bukan JSON."
        )


    # -----------------------------------------------------
    # CHECK HTTP STATUS
    # -----------------------------------------------------

    if kline_response.status_code != 200:

        raise HTTPException(
            status_code=502,
            detail=f"iTick K-line HTTP error: {kline_data}"
        )


    # -----------------------------------------------------
    # CHECK ITICK RESPONSE CODE
    # -----------------------------------------------------

    if kline_data.get("code") != 0:

        raise HTTPException(
            status_code=502,
            detail=f"iTick K-line error: {kline_data}"
        )


    # =====================================================
    # GET CANDLES
    # =====================================================

    raw = kline_data.get("data", [])


    if isinstance(raw, list):

        candles = raw

    else:

        candles = []


    print(
        "KLINE DATA TYPE:",
        type(raw).__name__
    )

    print(
        "FINAL CANDLE COUNT:",
        len(candles)
    )


    # =====================================================
    # SORT CANDLES
    #
    # Supertrend mesti dikira dari candle lama
    # kepada candle baru.
    # =====================================================

    try:

        candles = sorted(
            candles,
            key=lambda x: float(x.get("t", 0))
        )

    except Exception:

        pass


    # =====================================================
    # CALCULATE SUPERTREND
    # =====================================================

    supertrend_data = calculate_supertrend(
        candles,
        ATR_LENGTH,
        SUPERTREND_FACTOR
    )


    # =====================================================
    # LAST 15 RESULTS
    # =====================================================

    latest_results = []


    for candle in supertrend_data[-15:]:

        latest_results.append({

            "date": format_timestamp(
                candle.get("t")
            ),

            "close": candle.get("c"),

            "high": candle.get("h"),

            "low": candle.get("l"),

            "atr10": (
                round(candle["atr"], 6)
                if candle.get("atr") is not None
                else None
            ),

            "supertrend": (
                round(candle["supertrend"], 6)
                if candle.get("supertrend") is not None
                else None
            ),

            "direction": candle.get(
                "direction"
            ),

            "trend": candle.get(
                "trend"
            )
        })


    # =====================================================
    # LATEST SUPERTREND
    # =====================================================

    latest = (
        supertrend_data[-1]
        if supertrend_data
        else None
    )


    latest_supertrend = None
    latest_direction = None
    latest_trend = None


    if latest:

        latest_supertrend = (
            round(
                latest["supertrend"],
                6
            )
            if latest.get("supertrend") is not None
            else None
        )

        latest_direction = latest.get(
            "direction"
        )

        latest_trend = latest.get(
            "trend"
        )


    # =====================================================
    # FINAL RESPONSE
    # =====================================================

    return {

        "ok": bool(candles),

        "stage": "supertrend_complete",

        "requested_symbol": "D&O",

        "symbol": actual_code,

        "name": actual_name,

        "region": "MY",

        "exchange": actual_exchange,

        "timeframe": "1D",

        "requested": 50,

        "count": len(candles),

        "settings": {

            "atr_length": ATR_LENGTH,

            "factor": SUPERTREND_FACTOR

        },

        "latest": {

            "supertrend": latest_supertrend,

            "direction": latest_direction,

            "trend": latest_trend

        },

        "latest_15": latest_results

    }