import os
import time
import threading
import sqlite3
import json
import uuid
from datetime import datetime, timezone, timedelta

import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="Bursa Supertrend Scanner")

# ============================================================
# ITICK - JANGAN UBAH LOGIK INI
# ============================================================
ITICK_API_KEY = os.getenv("ITICK_API_KEY", "")
ITICK_KLINE_URL = "https://api-free.itick.org/stock/kline"
ITICK_BATCH_KLINE_URL = "https://api-free.itick.org/stock/klines"
ITICK_REQUEST_DELAY = 20.0
ITICK_MAX_RETRIES = 0
ITICK_CONNECT_TIMEOUT = 5
ITICK_READ_TIMEOUT = 15

# ============================================================
# SUPERTREND - LOCKED
# ============================================================
ATR_LENGTH = 10
SUPERTREND_FACTOR = 1.0

# ============================================================
# SCANNER
# ============================================================
DEFAULT_BATCH_SIZE = 3
RECENT_SIGNAL_DAYS = 5
VOLUME_BATCH_SIZE = 10

# ============================================================
# DATABASE
# ============================================================
DATA_DIR = os.getenv("DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "recent_signals.db")
_db_lock = threading.Lock()
_job_thread_lock = threading.Lock()
_job_threads = {}


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS volume_snapshot (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                updated_at TEXT NOT NULL,
                ranking_json TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                current_index INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                max_signals INTEGER NOT NULL DEFAULT 5,
                results_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT,
                updated_at TEXT NOT NULL,
                finished_at TEXT,
                message TEXT
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
    "D&O", "SRIDGE", "DNEX", "ZETRIX", "INARI", "FRONTKN", "JCY",
    "GREATEC", "NOTION", "SNS", "VSTECS", "AEMULUS", "MICROLN", "JHM",
    "TOPGLOV", "SUPERMX", "HARTA", "KOSSAN", "DXN", "ARMADA", "VELESTO",
    "CAPITALA", "MRCB", "BJCORP", "TANCO", "JAKS", "WCT", "IJM", "MAHSING",
    "TM", "AXIATA", "MAXIS", "DIALOG", "GENM", "YTLPOWR", "VS"
]

# ============================================================
# TIME / PACING
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


def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def malaysia_now_string():
    malaysia = timezone(timedelta(hours=8))
    return datetime.now(malaysia).strftime("%Y-%m-%d %H:%M:%S")


def format_timestamp(timestamp):
    try:
        dt = datetime.fromtimestamp(float(timestamp) / 1000, tz=timezone.utc)
        return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
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
# VOLUME SNAPSHOT
# ============================================================
def save_volume_snapshot(ranking):
    updated_at = malaysia_now_string()
    with _db_lock:
        conn = get_db()
        conn.execute("""
            INSERT INTO volume_snapshot (id, updated_at, ranking_json)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                updated_at=excluded.updated_at,
                ranking_json=excluded.ranking_json
        """, (updated_at, json.dumps(ranking)))
        conn.commit()
        conn.close()
    return updated_at


def get_volume_snapshot():
    with _db_lock:
        conn = get_db()
        row = conn.execute("SELECT updated_at, ranking_json FROM volume_snapshot WHERE id=1").fetchone()
        conn.close()
    if not row:
        return {"ok": False, "updated_at": None, "ranking": []}
    try:
        ranking = json.loads(row["ranking_json"])
    except Exception:
        ranking = []
    return {"ok": True, "updated_at": row["updated_at"], "ranking": ranking}

# ============================================================
# JOB STORAGE
# ============================================================
def _job_default_results():
    return {"signals": [], "errors": [], "skipped": [], "volume_results": []}


def create_job(job_type, total, max_signals=5):
    job_id = uuid.uuid4().hex[:12]
    now = iso_now()
    results = _job_default_results()
    with _db_lock:
        conn = get_db()
        conn.execute("""
            INSERT INTO jobs
            (job_id, job_type, status, current_index, total, max_signals,
             results_json, started_at, updated_at, message)
            VALUES (?, ?, 'running', 0, ?, ?, ?, ?, ?, ?)
        """, (job_id, job_type, total, max_signals, json.dumps(results), now, now, "Job dimulakan."))
        conn.commit()
        conn.close()
    return job_id


def get_job(job_id):
    with _db_lock:
        conn = get_db()
        row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        conn.close()
    if not row:
        return None
    item = dict(row)
    try:
        item["results"] = json.loads(item.pop("results_json"))
    except Exception:
        item["results"] = _job_default_results()
    return item


def update_job(job_id, **fields):
    if not fields:
        return
    fields["updated_at"] = iso_now()
    if "results" in fields:
        fields["results_json"] = json.dumps(fields.pop("results"))
    assignments = ", ".join(f"{key}=?" for key in fields)
    values = list(fields.values()) + [job_id]
    with _db_lock:
        conn = get_db()
        conn.execute(f"UPDATE jobs SET {assignments} WHERE job_id=?", values)
        conn.commit()
        conn.close()


def active_job():
    with _db_lock:
        conn = get_db()
        row = conn.execute("""
            SELECT job_id FROM jobs
            WHERE status='running'
            ORDER BY started_at DESC LIMIT 1
        """).fetchone()
        conn.close()
    return get_job(row["job_id"]) if row else None


def latest_resumable_job():
    with _db_lock:
        conn = get_db()
        row = conn.execute("""
            SELECT job_id FROM jobs
            WHERE status IN ('interrupted', 'failed')
            ORDER BY updated_at DESC LIMIT 1
        """).fetchone()
        conn.close()
    return get_job(row["job_id"]) if row else None


def mark_old_running_jobs_interrupted():
    with _db_lock:
        conn = get_db()
        conn.execute("""
            UPDATE jobs SET status='interrupted', message='Service restarted. Job boleh disambung.', updated_at=?
            WHERE status='running'
        """, (iso_now(),))
        conn.commit()
        conn.close()


mark_old_running_jobs_interrupted()


def start_thread(job_id, target):
    with _job_thread_lock:
        existing = _job_threads.get(job_id)
        if existing and existing.is_alive():
            return False
        thread = threading.Thread(target=target, args=(job_id,), daemon=True)
        _job_threads[job_id] = thread
        thread.start()
        return True

# ============================================================
# SUPERTREND ENGINE
# ============================================================
def true_range(high, low, previous_close):
    if previous_close is None:
        return high - low
    return max(high-low, abs(high-previous_close), abs(low-previous_close))


def calculate_supertrend(candles, atr_length=10, factor=1.0):
    if not candles:
        return []
    parsed = []
    for candle in candles:
        try:
            parsed.append({
                "t": float(candle.get("t", 0)), "o": float(candle.get("o", 0)),
                "h": float(candle.get("h", 0)), "l": float(candle.get("l", 0)),
                "c": float(candle.get("c", 0)), "v": candle.get("v", 0)
            })
        except Exception:
            continue
    if len(parsed) < atr_length:
        return []
    trs = []
    for i, candle in enumerate(parsed):
        previous_close = parsed[i-1]["c"] if i > 0 else None
        trs.append(true_range(candle["h"], candle["l"], previous_close))
    atr_values = [None] * len(parsed)
    atr_values[atr_length-1] = sum(trs[:atr_length]) / atr_length
    for i in range(atr_length, len(trs)):
        atr_values[i] = ((atr_values[i-1] * (atr_length-1)) + trs[i]) / atr_length
    result = []
    final_upper = None
    final_lower = None
    previous_direction = None
    for i, candle in enumerate(parsed):
        atr = atr_values[i]
        if atr is None:
            result.append({**candle, "atr": None, "supertrend": None, "direction": None,
                           "flip": False, "high_break": False, "signal": 0, "signal_name": "NONE"})
            continue
        hl2 = (candle["h"] + candle["l"]) / 2.0
        basic_upper = hl2 + factor * atr
        basic_lower = hl2 - factor * atr
        if final_upper is None:
            final_upper, final_lower = basic_upper, basic_lower
        else:
            previous_close = parsed[i-1]["c"]
            previous_final_upper, previous_final_lower = final_upper, final_lower
            final_upper = basic_upper if (basic_upper < previous_final_upper or previous_close > previous_final_upper) else previous_final_upper
            final_lower = basic_lower if (basic_lower > previous_final_lower or previous_close < previous_final_lower) else previous_final_lower
        if previous_direction is None:
            direction = 1 if candle["c"] <= final_upper else -1
        elif previous_direction == 1:
            direction = -1 if candle["c"] > final_upper else 1
        else:
            direction = 1 if candle["c"] < final_lower else -1
        supertrend = final_lower if direction < 0 else final_upper
        flip = previous_direction is not None and previous_direction > 0 and direction < 0
        high_break = i > 0 and direction < 0 and candle["c"] > parsed[i-1]["h"]
        if flip and high_break:
            signal, signal_name = 3, "FLIP + HIGH BREAK"
        elif flip:
            signal, signal_name = 1, "FLIP"
        elif high_break:
            signal, signal_name = 2, "HIGH BREAK"
        else:
            signal, signal_name = 0, "NONE"
        result.append({**candle, "atr": atr, "supertrend": supertrend, "direction": direction,
                       "flip": flip, "high_break": high_break, "signal": signal, "signal_name": signal_name})
        previous_direction = direction
    return result


def format_result(candle):
    direction = candle.get("direction")
    trend = "BULL" if direction == -1 else "BEAR" if direction == 1 else "NA"
    return {
        "date": format_timestamp(candle.get("t", 0)),
        "close": candle.get("c"), "high": candle.get("h"), "low": candle.get("l"),
        "atr10": candle.get("atr"), "supertrend": candle.get("supertrend"),
        "direction": direction, "trend": trend, "flip": candle.get("flip", False),
        "high_break": candle.get("high_break", False), "signal": candle.get("signal", 0),
        "signal_name": candle.get("signal_name", "NONE")
    }

# ============================================================
# ITICK SINGLE KLINE
# ============================================================
def fetch_kline(symbol, limit=50):
    if not ITICK_API_KEY:
        return {"ok": False, "symbol": symbol, "error": "ITICK_API_KEY belum diset."}
    headers = {"accept": "application/json", "token": ITICK_API_KEY}
    params = {"region": "MY", "exchange": "MYX", "code": symbol, "kType": 8, "limit": limit}
    wait_before_itick_request()
    try:
        response = requests.get(ITICK_KLINE_URL, params=params, headers=headers, timeout=(ITICK_CONNECT_TIMEOUT, ITICK_READ_TIMEOUT))
    except requests.RequestException as error:
        return {"ok": False, "symbol": symbol, "error": f"K-line request error: {error}"}
    if response.status_code == 429:
        return {"ok": False, "symbol": symbol, "error": "HTTP 429 - iTick rate limit."}
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


def calculate_symbol(symbol):
    fetched = fetch_kline(symbol, 50)
    if not fetched.get("ok"):
        return fetched
    calculated = calculate_supertrend(fetched["candles"], ATR_LENGTH, SUPERTREND_FACTOR)
    if not calculated:
        return {"ok": False, "symbol": symbol, "error": "Supertrend calculation gagal."}
    latest = calculated[-1]
    return {
        "ok": True, "symbol": symbol, "latest": format_result(latest),
        "historical_signals": [format_result(c) for c in calculated if c.get("signal", 0) != 0]
    }

# ============================================================
# DAILY VOLUME
# ============================================================
def fetch_daily_volume_batch(symbols):
    if not ITICK_API_KEY:
        return {"ok": False, "error": "ITICK_API_KEY belum diset.", "results": []}
    results = []
    for symbol in symbols:
        print("VOLUME SINGLE KLINE:", symbol, flush=True)
        fetched = fetch_kline(symbol, limit=1)
        if not fetched.get("ok"):
            results.append({"symbol": symbol, "ok": False, "error": fetched.get("error", "Gagal mendapatkan daily candle.")})
            continue
        candles = fetched.get("candles", [])
        if not candles:
            results.append({"symbol": symbol, "ok": False, "error": "Tiada daily candle."})
            continue
        try:
            latest = max(candles, key=lambda x: float(x.get("t", 0) or 0))
            volume = float(latest.get("v", 0) or 0)
        except Exception as error:
            results.append({"symbol": symbol, "ok": False, "error": str(error)})
            continue
        results.append({"symbol": symbol, "ok": True, "volume": volume})
    return {"ok": True, "results": results}

# ============================================================
# BACKGROUND VOLUME JOB
# ============================================================
def run_volume_job(job_id):
    try:
        job = get_job(job_id)
        if not job:
            return
        results = job["results"]
        volume_results = results.get("volume_results", [])
        errors = results.get("errors", [])
        universe = BURSA_UNIVERSE
        start = job["current_index"] 
        total = len(universe)
        update_job(job_id, status="running", total=total, message="Mengambil Daily Volume...", results=results)
        while start < total:
            symbol = universe[start]
    try:
        result = fetch_daily_volume_batch([symbol])
        item = result.get("results", [{}])[0] if 
    result.get("results") else {"symbol": symbol, "ok": False, 
    "error": result.get("error", "Unknown error")}
    except Exception as error:
        item = {"symbol": symbol, "ok": False, "error": f"Volume 
    request exception: {error}"}
            
            if item.get("ok"):
                volume_results.append(item)
            else:
                errors.append(item)
            start += 1
            results["volume_results"] = volume_results
            results["errors"] = errors
            update_job(job_id, current_index=start, total=total,
                       message=f"Volume {start}/{total}: {symbol}", results=results)
        ranking = sorted(volume_results, key=lambda x: float(x.get("volume", 0)), reverse=True)
        for index, item in enumerate(ranking, start=1):
            item["rank"] = index
        updated_at = save_volume_snapshot(ranking) if ranking else None
        results["ranking"] = ranking
        results["updated_at"] = updated_at
        update_job(job_id, status="completed" if ranking else "failed", current_index=total,
                   message=f"Selesai: {len(ranking)}/{total} kaunter.", results=results, finished_at=iso_now())
    except Exception as error:
        print("VOLUME JOB ERROR:", repr(error), flush=True)
        job = get_job(job_id)
        results = job["results"] if job else _job_default_results()
        update_job(job_id, status="failed", message=f"Volume job error: {error}", results=results, finished_at=iso_now())

# ============================================================
# BACKGROUND SCAN JOBS
# ============================================================
def run_scan_job(job_id):
    try:
        job = get_job(job_id)
        if not job:
            return
        results = job["results"]
        signals = results.get("signals", [])
        errors = results.get("errors", [])
        skipped = results.get("skipped", [])
        job_type = job["job_type"]
        if job_type == "saved_volume_scan":
            snapshot = get_volume_snapshot()
            symbols = [x.get("symbol") for x in snapshot["ranking"] if x.get("symbol")]
        else:
            symbols = BURSA_UNIVERSE
        total = len(symbols)
        start = job["current_index"]
        max_signals = max(1, job["max_signals"])
        update_job(job_id, total=total, status="running", message="Scan sedang berjalan...", results=results)
        while start < total and len(signals) < max_signals:
            symbol = symbols[start]
            recent_symbols = get_recent_symbols()
            if symbol in recent_symbols:
                skipped.append({"symbol": symbol, "reason": "RECENT SIGNAL"})
            else:
                result = calculate_symbol(symbol)
                if not result.get("ok"):
                    errors.append({"symbol": symbol, "error": result.get("error", "Unknown error")})
                else:
                    latest = result.get("latest", {})
                    if latest.get("signal", 0) != 0:
                        signal = {"symbol": symbol, **latest}
                        signals.append(signal)
                        save_recent_signal(signal)
            start += 1
            results.update({"signals": signals, "errors": errors, "skipped": skipped})
            update_job(job_id, current_index=start, total=total, max_signals=max_signals,
                       message=f"Scan {start}/{total}: {symbol} | Signal {len(signals)}/{max_signals}", results=results)
        done = start >= total or len(signals) >= max_signals
        status = "completed" if done else "running"
        update_job(job_id, status=status, current_index=start, total=total,
                   message=f"Selesai: {len(signals)} signal, {start}/{total} kaunter.",
                   results=results, finished_at=iso_now() if done else None)
    except Exception as error:
        print("SCAN JOB ERROR:", repr(error), flush=True)
        job = get_job(job_id)
        results = job["results"] if job else _job_default_results()
        update_job(job_id, status="failed", message=f"Scan job error: {error}", results=results, finished_at=iso_now())

# ============================================================
# RESCAN JOB
# ============================================================
def run_rescan_job(job_id):
    try:
        job = get_job(job_id)
        saved = get_recent_signals()
        results = job["results"]
        output = results.get("rescan", [])
        errors = results.get("errors", [])
        start = job["current_index"]
        total = len(saved)
        while start < total:
            item = saved[start]
            symbol = item["symbol"]
            result = calculate_symbol(symbol)
            if not result.get("ok"):
                errors.append({"symbol": symbol, "error": result.get("error", "Unknown error")})
            else:
                latest = result.get("latest", {})
                if latest.get("signal", 0) != 0:
                    signal = {"symbol": symbol, **latest}
                    save_recent_signal(signal)
                    output.append({"symbol": symbol, "status": "SIGNAL STILL ACTIVE", "signal": signal})
                else:
                    remove_recent_signal(symbol)
                    output.append({"symbol": symbol, "status": "SIGNAL CLEARED"})
            start += 1
            results.update({"rescan": output, "errors": errors})
            update_job(job_id, current_index=start, total=total,
                       message=f"Rescan {start}/{total}: {symbol}", results=results)
        update_job(job_id, status="completed", current_index=total, total=total,
                   message=f"Rescan selesai: {total} saved signal.", results=results, finished_at=iso_now())
    except Exception as error:
        print("RESCAN JOB ERROR:", repr(error), flush=True)
        job = get_job(job_id)
        results = job["results"] if job else _job_default_results()
        update_job(job_id, status="failed", message=f"Rescan job error: {error}", results=results, finished_at=iso_now())

# ============================================================
# JOB START / RESUME HELPERS
# ============================================================
def start_or_resume_job(job_type, total, max_signals=5, resume_job_id=None):
    if resume_job_id:
        job = get_job(resume_job_id)
        if not job or job["status"] not in ("interrupted", "failed"):
            return None, "Job tidak boleh disambung."
        update_job(resume_job_id, status="running", message="Job disambung...")
        job_id = resume_job_id
    else:
        existing = active_job()
        if existing:
            return existing["job_id"], "Job sedang berjalan."
        job_id = create_job(job_type, total, max_signals)
    if job_type == "volume_update":
        start_thread(job_id, run_volume_job)
    elif job_type in ("saved_volume_scan", "universe_scan"):
        start_thread(job_id, run_scan_job)
    elif job_type == "rescan_saved":
        start_thread(job_id, run_rescan_job)
    return job_id, None

# ============================================================
# API
# ============================================================
@app.get("/api/health")
def health():
    return {"ok": True, "service": "bursa-supertrend-scanner"}


@app.get("/api/recent-signals")
def recent_signals():
    signals = get_recent_signals()
    return {"ok": True, "days": RECENT_SIGNAL_DAYS, "count": len(signals), "signals": signals}


@app.get("/api/volume-ranking")
def volume_ranking():
    snapshot = get_volume_snapshot()
    return {"ok": snapshot["ok"], "count": len(snapshot["ranking"]),
            "updated_at": snapshot["updated_at"], "ranking": snapshot["ranking"], "errors": []}


@app.get("/api/job-status")
def job_status(job_id: str = ""):
    job = get_job(job_id) if job_id else active_job()
    if not job:
        job = latest_resumable_job()
    return {"ok": bool(job), "job": job}


@app.get("/api/jobs")
def jobs():
    with _db_lock:
        conn = get_db()
        rows = conn.execute("SELECT * FROM jobs ORDER BY updated_at DESC LIMIT 10").fetchall()
        conn.close()
    output = []
    for row in rows:
        item = dict(row)
        try:
            item["results"] = json.loads(item.pop("results_json"))
        except Exception:
            item["results"] = _job_default_results()
        output.append(item)
    return {"ok": True, "jobs": output}


@app.get("/api/update-volume")
def update_volume():
    job_id, note = start_or_resume_job("volume_update", len(BURSA_UNIVERSE), 0)
    return {"ok": True, "started": True, "job_id": job_id, "message": note or "Update Daily Volume dimulakan."}


@app.get("/api/update-volume/resume")
def resume_volume(job_id: str):
    job_id, note = start_or_resume_job("volume_update", len(BURSA_UNIVERSE), 0, job_id)
    if not job_id:
        return {"ok": False, "error": note}
    return {"ok": True, "job_id": job_id, "message": note or "Volume job disambung."}


@app.get("/api/test/universe/start")
def start_universe_scan(max_signals: int = 5):
    max_signals = min(max(max_signals, 1), 20)
    job_id, note = start_or_resume_job("universe_scan", len(BURSA_UNIVERSE), max_signals)
    return {"ok": True, "job_id": job_id, "message": note or "Universe scan dimulakan."}


@app.get("/api/test/volume-scan/start")
def start_volume_scan(max_signals: int = 5):
    snapshot = get_volume_snapshot()
    if not snapshot["ranking"]:
        return {"ok": False, "error": "Belum ada Daily Volume snapshot. Tekan UPDATE DAILY VOLUME dahulu."}
    max_signals = min(max(max_signals, 1), 20)
    job_id, note = start_or_resume_job("saved_volume_scan", len(snapshot["ranking"]), max_signals)
    return {"ok": True, "job_id": job_id, "message": note or "Saved Volume scan dimulakan."}


@app.get("/api/rescan-saved/start")
def start_rescan_saved():
    saved = get_recent_signals()
    if not saved:
        return {"ok": False, "error": "Tiada Recent Signal untuk di-rescan."}
    job_id, note = start_or_resume_job("rescan_saved", len(saved), 0)
    return {"ok": True, "job_id": job_id, "message": note or "Rescan dimulakan."}


@app.get("/api/job-resume")
def resume_job(job_id: str):
    job = get_job(job_id)
    if not job:
        return {"ok": False, "error": "Job tidak dijumpai."}
    if job["job_type"] == "volume_update":
        new_id, note = start_or_resume_job(job["job_type"], len(BURSA_UNIVERSE), 0, job_id)
    elif job["job_type"] in ("saved_volume_scan", "universe_scan"):
        new_id, note = start_or_resume_job(job["job_type"], job["total"], job["max_signals"], job_id)
    elif job["job_type"] == "rescan_saved":
        new_id, note = start_or_resume_job(job["job_type"], job["total"], 0, job_id)
    else:
        return {"ok": False, "error": "Jenis job tidak disokong."}
    if not new_id:
        return {"ok": False, "error": note}
    return {"ok": True, "job_id": new_id}

# ============================================================
# LEGACY TEST ENDPOINTS - KEKAL UNTUK DEBUG
# ============================================================
@app.get("/api/test/5")
def test_five():
    return {"requested_count": len(TEST_SYMBOLS), "results": [calculate_symbol(s) for s in TEST_SYMBOLS]}


@app.get("/api/test/history")
def test_history():
    results = []
    for symbol in TEST_SYMBOLS:
        result = calculate_symbol(symbol)
        results.append({"symbol": symbol, "historical_signals": result.get("historical_signals", [])} if result.get("ok") else result)
    return {"results": results}

# ============================================================
# HOME PAGE
# ============================================================
@app.get("/", response_class=HTMLResponse)
def home():
    return r'''<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bursa Supertrend Scanner</title>
<style>
body{margin:0;padding:20px;background:#101010;color:#eee;font-family:Arial,sans-serif}.container{max-width:900px;margin:auto}h1{margin-bottom:5px;font-size:24px}.subtitle{color:#999;margin-bottom:20px}button{width:100%;padding:14px;margin-top:10px;border:0;border-radius:8px;background:#1f8f4d;color:#fff;font-size:16px;font-weight:bold}button:disabled{opacity:.5}.secondary{background:#333}.danger{background:#7b3030}.volume{background:#1769aa}input{width:100%;box-sizing:border-box;padding:12px;margin-top:8px;margin-bottom:8px;border-radius:8px;border:1px solid #444;background:#181818;color:#fff;font-size:16px}.card{margin-top:20px;padding:15px;background:#181818;border-radius:10px}.card-title{font-size:18px;font-weight:bold;margin-bottom:10px}.info{margin-top:15px;padding:12px;background:#181818;border-radius:8px;color:#bbb;font-size:13px}pre{margin-top:20px;padding:15px;background:#181818;border-radius:8px;overflow-x:auto;white-space:pre-wrap;word-break:break-word;font-size:13px}.signal{padding:12px;margin-top:8px;border-radius:8px;background:#222;border-left:4px solid #1f8f4d}.signal-title{font-size:16px;font-weight:bold}.signal-detail{color:#bbb;margin-top:5px;font-size:13px}.rank{padding:9px;margin-top:5px;background:#222;border-radius:7px;font-size:13px}.progress{margin-top:10px;padding:12px;background:#222;border-radius:8px}.bar{height:10px;background:#333;border-radius:5px;overflow:hidden;margin-top:8px}.fill{height:100%;background:#1f8f4d;width:0}.small{font-size:12px;color:#aaa;margin-top:7px}
</style>
</head>
<body>
<div class="container">
<h1>BURSA SUPERTREND SCANNER</h1>
<div class="subtitle">Bursa Universe • Daily • ATR 10 / Factor 1.0</div>

<div class="card"><div class="card-title">RECENT SIGNALS</div><div id="recentSignals">Loading...</div><button class="secondary" onclick="loadRecentSignals()">REFRESH RECENT SIGNALS</button><button class="danger" id="rescanButton" onclick="startRescan()">RESCAN SAVED</button></div>

<div class="card"><div class="card-title">DAILY VOLUME SNAPSHOT</div><button class="volume" id="updateVolumeButton" onclick="startVolumeUpdate()">UPDATE DAILY VOLUME</button><div id="volumeStatus" class="info">Belum ada Daily Volume snapshot.</div><div id="volumeList"></div></div>

<div class="card"><div class="card-title">SCAN SAVED VOLUME</div><label>MAX SIGNALS</label><input id="savedMax" type="number" min="1" max="20" value="5"><button class="volume" id="savedVolumeButton" onclick="startSavedScan()">SCAN SAVED VOLUME → SCAN 5</button></div>

<div class="card"><div class="card-title">BURSA UNIVERSE SCANNER</div><label>MAX SIGNALS</label><input id="maxSignals" type="number" min="1" max="20" value="5"><button id="scanButton" onclick="startNormalScan()">SCAN 5</button><button class="secondary" onclick="testHistory()">TEST HISTORICAL SIGNAL</button></div>

<div id="jobProgress" class="card"><div class="card-title">JOB STATUS</div><div id="jobText">Tiada job aktif.</div><div class="bar"><div id="jobFill" class="fill"></div></div><div id="jobSmall" class="small"></div><button id="resumeButton" class="secondary" style="display:none" onclick="resumeJob()">RESUME JOB</button></div>

<div class="info"><b>Background Job:</b> browser tidak lagi menunggu request 10–12 minit. Server menjalankan kerja di background dan menyimpan progress selepas setiap kaunter.<br><br><b>Daily Volume:</b> hanya update manual. Scanner menggunakan snapshot tersimpan.<br><br><b>Recent Signals:</b> disimpan selama <b>5 hari</b>.</div>
<pre id="out">Ready.</pre>
</div>
<script>
let pollTimer=null;let currentJobId=null;let lastJobStatus=null;
function price(v){return v===null||v===undefined?"-":Number(v).toFixed(3)}
function fmtExpiry(v){try{return new Date(v).toLocaleString("ms-MY",{dateStyle:"medium",timeStyle:"short"})}catch(e){return v||"-"}}
function jobLabel(t){return ({volume_update:"UPDATE DAILY VOLUME",saved_volume_scan:"SCAN SAVED VOLUME",universe_scan:"BURSA UNIVERSE SCAN",rescan_saved:"RESCAN SAVED"}[t]||t)}
function setOut(s){document.getElementById("out").textContent=s}
async function loadRecentSignals(){const box=document.getElementById("recentSignals");try{const r=await fetch("/api/recent-signals");const d=await r.json();if(!d.signals.length){box.innerHTML='<div class="small">Tiada Recent Signal.</div>';return}box.innerHTML=d.signals.map(s=>'<div class="signal"><div class="signal-title">'+s.symbol+' | RM '+price(s.close)+'</div><div class="signal-detail">'+s.date+' | '+s.signal_name+'</div><div class="signal-detail">Expired: '+fmtExpiry(s.expires_at)+'</div></div>').join("")}catch(e){box.textContent="ERROR: "+e}}
async function loadSavedVolume(){const status=document.getElementById("volumeStatus"),list=document.getElementById("volumeList");try{const r=await fetch("/api/volume-ranking");const d=await r.json();if(!d.ranking||!d.ranking.length){status.textContent="Belum ada Daily Volume snapshot.";list.innerHTML="";return}status.textContent="Snapshot: "+d.updated_at+" | "+d.ranking.length+" / 36 kaunter.";list.innerHTML=d.ranking.slice(0,10).map(x=>'<div class="rank">#'+x.rank+' '+x.symbol+' | Volume '+Number(x.volume).toLocaleString()+"</div>").join("")}catch(e){status.textContent="Volume snapshot error: "+e}}
function buttonsBusy(busy){["updateVolumeButton","savedVolumeButton","scanButton","rescanButton"].forEach(id=>document.getElementById(id).disabled=busy)}
async function startVolumeUpdate(){try{const r=await fetch("/api/update-volume");const d=await r.json();if(!d.ok)throw new Error(d.error||"Gagal");currentJobId=d.job_id;setOut("UPDATE DAILY VOLUME dimulakan.\n\nServer sedang bekerja di background.\nBrowser tidak perlu menunggu.");pollJob()}catch(e){setOut("UPDATE ERROR\n\n"+e)}}
async function startSavedScan(){const max=Math.min(Math.max(parseInt(document.getElementById("savedMax").value)||5,1),20);try{const r=await fetch("/api/test/volume-scan/start?max_signals="+max);const d=await r.json();if(!d.ok)throw new Error(d.error||"Gagal");currentJobId=d.job_id;setOut("SCAN SAVED VOLUME dimulakan.\n\nServer sedang bekerja di background.");pollJob()}catch(e){setOut("SCAN ERROR\n\n"+e)}}
async function startNormalScan(){const max=Math.min(Math.max(parseInt(document.getElementById("maxSignals").value)||5,1),20);try{const r=await fetch("/api/test/universe/start?max_signals="+max);const d=await r.json();if(!d.ok)throw new Error(d.error||"Gagal");currentJobId=d.job_id;setOut("BURSA UNIVERSE SCAN dimulakan.\n\nServer sedang bekerja di background.");pollJob()}catch(e){setOut("SCAN ERROR\n\n"+e)}}
async function startRescan(){try{const r=await fetch("/api/rescan-saved/start");const d=await r.json();if(!d.ok)throw new Error(d.error||"Gagal");currentJobId=d.job_id;setOut("RESCAN SAVED dimulakan.\n\nServer sedang bekerja di background.");pollJob()}catch(e){setOut("RESCAN ERROR\n\n"+e)}}
async function pollJob(){if(pollTimer)clearInterval(pollTimer);await refreshJob();pollTimer=setInterval(refreshJob,5000)}
async function refreshJob(){try{const url=currentJobId?"/api/job-status?job_id="+encodeURIComponent(currentJobId):"/api/job-status";const r=await fetch(url);const d=await r.json();const job=d.job;if(!job){document.getElementById("jobText").textContent="Tiada job aktif.";document.getElementById("resumeButton").style.display="none";return}lastJobStatus=job;currentJobId=job.job_id;const total=Number(job.total||0),cur=Number(job.current_index||0),pct=total?Math.min(100,Math.round(cur/total*100)):0;document.getElementById("jobText").textContent=jobLabel(job.job_type)+" • "+job.status.toUpperCase();document.getElementById("jobFill").style.width=pct+"%";document.getElementById("jobSmall").textContent=(job.message||"")+" | Progress "+cur+" / "+total+" ("+pct+"%)";const resumable=(job.status==="interrupted"||job.status==="failed");document.getElementById("resumeButton").style.display=resumable?"block":"none";buttonsBusy(job.status==="running");if(job.status==="completed"||job.status==="failed"){renderJobResult(job);if(pollTimer){clearInterval(pollTimer);pollTimer=null}await loadRecentSignals();await loadSavedVolume();buttonsBusy(false)}}}catch(e){document.getElementById("jobSmall").textContent="Polling error: "+e}}
function renderJobResult(job){const r=job.results||{};let out=jobLabel(job.job_type)+"\n\n"+(job.message||"")+"\n\n";if(r.ranking){out+="TOP VOLUME:\n"+r.ranking.slice(0,10).map(x=>"#"+x.rank+" "+x.symbol+" | "+Number(x.volume).toLocaleString()).join("\n")+"\n"}if(r.signals){out+="\nSIGNAL:\n"+(r.signals.length?r.signals.map((s,i)=>(i+1)+". "+s.symbol+" | "+s.date+" | RM "+price(s.close)+" | "+s.signal_name).join("\n"):"Tiada signal ditemui.")+"\n"}if(r.skipped&&r.skipped.length)out+="\nSKIP RECENT SIGNAL: "+r.skipped.length+"\n"+r.skipped.map(x=>x.symbol).join(", ")+"\n";if(r.errors&&r.errors.length)out+="\nERROR: "+r.errors.length+"\n"+r.errors.map(x=>x.symbol+" -> "+x.error).join("\n");if(r.rescan)out+="\nRESCAN:\n"+r.rescan.map(x=>x.symbol+" -> "+x.status).join("\n");setOut(out)}
async function resumeJob(){if(!lastJobStatus)return;try{const r=await fetch("/api/job-resume?job_id="+encodeURIComponent(lastJobStatus.job_id));const d=await r.json();if(!d.ok)throw new Error(d.error||"Gagal resume");currentJobId=d.job_id;setOut("JOB DISAMBUNG.\n\nServer meneruskan dari checkpoint terakhir.");pollJob()}catch(e){setOut("RESUME ERROR\n\n"+e)}}
async function testHistory(){setOut("Testing historical signals...\n\nSila tunggu.");try{const r=await fetch("/api/test/history");setOut(JSON.stringify(await r.json(),null,2))}catch(e){setOut("HISTORY ERROR\n\n"+e)}}
async function init(){loadRecentSignals();loadSavedVolume();refreshJob();}
init();
</script>
</body></html>'''
