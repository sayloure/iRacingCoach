from collections import deque


class TelemetryState:
    def __init__(self):
        self.connected = False
        self.lap_pct = 0.0
        self.speed_kmh = 0.0
        self.throttle = 0.0
        self.brake = 0.0
        self.clutch = 0.0
        self.steer_rad = 0.0
        self.gear = 0
        self.lap_time = 0.0
        self.track_len = 4500.0

        # Круги и позиция в гонке
        self.current_lap = 0
        self.total_laps = 0
        self.laps_remain = -1
        self.position = 0

        # Глобальные настройки и настройки отдельного виджета Педалей
        self.show_clutch = False
        self.warn_lead_sec = 5.0
        self.pedals_show_you_pct = True
        self.pedals_show_ref_pct = True

        # Вкл/Выкл элементов центрального Кокпит-виджета
        self.cp_show_ribbon = True
        self.cp_show_player_on_rib = True
        self.cp_show_pedal_match = True
        self.cp_show_steer = True
        self.cp_show_steer_text = True
        # Режим передач: "mismatch" (только при неверной), "always" (всегда), "off" (выкл)
        self.cp_gear_mode = "always"
        self.cp_show_speed_you = True
        self.cp_show_speed_ref = True
        self.cp_show_brake = True
        self.cp_show_throttle = True
        self.cp_show_clutch = False

        # Настройки текста процентов на педалях внутри Кокпита
        self.cp_pedal_show_you_pct = True
        self.cp_pedal_show_ref_pct = True

        # 1) Дальность/высота 3D-траектории (25..100%)
        self.cp_ribbon_top_reach = 75
        # 2) Сила затухания прозрачности от верха к середине траектории (0..100%)
        self.cp_ribbon_top_fade = 55

        # Максимальный угол шкалы руля в каждую сторону (90..360 градусов, по умолчанию 220°)
        self.cp_steer_max_deg = 220
        self.cp_steer_auto_scale = True  # Авто-подстройка под максимум загруженного рефа

        # Цвет текста градусов руля (HEX)
        self.cp_steer_text_color = "#c8e1f5"

        # Индивидуальная прозрачность (10..100%) каждого элемента Кокпита
        self.cp_op_ribbon = 100
        self.cp_op_player_on_rib = 100
        self.cp_op_steer = 100
        self.cp_op_gear = 100
        self.cp_op_speed = 100
        self.cp_op_brake = 100
        self.cp_op_throttle = 100
        self.cp_op_clutch = 100

        # Индивидуальные смещения (dx, dy в пикселях) для КАЖДОГО модуля внутри Кокпита
        self.cp_offsets = {
            "ribbon": [0, 0],
            "steer": [0, 0],
            "steer_text": [0, 0],
            "gear": [0, 0],
            "speed": [0, 0],
            "brake": [0, 0],
            "throttle": [0, 0],
            "clutch": [0, 0],
        }

        # Индивидуальный масштаб (40..250%) для КАЖДОГО модуля внутри Кокпита
        self.cp_scales = {
            "ribbon": 100,
            "steer": 100,
            "steer_text": 100,
            "gear": 100,
            "speed": 100,
            "brake": 100,
            "throttle": 100,
            "clutch": 100,
        }

        # Эталон в текущей точке
        self.t_speed = 0.0
        self.t_throttle = 0.0
        self.t_brake = 0.0
        self.t_clutch = 0.0
        self.t_steer_rad = 0.0
        self.t_gear = 0
        self.t_time = 0.0
        self.ref_idx = 0

        # Метрики тренера
        self.delta_sec = 0.0
        self.delta_speed = 0.0
        self.brake_in_sec = None
        self.brake_dist_m = None
        self.banner_mode = ""

        self.ref_data = []
        self.brake_zones = []
        self.player_trace = deque(maxlen=300)
        self.delta_history = deque(maxlen=120)