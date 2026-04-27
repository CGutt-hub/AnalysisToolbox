"""API Reader — Fetch data from a JSON REST API into a parquet file.

Input:  JSON entity config (participant_id, iso2, year_start, …)
        + API endpoints JSON (second arg, or inlined as 'api' key)
Output: {participant_id}_api.parquet  (long-format: participant_id, indicator, year, value, …)

Generic: works with any REST API returning JSON arrays.  The API definition
(base URL, endpoint template, field mapping, indicators) is config-driven —
no Python changes needed to target a different API or add indicators.
"""
import polars as pl, json, sys, os, time
from typing import Any
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

TAG = "api_reader"

def log_info(msg: str) -> None: print(f"[{TAG}] INFO: {msg}")
def log_warning(msg: str) -> None: print(f"[{TAG}] WARNING: {msg}")
def log_error(msg: str) -> None: print(f"[{TAG}] ERROR: {msg}")


def _fetch_json(url: str, retries: int = 3, timeout: int = 30) -> Any:
    """GET url → parsed JSON, with exponential-backoff retries."""
    for attempt in range(retries):
        try:
            with urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError, json.JSONDecodeError, OSError) as exc:
            if attempt < retries - 1:
                wait = 2 ** attempt
                log_warning(f"Retry {attempt + 1}/{retries} in {wait}s: {exc}")
                time.sleep(wait)
            else:
                log_error(f"Failed after {retries} attempts: {url} — {exc}")
                return None


def _detect_latest(tpl: str, variables: dict[str, Any],
                   first_code: str, retries: int) -> int | None:
    """Auto-detect the latest available year (e.g. World Bank mrv=1 pattern)."""
    url = tpl.format(**{**variables, "code": first_code})
    data = _fetch_json(url, retries)
    if data and isinstance(data, list) and len(data) > 1 and data[1]:
        try:
            return int(data[1][0].get("date", 0))
        except (ValueError, IndexError, TypeError, AttributeError):
            pass
    return None


def api_read(ip: str, endpoints_path: str | None = None) -> str:
    """Fetch indicators from a REST API for one entity.

    Parameters
    ----------
    ip : str
        Entity JSON config (must contain participant_id; may contain iso2, year_start, …).
    endpoints_path : str or None
        Shared API config JSON.  Falls back to 'api' key inside entity config.
    """
    if not os.path.exists(ip):
        log_error(f"File not found: {ip}"); sys.exit(1)
    with open(ip, "r", encoding="utf-8") as fh:
        entity: dict[str, Any] = json.load(fh)

    pid = entity.get("participant_id",
                     os.path.splitext(os.path.basename(ip))[0])
    log_info(f"Entity: {pid}")

    # --- resolve API definition ------------------------------------------------
    if endpoints_path and os.path.exists(endpoints_path):
        with open(endpoints_path, "r", encoding="utf-8") as fh:
            api: dict[str, Any] = json.load(fh)
        log_info(f"Endpoints config: {endpoints_path}")
    elif "api" in entity:
        api = entity["api"]
    else:
        log_error("No API config — provide endpoints JSON or 'api' key in entity")
        sys.exit(1)

    base_url:   str             = api["base_url"]
    url_tpl:    str             = api["url_template"]
    indicators: dict[str, str]  = api.get("indicators", {})
    resp_idx:   int             = api.get("response_index", 1)
    val_field:  str             = api.get("record_value", "value")
    date_field: str             = api.get("record_date", "date")
    retries:    int             = api.get("retries", 3)
    timeout:    int             = api.get("timeout", 30)

    if not indicators:
        log_error("No indicators in API config"); sys.exit(1)

    # template variables from entity (scalars only, excluding nested dicts)
    variables: dict[str, Any] = {
        k: v for k, v in entity.items()
        if k != "api" and isinstance(v, (str, int, float))
    }

    # auto-detect year_end when not provided
    if "year_end" not in variables and "detect_year_end" in api:
        first_code = next(iter(indicators.values()))
        det_tpl = base_url + api["detect_year_end"]
        year_end = _detect_latest(det_tpl, variables, first_code, retries)
        if year_end:
            variables["year_end"] = year_end
            log_info(f"Auto-detected year_end: {year_end}")
        else:
            variables["year_end"] = 2025
            log_warning("Could not auto-detect year_end, defaulting to 2025")

    # --- fetch all indicators --------------------------------------------------
    log_info(f"Fetching {len(indicators)} indicators from {base_url}")
    records: list[dict[str, Any]] = []
    meta = {k: v for k, v in entity.items()
            if k not in ("participant_id", "api") and isinstance(v, (str, int, float, bool))}

    for name, code in indicators.items():
        url = base_url + url_tpl.format(**{**variables, "code": code})
        data = _fetch_json(url, retries, timeout)
        if not data or not isinstance(data, list) or len(data) <= resp_idx:
            log_warning(f"No data for {name} ({code})"); continue
        entries = data[resp_idx]
        if not entries:
            log_warning(f"Empty response for {name} ({code})"); continue

        count = 0
        for entry in entries:
            val = entry.get(val_field)
            if val is None:
                continue
            records.append({
                "participant_id": pid,
                "source": api.get("source", base_url),
                "indicator": name,
                "indicator_code": code,
                "year": int(entry.get(date_field, 0)),
                "value": float(val),
                **meta,
            })
            count += 1
        log_info(f"  {name}: {count} records")

    if not records:
        log_error("No records fetched from API"); sys.exit(1)

    df = pl.DataFrame(records)
    out_file = f"{pid}_api.parquet"
    df.write_parquet(out_file, compression="snappy")

    yr_min, yr_max = df["year"].min(), df["year"].max()
    log_info(f"Output: {out_file} ({len(records)} records, {len(indicators)} indicators, {yr_min}\u2013{yr_max})")
    return out_file


if __name__ == "__main__":
    a = sys.argv
    if len(a) >= 2:
        api_read(a[1], a[2] if len(a) > 2 else None)
    else:
        print(f"[{TAG}] Fetch data from a JSON REST API into parquet.\n"
              f"Usage: api_reader.py <entity_config.json> [api_endpoints.json]")
        sys.exit(1)
