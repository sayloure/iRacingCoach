import csv
import sys
import os


def _find_column(headers, *candidates):
    for c in candidates:
        for h in headers:
            if h.strip().lower() == c.lower():
                return h.strip()
    return None


def load_garage61_csv(csv_path):
    if not os.path.exists(csv_path):
        print(f"Ошибка: файл не найден — {csv_path}")
        sys.exit(1)

    with open(csv_path, newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            print("Ошибка: файл CSV пуст или не содержит заголовков")
            sys.exit(1)

        headers = reader.fieldnames
        col_lap = _find_column(headers, 'LapDistPct', 'lapdistpct', 'LapDistPct', 'lap_dist_pct')
        col_speed = _find_column(headers, 'Speed', 'speed', 'Speed (km/h)', 'Speed (mph)', 'speed_kmh', 'speed_mph')
        col_brake = _find_column(headers, 'Brake', 'brake')
        col_throttle = _find_column(headers, 'Throttle', 'throttle')
        col_steer = _find_column(headers, 'SteeringWheelAngle', 'steeringwheelangle', 'Steer',
                                 'steering_angle', 'SteeringAngle')

        missing = []
        if col_lap is None:
            missing.append('LapDistPct')
        if col_speed is None:
            missing.append('Speed')
        if col_brake is None:
            missing.append('Brake')
        if col_throttle is None:
            missing.append('Throttle')
        if missing:
            print(f"Ошибка: не найдены колонки {missing}. Доступные заголовки: {headers}")
            sys.exit(1)

        rows = []
        for row in reader:
            steer_val = float(row[col_steer]) if col_steer else 0.0
            rows.append({
                'LapDistPct': float(row[col_lap]),
                'Speed': float(row[col_speed]),
                'Brake': float(row[col_brake]),
                'Throttle': float(row[col_throttle]),
                'SteeringWheelAngle': steer_val,
            })

    if not rows:
        print("Ошибка: файл CSV пуст")
        sys.exit(1)

    rows.sort(key=lambda r: r['LapDistPct'])
    return rows


def get_target_state(current_lap_dist, ref_data):
    if not ref_data:
        return {'Speed': 0, 'Brake': 0, 'Throttle': 0, 'SteeringWheelAngle': 0}

    n = len(ref_data)

    def lap_diff(a, b):
        raw = abs(a - b)
        return 1.0 - raw if raw > 0.5 else raw

    best = 0
    best_dist = float('inf')
    for i in range(n):
        d = lap_diff(current_lap_dist, ref_data[i]['LapDistPct'])
        if d < best_dist:
            best_dist = d
            best = i

    return {
        'Speed': ref_data[best]['Speed'],
        'Brake': ref_data[best]['Brake'],
        'Throttle': ref_data[best]['Throttle'],
        'SteeringWheelAngle': ref_data[best]['SteeringWheelAngle'],
    }


if __name__ == '__main__':
    csv_path = os.path.join(os.path.dirname(__file__), 'reference.csv')

    if not os.path.exists(csv_path):
        print(f"Тест пропущен: файл {csv_path} не найден")
        sys.exit(0)

    ref = load_garage61_csv(csv_path)
    print(f"Загружено {len(ref)} строк")
    print("Первые 5 строк:")
    for r in ref[:5]:
        print(f"  LapDistPct={r['LapDistPct']:.4f}  Speed={r['Speed']:.1f}  "
              f"Brake={r['Brake']:.3f}  Throttle={r['Throttle']:.3f}")

    for pct in [0.25, 0.5, 0.75]:
        target = get_target_state(pct, ref)
        print(f"\nget_target_state({pct}):")
        print(f"  Speed={target['Speed']:.1f}, Brake={target['Brake']:.3f}, "
              f"Throttle={target['Throttle']:.3f}, "
              f"Steer={target['SteeringWheelAngle']:.3f}")