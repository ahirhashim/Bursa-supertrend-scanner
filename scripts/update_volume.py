import json
import os
import time
from datetime import datetime, timezone, timedelta

import requests


ITICK_API_KEY = os.getenv("ITICK_API_KEY", "")
ITICK_KLINE_URL = "https://api-free.itick.org/stock/kline"

REQUEST_DELAY = 20.0
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 15


BURSA_UNIVERSE = [
    "D&O", "SRIDGE", "DNEX", "ZETRIX", "INARI", "FRONTKN", "JCY",
    "GREATEC", "NOTION", "SNS", "VSTECS", "AEMULUS", "MICROLN", "JHM",
    "TOPGLOV", "SUPERMX", "HARTA", "KOSSAN", "DXN", "ARMADA", "VELESTO",
    "CAPITALA", "MRCB", "BJCORP", "TANCO", "JAKS", "WCT", "IJM", "MAHSING",
    "TM", "AXIATA", "MAXIS", "DIALOG", "GENM", "YTLPOWR", "VS"
]


def malaysia_now():
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=8))
    ).strftime("%Y-%m-%d %H:%M:%S")


def fetch_volume(symbol, index):
    if not ITICK_API_KEY:
        return {
            "symbol": symbol,
            "ok": False,
            "error": "ITICK_API_KEY secret belum diset."
        }

    if index > 0:
        time.sleep(REQUEST_DELAY)

    headers = {
        "accept": "application/json",
        "token": ITICK_API_KEY
    }

    params = {
        "region": "MY",
        "exchange": "MYX",
        "code": symbol,
        "kType": 8,
        "limit": 1
    }

    try:
        response = requests.get(
            ITICK_KLINE_URL,
            params=params,
            headers=headers,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)
        )
    except requests.RequestException as error:
        return {
            "symbol": symbol,
            "ok": False,
            "error": f"Request error: {error}"
        }

    if response.status_code != 200:
        return {
            "symbol": symbol,
            "ok": False,
            "error": f"HTTP {response.status_code}"
        }

    try:
        data = response.json()
    except ValueError:
        return {
            "symbol": symbol,
            "ok": False,
            "error": "Response bukan JSON."
        }

    if data.get("code") != 0:
        return {
            "symbol": symbol,
            "ok": False,
            "error": str(data)
        }

    raw = data.get("data", [])

    if not isinstance(raw, list) or not raw:
        return {
            "symbol": symbol,
            "ok": False,
            "error": "Tiada daily candle."
        }

    try:
        latest = max(
            raw,
            key=lambda x: float(x.get("t", 0) or 0)
        )

        volume = float(latest.get("v", 0) or 0)

        return {
            "symbol": symbol,
            "ok": True,
            "volume": volume
        }

    except Exception as error:
        return {
            "symbol": symbol,
            "ok": False,
            "error": str(error)
        }


def main():
    if not ITICK_API_KEY:
        raise SystemExit("ITICK_API_KEY secret belum diset.")

    results = []
    errors = []

    total = len(BURSA_UNIVERSE)

    for index, symbol in enumerate(BURSA_UNIVERSE):
        print(
            f"[{index + 1}/{total}] {symbol}",
            flush=True
        )

        item = fetch_volume(symbol, index)

        if item.get("ok"):
            results.append(item)

            print(
                f"  volume={item['volume']}",
                flush=True
            )
        else:
            errors.append(item)

            print(
                f"  ERROR: {item.get('error')}",
                flush=True
            )

    ranking = sorted(
        results,
        key=lambda x: float(x.get("volume", 0)),
        reverse=True
    )

    for rank, item in enumerate(ranking, start=1):
        item["rank"] = rank

    snapshot = {
        "ok": bool(ranking),
        "updated_at": malaysia_now(),
        "count": len(ranking),
        "ranking": ranking,
        "errors": errors
    }

    with open(
        "volume_snapshot.json",
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            snapshot,
            file,
            ensure_ascii=False,
            indent=2
        )

    print(
        f"Saved volume snapshot: "
        f"{len(ranking)}/{total}",
        flush=True
    )


if __name__ == "__main__":
    main()
