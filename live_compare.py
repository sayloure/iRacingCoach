"""
Запусти iRacing, выезжай на трассу, затем выполни: python live_compare.py
"""

import irsdk
import time
import sys
import os

from reference import load_garage61_csv, get_target_state


def main():
    csv_path = os.path.join(os.path.dirname(__file__), 'reference.csv')
    if not os.path.exists(csv_path):
        print(f"Ошибка: файл эталона не найден — {csv_path}")
        sys.exit(1)

    ref_data = load_garage61_csv(csv_path)
    print(f"Загружен эталон: {len(ref_data)} точек\n")

    irc = irsdk.IRSDK()
    irc.startup()

    if not irc.is_connected:
        print("iRacing не запущен или нет активной сессии")
        sys.exit(1)

    print("Подключено к iRacing. Сравнение с эталоном...\n")

    prev_lap_pct = None

    try:
        while True:
            lap_pct = irc['LapDistPct']
            live_speed = irc['Speed']
            live_brake = irc['Brake']
            live_throttle = irc['Throttle']

            if lap_pct is None:
                print("Сессия завершена или потеряно соединение с iRacing.")
                break

            if prev_lap_pct is not None and prev_lap_pct - lap_pct > 0.5:
                print("\n--- Новый круг ---\n")
            prev_lap_pct = lap_pct

            target = get_target_state(lap_pct, ref_data)

            delta_speed = live_speed - target['Speed']
            delta_brake = live_brake - target['Brake']

            hints = []
            if target['Brake'] > 0.5 and live_brake < 0.1:
                hints.append("⚠️ ТОРМОЗИ!")
            if delta_speed > 10:
                hints.append(f"⚠️ Быстрее эталона на {delta_speed:.1f} м/с")
            if delta_speed < -10:
                hints.append(f"✓ Медленнее эталона на {-delta_speed:.1f} м/с")

            line = (f"LapDistPct: {lap_pct:.2f} | "
                    f"Speed: {live_speed:.1f} / {target['Speed']:.1f} ({delta_speed:+.1f}) | "
                    f"Brake: {live_brake:.2f} / {target['Brake']:.2f} ({delta_brake:+.2f}) | "
                    f"Throttle: {live_throttle:.2f} / {target['Throttle']:.2f}")
            if hints:
                line += " | " + " ".join(hints)
            print(line)

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nЗавершено пользователем.")
    finally:
        irc.shutdown()


if __name__ == '__main__':
    main()