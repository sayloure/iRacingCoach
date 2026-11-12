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


def load_garage61_csv(csv_path):
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
    col_time = _find_column(headers, "LapCurrentLapTime", "LapTime", "SessionTime", "Time")

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
            "Time": _safe_float(row.get(col_time), -1.0) if col_time else -1.0,
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

    # Нормализация сцепления (в iRacing 1.0 = отпущено, 0.0 = нажато в пол -> переводим в 0..1 нажатия педали)
    if col_clutch:
        max_cl = max(r["Clutch"] for r in rows)
        if max_cl > 1.5:
            for r in rows:
                r["Clutch"] /= 100.0
        avg_cl = sum(r["Clutch"] for r in rows) / len(rows)
        if avg_cl > 0.5:
            # Стандарт iRacing: большую часть круга Clutch = 1.0 (отпущено) -> инвертируем в процент нажатия
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

    has_valid_time = all(r["Time"] >= 0 for r in rows) and (rows[-1]["Time"] > rows[0]["Time"])
    if has_valid_time:
        t0 = rows[0]["Time"]
        for r in rows:
            r["Time"] -= t0
    else:
        t_acc = 0.0
        rows[0]["Time"] = 0.0
        for i in range(1, len(rows)):
            dpct = max(0.0, rows[i]["LapDistPct"] - rows[i - 1]["LapDistPct"])
            v_ms = max(5.0, (rows[i]["Speed"] + rows[i - 1]["Speed"]) * 0.5 / 3.6)
            t_acc += (dpct * 4000.0) / v_ms
            rows[i]["Time"] = t_acc

    return rows


def get_target_state(current_lap_dist, ref_data):
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

    best = 0
    best_dist = float("inf")
    for i, r in enumerate(ref_data):
        raw = abs(current_lap_dist - r["LapDistPct"])
        d = 1.0 - raw if raw > 0.5 else raw
        if d < best_dist:
            best_dist = d
            best = i

    pt = ref_data[best]
    return {
        "Speed": pt["Speed"],
        "Brake": pt["Brake"],
        "Throttle": pt["Throttle"],
        "Clutch": pt.get("Clutch", 0.0),
        "SteeringWheelAngle": pt["SteeringWheelAngle"],
        "Gear": pt.get("Gear", 0),
        "Time": pt.get("Time", 0.0),
        "Index": best,
    }