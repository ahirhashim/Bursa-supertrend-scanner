import os
import json
import requests

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

app = FastAPI(title="Bursa Supertrend Scanner")

ITICK_API_KEY = os.getenv("ITICK_API_KEY", "")
ITICK_URL = "https://api-free.itick.org/stock/klines"


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <!doctype html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width,initial-scale=1">
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

            <pre id="out">Belum diuji.</pre>

        </div>

        <script>
        async function testDO(){

            const out = document.getElementById('out');

            out.textContent = 'Sedang menghubungi iTick...';

            try{

                const r = await fetch('/api/test/do');

                const d = await r.json();

                out.textContent = JSON.stringify(d,null,2);

            }catch(e){

                out.textContent = 'Ralat sambungan: ' + e;

            }
        }
        </script>

    </body>
    </html>
    """


@app.get("/api/health")
def health():

    return {
        "ok": True,
        "service": "bursa-supertrend-scanner"
    }


@app.get("/api/test/do")
def test_do():

    if not ITICK_API_KEY:

        raise HTTPException(
            500,
            "ITICK_API_KEY belum diset dalam Render Environment."
        )

    params = {
        "region": "MY",
        "exchange": "MYX",
        "codes": "D&O",
        "kType": 8,
        "limit": 100
    }

    try:

        r = requests.get(
            ITICK_URL,
            params=params,
            headers={
                "accept": "application/json",
                "token": ITICK_API_KEY
            },
            timeout=20
        )

        print("==============================================")
        print("iTick TEST D&O")
        print("URL:", r.url)
        print("HTTP STATUS:", r.status_code)
        print("RAW RESPONSE:")
        print(r.text[:10000])
        print("==============================================")

        data = r.json()

    except requests.RequestException as e:

        print("iTick REQUEST ERROR:", str(e))

        raise HTTPException(
            502,
            f"Gagal menghubungi iTick: {e}"
        )

    except ValueError:

        print("iTick NON-JSON RESPONSE:")
        print(r.text[:10000])

        raise HTTPException(
            502,
            f"iTick memberi respons bukan JSON. HTTP {r.status_code}"
        )

    if r.status_code != 200:

        raise HTTPException(
            502,
            f"iTick HTTP error: {data}"
        )

    if data.get("code") != 0:

        raise HTTPException(
            502,
            f"iTick error: {data.get('msg', data)}"
        )

    raw = data.get("data")

    print("iTick DATA TYPE:", type(raw).__name__)

    candles = []

    raw_keys = []

    if isinstance(raw, dict):

        raw_keys = list(raw.keys())

        print("iTick DATA KEYS:", raw_keys)

        # ------------------------------------------
        # Cuba key asal
        # ------------------------------------------

        possible_keys = [
            "D&O",
            "D&O@MYX",
            "MYX:D&O",
            "D&O.MYX",
            "D&O:MYX"
        ]

        for key in possible_keys:

            value = raw.get(key)

            if isinstance(value, list):

                candles = value

                print("FOUND CANDLES USING KEY:", key)

                break

        # ------------------------------------------
        # Jika hanya ada satu simbol dalam response,
        # gunakan array tersebut.
        # ------------------------------------------

        if not candles:

            list_values = [
                value
                for value in raw.values()
                if isinstance(value, list)
            ]

            if len(list_values) == 1:

                candles = list_values[0]

                print(
                    "FOUND CANDLES USING SINGLE-LIST FALLBACK"
                )

    elif isinstance(raw, list):

        candles = raw

        print(
            "FOUND CANDLES: DATA ITICK IS DIRECT LIST"
        )

    print("FINAL CANDLE COUNT:", len(candles))
    print("==============================================")

    return {
        "ok": bool(candles),
        "symbol": "D&O",
        "region": "MY",
        "exchange": "MYX",
        "timeframe": "1D",
        "requested": 100,
        "count": len(candles),
        "raw_data_type": type(raw).__name__,
        "raw_data_keys": raw_keys,
        "candles": candles
    }