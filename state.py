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

        # Настройки отображения
        self.show_clutch = False
        self.warn_lead_sec = 5.0

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
        self.player_trace = deque(maxlen=260)  # (lap_pct, throttle, brake, clutch)
        self.delta_history = deque(maxlen=120)