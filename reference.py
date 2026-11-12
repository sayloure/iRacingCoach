import bisect
import csv
import math
import os


def _find_column(headers, *candidates):
    for c in candidates:
        for h in headers:
            if h and h.strip().lower() == c.lower():
                return h.strip()
    return None


def _safe_float(val, default=0.0):
    try:
        if val is None or val == "":
            return default
        return float(str(val).replace(",", "."))
    except ValueError:
        return default


def rebuild_reference_time(rows, track_len_m: float = 4000.0):
    if not rows:
        return

    raw_times = [r.get("_raw_time", -1.0) for r in rows]
    is_monotonic = (
        all(t >= 0.0 for t in raw_times)
        and all(raw_times[i] >= raw_times[i - 1] for i in range(1, len(raw_times)))
        and (raw_times[-1] - raw_times[0]) > 20.0
        and rows[0]["LapDistPct"] < 0.02
    )

    if is_monotonic:
        t0 = raw_times[0]
        v0_ms = max(5.0, rows[0]["Speed"] / 3.6)
        offset0 = (rows[0]["LapDistPct"] * track_len_m) / v0_ms
        for i, r in enumerate(rows):
            r["Time"] = (raw_times[i] - t0) + offset0
        return

    v0_ms = max(5.0, rows[0]["Speed"] / 3.6)
    t_acc = (rows[0]["LapDistPct"] * track_len_m) / v0_ms
    rows[0]["Time"] = t_acc

    for i in range(1, len(rows)):
        dpct = rows[i]["LapDistPct"] - rows[i - 1]["LapDistPct"]
        if dpct <= 0.0:
            rows[i]["Time"] = t_acc
            continue
        v_prev = rows[i - 1]["Speed"] / 3.6
        v_curr = rows[i]["Speed"] / 3.6
        v_avg_ms = max(3.0, (v_prev + v_curr) * 0.5)
        t_acc += (dpct * track_len_m) / v_avg_ms
        rows[i]["Time"] = t_acc


def load_garage61_csv(csv_path, track_len_m: float = 4000.0):
    if not os.path.exists(csv_path):
        print(f"Ошибка: файл не найден — {csv_path}")
        return []

    with open(csv_path, mode="r", encoding="utf-8-sig", newline="") as f:
        lines = f.readlines()

    header_idx = 0
    for idx, line in enumerate(lines[:20]):
        low = line.lower()
        if "lapdistpct" in low or ("speed" in low and "throttle" in low):
            header_idx = idx
            break

    reader = csv.DictReader(lines[header_idx:])
    if not reader.fieldnames:
        return []

    headers = reader.fieldnames
    col_lap = _find_column(headers, "LapDistPct", "lap_dist_pct", "DistancePct")
    col_speed = _find_column(headers, "Speed", "Speed (km/h)", "speed_kmh", "Velocity")
    col_brake = _find_column(headers, "Brake", "BrakePct")
    col_throttle = _find_column(headers, "Throttle", "ThrottlePct")
    col_clutch = _find_column(headers, "Clutch", "ClutchPct")
    col_steer = _find_column(headers, "SteeringWheelAngle", "Steer", "SteeringAngle")
    col_gear = _find_column(headers, "Gear")
    col_time = _find_column(headers, "LapCurrentLapTime", "LapTime", "Time", "SessionTime")

    if not col_lap or not col_speed or not col_brake or not col_throttle:
        print(f"Не найдены основные колонки в {csv_path}. Заголовки: {headers}")
        return []

    rows = []
    for row in reader:
        pct = _safe_float(row.get(col_lap), -1.0)
        if pct < 0.0:
            continue
        rows.append({
            "LapDistPct": pct,
            "Speed": _safe_float(row.get(col_speed)),
            "Brake": _safe_float(row.get(col_brake)),
            "Throttle": _safe_float(row.get(col_throttle)),
            "Clutch": _safe_float(row.get(col_clutch), 1.0) if col_clutch else 1.0,
            "SteeringWheelAngle": _safe_float(row.get(col_steer)) if col_steer else 0.0,
            "Gear": int(round(_safe_float(row.get(col_gear), 0))) if col_gear else 0,
            "_raw_time": _safe_float(row.get(col_time), -1.0) if col_time else -1.0,
            "Time": 0.0,
        })

    if not rows:
        return []

    max_pct = max(r["LapDistPct"] for r in rows)
    if max_pct > 1.5:
        for r in rows:
            r["LapDistPct"] /= 100.0

    max_speed = max(r["Speed"] for r in rows)
    if max_speed < 115.0:
        for r in rows:
            r["Speed"] *= 3.6

    max_thr = max(r["Throttle"] for r in rows)
    if max_thr > 1.5:
        for r in rows:
            r["Throttle"] /= 100.0

    max_brk = max(r["Brake"] for r in rows)
    if max_brk > 1.5:
        for r in rows:
            r["Brake"] /= 100.0

    if col_clutch:
        max_cl = max(r["Clutch"] for r in rows)
        if max_cl > 1.5:
            for r in rows:
                r["Clutch"] /= 100.0
        avg_cl = sum(r["Clutch"] for r in rows) / len(rows)
        if avg_cl > 0.5:
            for r in rows:
                r["Clutch"] = max(0.0, min(1.0, 1.0 - r["Clutch"]))
    else:
        for r in rows:
            r["Clutch"] = 0.0

    max_steer = max(abs(r["SteeringWheelAngle"]) for r in rows)
    if max_steer > 12.0:
        for r in rows:
            r["SteeringWheelAngle"] = math.radians(r["SteeringWheelAngle"])

    rows.sort(key=lambda r: r["LapDistPct"])

    unique_rows = [rows[0]]
    for r in rows[1:]:
        if r["LapDistPct"] > unique_rows[-1]["LapDistPct"] + 1e-6:
            unique_rows.append(r)
    rows = unique_rows

    rebuild_reference_time(rows, track_len_m)
    return rows


def get_target_state(current_lap_dist: float, ref_data: list, ref_pcts: list | None = None):
    """Мгновенная линейная интерполяция между соседними точками круга (включая переход через старт-финиш)."""
    if not ref_data:
        return {
            "Speed": 0.0,
            "Brake": 0.0,
            "Throttle": 0.0,
            "Clutch": 0.0,
            "SteeringWheelAngle": 0.0,
            "Gear": 0,
            "Time": 0.0,
            "Index": 0,
        }

    n = len(ref_data)
    if n == 1:
        pt = ref_data[0]
        return {**pt, "Index": 0}

    pct = current_lap_dist % 1.0
    if ref_pcts is None or len(ref_pcts) != n:
        ref_pcts = [r["LapDistPct"] for r in ref_data]

    idx = bisect.bisect_right(ref_pcts, pct)

    if 0 < idx < n:
        r0 = ref_data[idx - 1]
        r1 = ref_data[idx]
        span = r1["LapDistPct"] - r0["LapDistPct"]
        alpha = (pct - r0["LapDistPct"]) / span if span > 1e-6 else 0.0
    else:
        # Плавный переход через линию старт-финиш (между последней и первой точкой)
        r0 = ref_data[-1]
        r1 = ref_data[0]
        span = (1.0 - r0["LapDistPct"]) + r1["LapDistPct"]
        dist_from_r0 = (pct - r0["LapDistPct"]) if pct >= r0["LapDistPct"] else (1.0 - r0["LapDistPct"] + pct)
        alpha = dist_from_r0 / span if span > 1e-6 else 0.0

    alpha = max(0.0, min(1.0, alpha))

    return {
        "Speed": r0["Speed"] + (r1["Speed"] - r0["Speed"]) * alpha,
        "Brake": r0["Brake"] + (r1["Brake"] - r0["Brake"]) * alpha,
        "Throttle": r0["Throttle"] + (r1["Throttle"] - r0["Throttle"]) * alpha,
        "Clutch": r0.get("Clutch", 0.0) + (r1.get("Clutch", 0.0) - r0.get("Clutch", 0.0)) * alpha,
        "SteeringWheelAngle": r0["SteeringWheelAngle"] + (r1["SteeringWheelAngle"] - r0["SteeringWheelAngle"]) * alpha,
        "Gear": r0.get("Gear", 0) if alpha < 0.5 else r1.get("Gear", 0),
        "Time": r0.get("Time", 0.0) + (r1.get("Time", 0.0) - r0.get("Time", 0.0)) * alpha if 0 < idx < n else (r0.get("Time", 0.0) if idx == n else r1.get("Time", 0.0)),
        "Index": max(0, min(n - 1, idx - 1)),
    }