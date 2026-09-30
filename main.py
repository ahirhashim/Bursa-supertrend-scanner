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
ITICK_BATCH_KLINE_URL = "https://api-free.itick.org/stock/klines"
ITICK_REQUEST_DELAY = float(os.getenv("ITICK_REQUEST_DELAY", "20.0"))
ITICK_MAX_RETRIES = 3

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
VOLUME_BATCH_SIZE = 10  # iTick Base supports up to 10 codes/request

# ============================================================
# DATABASE
# ============================================================
DATA_DIR = os.getenv("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "recent_signals.db")
_db_lock = threading.Lock()


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
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
        conn.commit()
        conn.close()


init_database()

# ============================================================
# UNIVERSE
# ============================================================
TEST_SYMBOLS = ["D&O", "SRIDGE", "DNEX", "ZETRIX", "INARI"]

BURSA_UNIVERSE = [
    "D&O", "SRIDGE", "DNEX", "ZETRIX", "INARI", "FRONTKN", "JCY", "GREATEC",
    "NOTION", "SNS", "VSTECS", "AEMULUS", "MICROLN", "JHM", "TOPGLOV", "SUPERMX",
    "HARTA", "KOSSAN", "DXN", "ARMADA", "VELESTO", "CAPITALA", "MRCB", "BJCORP",
    "TANCO", "JAKS", "WCT", "IJM", "MAHSING", "TM", "AXIATA", "MAXIS", "DIALOG",
    "GENM", "YTLPOWR", "VS"
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
        wait_time = ITICK_REQUEST_DELAY - (now - _last_itick_request)
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


def format_timestamp(timestamp):
    try:
        dt = datetime.fromtimestamp(float(timestamp) / 1000, tz=timezone.utc)
        malaysia = dt.astimezone(timezone(timedelta(hours=8)))
        return malaysia.strftime("%Y-%m-%d")
    except Exception:
        return str(timestamp)

# ============================================================
# RECENT SIGNALS
# ============================================================
def cleanup_expired_signals():
    with _db_lock:
        conn = get_db()
        conn.execute("DELETE FROM recent_signals WHERE expires_at <= ?", (iso_now(),))
        conn.commit()
        conn.close()


def save_recent_signal(signal):
    cleanup_expired_signals()
    saved = utc_now()
    expires = saved + timedelta(days=RECENT_SIGNAL_DAYS)

    with _db_lock:
        conn = get_db()
        conn.execute("""
            INSERT INTO recent_signals
            (symbol, signal_date, close, high, low, signal_name, saved_at, expires_at)
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
            signal.get("symbol"), signal.get("date"), signal.get("close"),
            signal.get("high"), signal.get("low"), signal.get("signal_name"),
            saved.isoformat(), expires.isoformat()
        ))
        conn.commit()
        conn.close()


def get_recent_signals():
    cleanup_expired_signals()
    with _db_lock:
        conn = get_db()
        rows = conn.execute("""
            SELECT symbol, signal_date, close, high, low, signal_name, saved_at, expires_at
            FROM recent_signals ORDER BY saved_at DESC
        """).fetchall()
        conn.close()
    return [dict(row) for row in rows]


def get_recent_symbols():
    return {row["symbol"] for row in get_recent_signals()}


def remove_recent_signal(symbol):
    with _db_lock:
        conn = get_db()
        conn.execute("DELETE FROM recent_signals WHERE symbol = ?", (symbol,))
        conn.commit()
        conn.close()

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


def calculate_supertrend(candles, atr_length=10, factor=1.0):
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
        previous_close = parsed[i - 1]["c"] if i > 0 else None
        trs.append(true_range(candle["h"], candle["l"], previous_close))

    atr_values = [None] * len(parsed)
    atr_values[atr_length - 1] = sum(trs[:atr_length]) / atr_length

    for i in range(atr_length, len(trs)):
        atr_values[i] = (
            (atr_values[i - 1] * (atr_length - 1)) + trs[i]
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

        hl2 = (candle["h"] + candle["l"]) / 2.0
        basic_upper = hl2 + factor * atr
        basic_lower = hl2 - factor * atr

        if final_upper is None:
            final_upper = basic_upper
            final_lower = basic_lower
        else:
            previous_close = parsed[i - 1]["c"]
            previous_final_upper = final_upper
            previous_final_lower = final_lower

            if basic_upper < previous_final_upper or previous_close > previous_final_upper:
                final_upper = basic_upper
            else:
                final_upper = previous_final_upper

            if basic_lower > previous_final_lower or previous_close < previous_final_lower:
                final_lower = basic_lower
            else:
                final_lower = previous_final_lower

        if previous_direction is None:
            direction = 1 if candle["c"] <= final_upper else -1
        elif previous_direction == 1:
            direction = -1 if candle["c"] > final_upper else 1
        else:
            direction = 1 if candle["c"] < final_lower else -1

        supertrend = final_lower if direction < 0 else final_upper

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
        "date": format_timestamp(candle.get("t", 0)),
        "close": candle.get("c"),
        "high": candle.get("h"),
        "low": candle.get("l"),
        "atr10": candle.get("atr"),
        "supertrend": candle.get("supertrend"),
        "direction": direction,
        "trend": trend,
        "flip": candle.get("flip", False),
        "high_break": candle.get("high_break", False),
        "signal": candle.get("signal", 0),
        "signal_name": candle.get("signal_name", "NONE")
    }

# ============================================================
# SINGLE STOCK KLINE
# ============================================================
def fetch_kline(symbol, limit=50):
    if not ITICK_API_KEY:
        return {"ok": False, "symbol": symbol, "error": "ITICK_API_KEY belum diset."}

    headers = {"accept": "application/json", "token": ITICK_API_KEY}
    params = {
        "region": "MY",
        "exchange": "MYX",
        "code": symbol,
        "kType": 8,
        "limit": limit
    }

    for attempt in range(ITICK_MAX_RETRIES + 1):
        wait_before_itick_request()
        try:
            response = requests.get(
                ITICK_KLINE_URL,
                params=params,
                headers=headers,
                timeout=20
            )
        except requests.RequestException as error:
            return {"ok": False, "symbol": symbol, "error": f"K-line request error: {error}"}

        if response.status_code == 429:
            if attempt >= ITICK_MAX_RETRIES:
                return {"ok": False, "symbol": symbol, "error": "HTTP 429 selepas retry."}
            retry_after = response.headers.get("Retry-After")
            try:
                sleep_seconds = float(retry_after) if retry_after else 2.0 * (2 ** attempt)
            except Exception:
                sleep_seconds = 2.0 * (2 ** attempt)
            time.sleep(min(max(sleep_seconds, 10.0), 90.0))
            continue

        if response.status_code != 200:
            return {"ok": False, "symbol": symbol, "error": f"HTTP {response.status_code}"}

        try:
            data = response.json()
        except ValueError:
            return {"ok": False, "symbol": symbol, "error": "K-line response bukan JSON."}

        if data.get("code") != 0:
            return {"ok": False, "symbol": symbol, "error": str(data)}

        raw = data.get("data", [])
        if not isinstance(raw, list):
            raw = []
        if not raw:
            return {"ok": False, "symbol": symbol, "error": "Tiada daily candle."}

        try:
            raw = sorted(raw, key=lambda x: float(x.get("t", 0)))
        except Exception:
            pass

        return {"ok": True, "symbol": symbol, "candles": raw}

    return {"ok": False, "symbol": symbol, "error": "K-line request gagal."}


def calculate_symbol(symbol):
    fetched = fetch_kline(symbol, 50)
    if not fetched.get("ok"):
        return fetched

    calculated = calculate_supertrend(
        fetched["candles"],
        ATR_LENGTH,
        SUPERTREND_FACTOR
    )

    if not calculated:
        return {"ok": False, "symbol": symbol, "error": "Supertrend calculation gagal."}

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
    """Fetch latest daily OHLCV for up to 10 symbols in one iTick request."""
    if not ITICK_API_KEY:
        return {"ok": False, "error": "ITICK_API_KEY belum diset.", "results": []}

    symbols = [s for s in symbols if s in BURSA_UNIVERSE]
    if not symbols:
        return {"ok": True, "results": []}

    headers = {"accept": "application/json", "token": ITICK_API_KEY}
    params = {
        "region": "MY",
        "exchange": "MYX",
        "codes": ",".join(symbols),
        "kType": 8,
        "limit": 1
    }

    for attempt in range(ITICK_MAX_RETRIES + 1):
        wait_before_itick_request()
        try:
            response = requests.get(
                ITICK_BATCH_KLINE_URL,
                params=params,
                headers=headers,
                timeout=20
            )
        except requests.RequestException as error:
            return {"ok": False, "error": f"Batch volume request error: {error}", "results": []}

        if response.status_code == 429:
            if attempt >= ITICK_MAX_RETRIES:
                return {"ok": False, "error": "HTTP 429 selepas retry.", "results": []}
            retry_after = response.headers.get("Retry-After")
            try:
                sleep_seconds = float(retry_after) if retry_after else 2.0 * (2 ** attempt)
            except Exception:
                sleep_seconds = 2.0 * (2 ** attempt)
            time.sleep(min(max(sleep_seconds, 2.0), 30.0))
            continue

        if response.status_code != 200:
            return {"ok": False, "error": f"HTTP {response.status_code}", "results": []}

        try:
            data = response.json()
        except ValueError:
            return {"ok": False, "error": "Batch volume response bukan JSON.", "results": []}

        if data.get("code") != 0:
            return {"ok": False, "error": str(data), "results": []}

        payload = data.get("data", {})
        if not isinstance(payload, dict):
            return {"ok": False, "error": "Format data volume tidak dijangka.", "results": []}

    results = []

    for symbol in symbols:
        candles = payload.get(symbol, [])

        # Fallback jika batch tidak memulangkan candle
        if not candles:
            single = fetch_kline(symbol, limit=1)

            if single.get("ok") and single.get("candles"):
                candles = single["candles"]
            else:
                results.append({
                    "symbol": symbol,
                    "ok": False,
                    "error": single.get("error", "Tiada daily candle")
                })
                continue

        candle = max(candles, key=lambda x: float(x.get("t", 0)))

        try:
            volume = float(candle.get("v", 0) or 0)
        except Exception:
            volume = 0.0

        results.append({
            "symbol": symbol,
            "ok": True,
            "date": format_timestamp(candle.get("t", 0)),
            "volume": volume,
            "close": candle.get("c"),
            "high": candle.get("h"),
            "low": candle.get("l")
        })

        return {"ok": True, "results": results}

    return {"ok": False, "error": "Batch volume request gagal.", "results": []}


def build_volume_ranking():
    ranking = []
    errors = []

    for i in range(0, len(BURSA_UNIVERSE), VOLUME_BATCH_SIZE):
        batch = BURSA_UNIVERSE[i:i + VOLUME_BATCH_SIZE]
        result = fetch_daily_volume_batch(batch)

        if not result.get("ok"):
            errors.append({"symbols": batch, "error": result.get("error", "Unknown error")})
            continue

        for item in result.get("results", []):
            if item.get("ok"):
                ranking.append(item)
            else:
                errors.append({"symbol": item.get("symbol"), "error": item.get("error", "Unknown error")})

    ranking.sort(key=lambda x: x.get("volume", 0), reverse=True)
    for rank, item in enumerate(ranking, 1):
        item["rank"] = rank

    return {"ranking": ranking, "errors": errors}

# ============================================================
# SCANNER ENGINE
# ============================================================
def scan_symbols(symbols, start=0, batch_size=5):
    symbols = [s for s in symbols if s in BURSA_UNIVERSE]
    end = min(start + batch_size, len(symbols))
    batch = symbols[start:end]

    recent_symbols = get_recent_symbols()
    scanner = []
    errors = []
    skipped = []

    for symbol in batch:
        if symbol in recent_symbols:
            skipped.append({"symbol": symbol, "reason": "RECENT SIGNAL"})
            continue

        result = calculate_symbol(symbol)
        if not result.get("ok"):
            errors.append({
                "symbol": symbol,
                "error": result.get("error", "Unknown error")
            })
            continue

        latest = result.get("latest", {})
        if latest.get("signal", 0) != 0:
            signal = {"symbol": symbol, **latest}
            scanner.append(signal)
            save_recent_signal(signal)

    return {
        "start": start,
        "end": end,
        "batch_size": len(batch),
        "requested_count": len(symbols),
        "scanned_count": end,
        "remaining_count": len(symbols) - end,
        "done": end >= len(symbols),
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
    return {"ok": True, "service": "bursa-supertrend-scanner"}


@app.get("/api/recent-signals")
def recent_signals():
    signals = get_recent_signals()
    return {
        "ok": True,
        "days": RECENT_SIGNAL_DAYS,
        "count": len(signals),
        "signals": signals
    }


@app.get("/api/volume-ranking")
def volume_ranking():
    result = build_volume_ranking()
    return {
        "ok": True,
        "count": len(result["ranking"]),
        "ranking": result["ranking"],
        "error_count": len(result["errors"]),
        "errors": result["errors"]
    }


@app.get("/api/test/universe")
def test_universe(start: int = 0, batch_size: int = DEFAULT_BATCH_SIZE):
    if start < 0:
        start = 0
    batch_size = min(max(batch_size, 1), 10)
    return scan_symbols(BURSA_UNIVERSE, start, batch_size)


@app.get("/api/test/volume-scan")
def volume_scan(start: int = 0, batch_size: int = DEFAULT_BATCH_SIZE, symbols: str = ""):
    ordered = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    ordered = [s for s in ordered if s in BURSA_UNIVERSE]   
    ordered += [s for s in BURSA_UNIVERSE if s not in ordered]
    
    if not ordered:
        return {"ok": False, "error": "Volume ranking belum dihantar.", "scanner": []}

    batch_size = min(max(batch_size, 1), 10)
    return scan_symbols(ordered, start, batch_size)


@app.get("/api/rescan-saved")
def rescan_saved():
    saved = get_recent_signals()
    results = []
    errors = []

    for item in saved:
        symbol = item["symbol"]
        result = calculate_symbol(symbol)

        if not result.get("ok"):
            errors.append({
                "symbol": symbol,
                "error": result.get("error", "Unknown error")
            })
            continue

        latest = result.get("latest", {})
        if latest.get("signal", 0) != 0:
            signal = {"symbol": symbol, **latest}
            save_recent_signal(signal)
            results.append({
                "symbol": symbol,
                "status": "SIGNAL STILL ACTIVE",
                "signal": signal
            })
        else:
            remove_recent_signal(symbol)
            results.append({
                "symbol": symbol,
                "status": "SIGNAL CLEARED"
            })

    return {
        "ok": True,
        "rescanned_count": len(saved),
        "results": results,
        "error_count": len(errors),
        "errors": errors,
        "recent_signals": get_recent_signals()
    }


@app.get("/api/test/5")
def test_five():
    return {
        "requested_count": len(TEST_SYMBOLS),
        "results": [calculate_symbol(s) for s in TEST_SYMBOLS]
    }


@app.get("/api/test/history")
def test_history():
    results = []
    for symbol in TEST_SYMBOLS:
        result = calculate_symbol(symbol)
        if result.get("ok"):
            results.append({
                "symbol": symbol,
                "historical_signals": result.get("historical_signals", [])
            })
        else:
            results.append(result)
    return {"results": results}

# ============================================================
# HOME PAGE
# ============================================================
@app.get("/", response_class=HTMLResponse)
def home():
    return r'''
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bursa Supertrend Scanner</title>
<style>
body{margin:0;padding:20px;background:#101010;color:#eeeeee;font-family:Arial,sans-serif}
.container{max-width:900px;margin:auto}
h1{margin-bottom:5px;font-size:24px}
.subtitle{color:#999999;margin-bottom:20px}
button{width:100%;padding:14px;margin-top:10px;border:none;border-radius:8px;background:#1f8f4d;color:white;font-size:16px;font-weight:bold}
button:disabled{opacity:.5}
.secondary{background:#333333}
.danger{background:#7b3030}
.volume{background:#1769aa}
input{width:100%;box-sizing:border-box;padding:12px;margin-top:8px;margin-bottom:8px;border-radius:8px;border:1px solid #444;background:#181818;color:white;font-size:16px}
label{display:block;margin-top:15px;color:#cccccc}
pre{margin-top:20px;padding:15px;background:#181818;border-radius:8px;overflow-x:auto;white-space:pre-wrap;word-break:break-word;font-size:13px}
.card{margin-top:20px;padding:15px;background:#181818;border-radius:10px}
.card-title{font-size:18px;font-weight:bold;margin-bottom:10px}
.info{margin-top:15px;padding:12px;background:#181818;border-radius:8px;color:#bbbbbb;font-size:13px}
.signal{padding:12px;margin-top:8px;border-radius:8px;background:#222222;border-left:4px solid #1f8f4d}
.signal-title{font-size:16px;font-weight:bold}
.signal-detail{color:#bbbbbb;margin-top:5px;font-size:13px}
.rank{padding:9px;margin-top:5px;background:#222222;border-radius:7px;font-size:13px}
.empty{color:#888888;font-size:14px}
</style>
</head>
<body>
<div class="container">
<h1>BURSA SUPERTREND SCANNER</h1>
<div class="subtitle">Bursa Universe • Daily • ATR 10 / Factor 1.0</div>

<div class="card">
<div class="card-title">RECENT SIGNALS</div>
<div id="recentSignals">Loading...</div>
<button class="secondary" onclick="loadRecentSignals()">REFRESH RECENT SIGNALS</button>
<button class="danger" onclick="rescanSaved()">RESCAN SAVED</button>
</div>

<div class="card">
<div class="card-title">TOP DAILY VOLUME</div>
<button class="volume" id="volumeButton" onclick="startVolumeScan()">TOP DAILY VOLUME → SCAN 5</button>
<div id="volumeStatus" class="info">Belum buat ranking volume.</div>
<div id="volumeList"></div>
</div>

<div class="card">
<div class="card-title">BURSA UNIVERSE SCANNER</div>
<label>MAX SIGNALS</label>
<input id="maxSignals" type="number" min="1" max="20" value="5">
<button id="scanButton" onclick="scanFirstBatch()">SCAN 5</button>
<button id="nextButton" onclick="scanNextBatch()" disabled>SCAN NEXT 5</button>
<button class="secondary" onclick="testHistory()">TEST HISTORICAL SIGNAL</button>
</div>

<div class="info">
<b>TOP DAILY VOLUME</b> akan susun 36 kaunter berdasarkan volume daily terkini, kemudian scan 5 kaunter tertinggi setiap batch.<br><br>
Kaunter dalam <b>RECENT SIGNALS</b> akan di-skip secara automatik.<br><br>
<b>SCAN 5</b> biasa masih boleh digunakan untuk scan mengikut susunan Bursa Universe.
</div>

<pre id="out">Ready.</pre>
</div>

<script>
let nextStart=0,totalSignals=0,allSignals=[],allErrors=[],scanDone=false;
let volumeOrder=[],volumeMode=false;

function price(v){return v===null||v===undefined?"-":Number(v).toFixed(3)}
function fmtExpiry(v){try{return new Date(v).toLocaleString("ms-MY",{dateStyle:"medium",timeStyle:"short"})}catch(e){return v||"-"}}

async function loadRecentSignals(){
 const box=document.getElementById("recentSignals");
 box.textContent="Loading...";
 try{
  const r=await fetch("/api/recent-signals");
  if(!r.ok)throw new Error("HTTP "+r.status);
  const d=await r.json();
  if(!d.signals.length){box.innerHTML='<div class="empty">Tiada Recent Signal.</div>';return}
  box.innerHTML=d.signals.map(s=>
   '<div class="signal"><div class="signal-title">'+s.symbol+' | RM '+price(s.close)+'</div>'+
   '<div class="signal-detail">'+s.date+' | '+s.signal_name+'</div>'+
   '<div class="signal-detail">Expired: '+fmtExpiry(s.expires_at)+'</div></div>'
  ).join("");
 }catch(e){box.textContent="ERROR\n\n"+e}
}

function resetScan(){nextStart=0;totalSignals=0;allSignals=[];allErrors=[];scanDone=false}

async function startVolumeScan(){
 resetScan();volumeMode=true;
 const out=document.getElementById("out");
 const vb=document.getElementById("volumeButton");
 vb.disabled=true;
 out.textContent="MEMBINA TOP DAILY VOLUME...\n\n";
 try{
  const r=await fetch("/api/volume-ranking");
  if(!r.ok)throw new Error("HTTP "+r.status);
  const d=await r.json();
  volumeOrder=d.ranking.map(x=>x.symbol);
  document.getElementById("volumeStatus").textContent="Ranking siap: "+volumeOrder.length+" / 36 kaunter.";
  document.getElementById("volumeList").innerHTML=d.ranking.slice(0,10).map(x=>
   '<div class="rank">#'+x.rank+' '+x.symbol+' | Volume '+Number(x.volume).toLocaleString()+ ' | '+x.date+'</div>'
  ).join("");
  if(d.errors.length){out.textContent+="Volume error: "+JSON.stringify(d.errors,null,2)+"\n\n"}
  if(!volumeOrder.length){out.textContent+="Tiada ranking volume.\n";return}
  await scanOneBatch();
 }catch(e){out.textContent+="VOLUME SCAN ERROR\n\n"+e}
 vb.disabled=false;
}

async function scanFirstBatch(){
 resetScan();volumeMode=false;
 document.getElementById("out").textContent="MULA SCAN 5...\n\n";
 await scanOneBatch();
}

async function scanNextBatch(){
 const max=parseInt(document.getElementById("maxSignals").value)||5;
 if(scanDone||totalSignals>=max)return;
 await scanOneBatch();
}

async function scanOneBatch(){
 const out=document.getElementById("out");
 const scanButton=document.getElementById("scanButton");
 const nextButton=document.getElementById("nextButton");
 const volumeButton=document.getElementById("volumeButton");
 const max=parseInt(document.getElementById("maxSignals").value)||5;
 scanButton.disabled=true;nextButton.disabled=true;volumeButton.disabled=true;

 const endpoint=volumeMode?"/api/test/volume-scan":"/api/test/universe";
 const query=volumeMode
  ? endpoint+"?start="+nextStart+"&batch_size=5&symbols="+encodeURIComponent(volumeOrder.join(","))
  : endpoint+"?start="+nextStart+"&batch_size=5";

 out.textContent+="--------------------------------\n"+(volumeMode?"TOP VOLUME SCAN BATCH\n":"SCAN BATCH\n")+"Kaunter: "+nextStart+" → "+(nextStart+5)+"\n\n";
 try{
  const r=await fetch(query);
  if(!r.ok)throw new Error("HTTP "+r.status);
  const d=await r.json();
  if(d.ok===false)throw new Error(d.error||"Scanner error");

  if(d.scanner&&d.scanner.length){
   for(const s of d.scanner){
    if(totalSignals>=max)break;
    totalSignals++;allSignals.push(s);
    out.textContent+="SIGNAL #"+totalSignals+"\n"+s.symbol+" | "+s.date+" | RM "+price(s.close)+" | "+s.signal_name+"\n\n";
   }
  }else out.textContent+="Tiada signal baru dalam batch ini.\n\n";

  if(d.skipped&&d.skipped.length){
   out.textContent+="SKIP RECENT SIGNAL: "+d.skipped.length+"\n";
   d.skipped.forEach(x=>out.textContent+=x.symbol+" -> RECENT SIGNAL\n");
   out.textContent+="\n";
  }

  if(d.errors&&d.errors.length){d.errors.forEach(x=>allErrors.push(x));out.textContent+="Error batch: "+d.errors.length+"\n\n"}

  out.textContent+="--------------------------------\nProgress: "+d.scanned_count+" / "+d.requested_count+"\nSignal: "+totalSignals+" / "+max+"\n";
  nextStart=d.end;

  if(totalSignals>=max){scanDone=true;out.textContent+="\nMAX SIGNALS "+max+" DICAPAI.\nSCAN DIHENTIKAN.\n"}
  else if(d.done){scanDone=true;out.textContent+="\nSEMUA SUSUNAN SELESAI.\n"}
  else {out.textContent+="\nBATCH INI SELESAI.\nTeruskan scan seterusnya...\n";await scanOneBatch();return;} oh

  out.textContent+="\n================================\nSIGNAL DIJUMPAI SETAKAT INI\n================================\n";
  if(allSignals.length)allSignals.forEach((s,i)=>out.textContent+=(i+1)+". "+s.symbol+" | "+s.date+" | RM "+price(s.close)+" | "+s.signal_name+"\n");
  else out.textContent+="Tiada signal ditemui.\n";

  if(allErrors.length){out.textContent+="\nERROR / 429:\n";allErrors.forEach(x=>out.textContent+=x.symbol+" -> "+x.error+"\n")}
  await loadRecentSignals();
 }catch(e){out.textContent+="\nSCAN ERROR\n\n"+e}
 

 scanButton.disabled=false;volumeButton.disabled=false;nextButton.disabled=scanDone||totalSignals>=max;
}

async function rescanSaved(){
 const out=document.getElementById("out");out.textContent="RESCAN SAVED...\n\nSila tunggu...\n";
 try{
  const r=await fetch("/api/rescan-saved");if(!r.ok)throw new Error("HTTP "+r.status);
  const d=await r.json();out.textContent+="Jumlah saved sebelum rescan: "+d.rescanned_count+"\n\n";
  d.results.forEach(x=>{out.textContent+=x.symbol+" -> "+x.status+"\n";if(x.signal)out.textContent+="   "+x.signal.signal_name+" | RM "+price(x.signal.close)+" | "+x.signal.date+"\n"});
  await loadRecentSignals();
 }catch(e){out.textContent="RESCAN SAVED ERROR\n\n"+e}
}

async function testHistory(){
 const out=document.getElementById("out");out.textContent="Testing historical signals...";
 try{const r=await fetch("/api/test/history");out.textContent=JSON.stringify(await r.json(),null,2)}catch(e){out.textContent="HISTORY ERROR\n\n"+e}
}

loadRecentSignals();
</script>
</body>
</html>
'''
