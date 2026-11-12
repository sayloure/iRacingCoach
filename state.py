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

        # Глобальные настройки
        self.show_clutch = False
        self.warn_lead_sec = 5.0

        # Вкл/Выкл элементов центрального Кокпит-виджета
        self.cp_show_ribbon = True
        self.cp_show_player_on_rib = True
        self.cp_show_pedal_match = True
        self.cp_show_steer = True
        self.cp_show_steer_text = True
        # Режим передач: "mismatch" (только при неверной), "always" (всегда), "off" (выкл)
        self.cp_gear_mode = "mismatch"
        self.cp_show_speed_you = True
        self.cp_show_speed_ref = True
        self.cp_show_brake = True
        self.cp_show_throttle = True
        self.cp_show_clutch = False

        # Индивидуальная прозрачность (10..100%) каждого элемента Кокпита
        self.cp_op_ribbon = 100
        self.cp_op_player_on_rib = 100
        self.cp_op_steer = 100
        self.cp_op_gear = 100
        self.cp_op_speed = 100
        self.cp_op_brake = 100
        self.cp_op_throttle = 100
        self.cp_op_clutch = 100

        # Смещение 3D-траектории внутри виджета Кокпита (в пикселях)
        self.cp_ribbon_dx = 0
        self.cp_ribbon_dy = 0

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