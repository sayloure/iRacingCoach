"""
Запусти iRacing, выезжай на трассу, затем выполни: python probe.py
"""

import irsdk
import time
import sys


def main():
    irc = irsdk.IRSDK()
    irc.startup()

    if not irc.is_connected:
        print("iRacing не запущен или нет активной сессии")
        sys.exit(1)

    print("Подключено к iRacing. Чтение данных...\n")

    try:
        while True:
            lap_pct = irc['LapDistPct']
            speed = irc['Speed']
            brake = irc['Brake']
            throttle = irc['Throttle']
            gear = irc['Gear']

            if lap_pct is None:
                print("Сессия завершена или потеряно соединение с iRacing.")
                break

            print(f"LapDistPct: {lap_pct:.2f} | Speed: {speed:.1f} | "
                  f"Brake: {brake:.2f} | Throttle: {throttle:.2f} | "
                  f"Gear: {int(gear) if gear is not None else 0}")

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nЗавершено пользователем.")
    finally:
        irc.shutdown()


if __name__ == '__main__':
    main()