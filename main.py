import os
import requests

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse


app = FastAPI(title="Bursa Supertrend Scanner")


# =========================================================
# iTick SETTINGS
# =========================================================

ITICK_API_KEY = os.getenv("ITICK_API_KEY", "")

ITICK_SYMBOL_URL = "https://api-free.itick.org/symbol/list"
ITICK_KLINE_URL = "https://api-free.itick.org/stock/kline"


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

        body{
            font-family:Arial;
            background:#07111f;
            color:#eef5ff;
            margin:0;
            padding:20px
        }

        .card{
            max-width:560px;
            margin:auto;
            background:#0d1b2d;
            padding:20px;
            border-radius:18px
        }

        h1{
            font-size:22px
        }

        .muted{
            color:#9db0c8;
            font-size:13px
        }

        button{
            width:100%;
            padding:13px;
            border:0;
            border-radius:10px;
            background:#247cff;
            color:white;
            font-weight:bold;
            font-size:15px
        }

        pre{
            white-space:pre-wrap;
            background:#071524;
            padding:12px;
            border-radius:10px;
            margin-top:14px;
            overflow-wrap:break-word;
        }

        </style>

    </head>


    <body>

        <div class="card">

            <h1>BURSA SUPERTREND SCANNER</h1>

            <p class="muted">
                D&O iTick connection test • Daily • ATR 10 / Factor 1.0
            </p>

            <button onclick="testDO()">
                TEST D&O SEKARANG
            </button>

            <pre id="out">
Belum diuji.
            </pre>

        </div>


        <script>

        async function testDO(){

            const out = document.getElementById('out');

            out.textContent =
                'Sedang menghubungi iTick...';


            try{

                const r =
                    await fetch('/api/test/do');


                const d =
                    await r.json();


                out.textContent =
                    JSON.stringify(d,null,2);


            }catch(e){

                out.textContent =
                    'Ralat sambungan: ' + e;

            }

        }

        </script>

    </body>

    </html>
    """


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/api/health")
def health():

    return {

        "ok": True,

        "service":
            "bursa-supertrend-scanner"

    }


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

            500,

            "ITICK_API_KEY belum diset dalam Render Environment."

        )


    headers = {

        "accept":
            "application/json",

        "token":
            ITICK_API_KEY

    }


    # =====================================================
    # STEP 1
    # LOOKUP SYMBOL D&O
    # =====================================================

    symbol_params = {

        "type":
            "stock",

        "region":
            "MY",

        "code":
            "D&O"

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
        print(
            symbol_response.text[:10000]
        )

        print("==============================================")


        symbol_data =
            symbol_response.json()


    except requests.RequestException as e:

        raise HTTPException(

            502,

            f"Gagal menghubungi iTick symbol list: {e}"

        )


    except ValueError:

        raise HTTPException(

            502,

            "iTick symbol list memberi respons bukan JSON."

        )


    # -----------------------------------------------------
    # CHECK SYMBOL HTTP
    # -----------------------------------------------------

    if symbol_response.status_code != 200:

        raise HTTPException(

            502,

            f"iTick symbol list HTTP error: {symbol_data}"

        )


    # -----------------------------------------------------
    # CHECK SYMBOL API
    # -----------------------------------------------------

    if symbol_data.get("code") != 0:

        raise HTTPException(

            502,

            f"iTick symbol lookup error: {symbol_data}"

        )


    symbol_list =
        symbol_data.get("data", [])


    if not isinstance(symbol_list, list):

        symbol_list = []


    # =====================================================
    # SYMBOL NOT FOUND
    # =====================================================

    if not symbol_list:

        return {

            "ok":
                False,

            "stage":
                "symbol_lookup",

            "message":
                "iTick tidak memulangkan symbol untuk D&O.",

            "requested_symbol":
                "D&O",

            "region":
                "MY",

            "symbol_lookup":
                [],

            "count":
                0,

            "candles":
                []

        }


    # =====================================================
    # GET ACTUAL SYMBOL INFORMATION
    # =====================================================

    symbol_info =
        symbol_list[0]


    actual_code =
        symbol_info.get("c")


    actual_name =
        symbol_info.get("n")


    actual_exchange =
        symbol_info.get("e")


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

            "ok":
                False,

            "stage":
                "symbol_lookup",

            "message":
                "iTick pulangkan symbol tetapi tiada code.",

            "symbol_lookup":
                symbol_list,

            "count":
                0,

            "candles":
                []

        }


    # =====================================================
    # STEP 2
    # SINGLE STOCK K-LINE
    # =====================================================

    kline_params = {

        "region":
            "MY",

        "exchange":
            actual_exchange or "",

        "code":
            actual_code,

        "kType":
            8,

        "limit":
            10

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

        print(
            kline_response.text[:10000]
        )

        print("==============================================")


        kline_data =
            kline_response.json()


    except requests.RequestException as e:

        raise HTTPException(

            502,

            f"Gagal menghubungi iTick K-line: {e}"

        )


    except ValueError:

        raise HTTPException(

            502,

            "iTick K-line memberi respons bukan JSON."

        )


    # =====================================================
    # CHECK HTTP
    # =====================================================

    if kline_response.status_code != 200:

        raise HTTPException(

            502,

            f"iTick K-line HTTP error: {kline_data}"

        )


    # =====================================================
    # CHECK iTick CODE
    # =====================================================

    if kline_data.get("code") != 0:

        raise HTTPException(

            502,

            f"iTick K-line error: {kline_data}"

        )


    # =====================================================
    # GET CANDLES
    # =====================================================

    raw =
        kline_data.get("data", [])


    candles = []


    if isinstance(raw, list):

        candles = raw


    print(
        "KLINE DATA TYPE:",
        type(raw).__name__
    )


    print(
        "FINAL CANDLE COUNT:",
        len(candles)
    )


    # =====================================================
    # FINAL RESPONSE
    # =====================================================

    return {

        "ok":
            bool(candles),

        "stage":
            "complete",

        "requested_symbol":
            "D&O",

        "symbol":
            actual_code,

        "name":
            actual_name,

        "region":
            "MY",

        "exchange":
            actual_exchange,

        "timeframe":
            "1D",

        "requested":
            10,

        "symbol_lookup":
            symbol_list,

        "count":
            len(candles),

        "candles":
            candles

    }