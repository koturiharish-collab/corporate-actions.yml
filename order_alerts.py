    if matches:
        # Keep the first-seen timestamp, even if details are refreshed later.
        alert["time"] = matches[0].get("time") or alert.get("time")

        # If more than one old copy exists, collapse them into one.
        if len(matches) == 1 and matches[0] == alert:
            return False
        for existing in matches:
            alerts.remove(existing)

    alerts.append(alert)
    data["updated"] = datetime.now(IST).isoformat(timespec="seconds")
    ALERT_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def main():
    today = datetime.now(IST).date()

    # Re-read a rolling 7-calendar-day window. This backfills missed scans
    # while the exchange APIs still expose those announcements by date.
    start_day = today - timedelta(days=7)
    items = []
    successful_sources = 0

    for name, source in (("BSE", fetch_bse), ("NSE", fetch_nse)):
        try:
            rows = source(start_day, today)
            log(
                f"{name}: {len(rows)} announcements "
                f"from {start_day} through {today}"
            )
            items.extend(rows)
            successful_sources += 1
        except Exception as exc:
            log(f"{name} fetch failed: {exc!r}")

    if successful_sources == 0:
        log("Both exchange sources failed; leaving Share Sanket data unchanged.")
        return 1

    # Remove duplicates caused by overlapping exchange API pages.
    unique_items = {}
    for item in items:
        unique_items[item["id"]] = item
    items = list(unique_items.values())

    try:
        current_data = json.loads(ALERT_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        current_data = {}

    existing_alerts = current_data.get("alerts", [])
    existing_by_source = {
        alert.get("source_id"): alert
        for alert in existing_alerts
        if alert.get("source_id")
    }
    # Also recognise earlier records by the deterministic alert ID, in case
    # a previous app record did not contain source_id for some reason.
    existing_alert_ids = {
        alert.get("id") for alert in existing_alerts if alert.get("id")
    }

    hits = [
        item for item in items
        if INCLUDE.search(f"{item.get('title', '')} {item.get('text', '')}")
        and not EXCLUDE.search(f"{item.get('title', '')} {item.get('text', '')}")
    ]

    def app_alert_id(source_id):
        return hashlib.sha256(str(source_id).encode("utf-8")).hexdigest()[:24]

    # Recover every qualifying announcement not present in alerts.json.
    # seen.json is deliberately NOT used as the authority for app delivery:
    # older runs may have marked an ID seen before successfully saving it.
    new_hits = [
        item for item in hits
        if (
            item["id"] not in existing_by_source
            and app_alert_id(item["id"]) not in existing_alert_ids
        )
    ]

    # Reprocess older-format records once so their filing fields can be refreshed.
    refresh_pdf = []
    for item in hits:
        old_alert = existing_by_source.get(item["id"])
        if not old_alert or not item.get("link"):
            continue

        if old_alert.get("parser_version", 0) < 3:
            refresh_pdf.append(item)

    to_process = new_hits + refresh_pdf
    log(
        f"{len(hits)} qualifying order/contract announcements in the window; "
        f"{len(new_hits)} missing from the app; "
        f"{len(refresh_pdf)} older records to refresh"
    )

    market_cache = {}
    changed = 0
    failed_ids = set()

    for item in to_process:
        try:
            alert = order_alert(item, market_cache)
            if save_app_alert(alert):
                changed += 1
        except Exception as exc:
            failed_ids.add(item["id"])
            log("Could not add/update order alert:", item.get("id"), repr(exc))

    # Keep seen.json as a compact scanner checkpoint, but never let it prevent
    # a missing application record from being recovered on the next scan.
    seen = load_seen()
    fetched_ids = [item["id"] for item in items]
    seen = list(dict.fromkeys(seen + fetched_ids))
    save_seen(seen)

    log(f"Share Sanket order alerts added or updated: {changed}")
    if failed_ids:
        log(f"Order records that need retry: {len(failed_ids)}")
