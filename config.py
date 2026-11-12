import os
import json

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "hud_layout.json")
FONT_UI = "Segoe UI"
FONT_MONO = "Consolas"

VK_NAMES = {
    0x70: "F1", 0x71: "F2", 0x72: "F3", 0x73: "F4", 0x74: "F5", 0x75: "F6",
    0x76: "F7", 0x77: "F8", 0x78: "F9", 0x79: "F10", 0x7A: "F11", 0x7B: "F12",
    0xC0: "~ (Тильда)", 0x09: "TAB", 0x14: "CAPS", 0x20: "SPACE",
    0x2D: "INSERT", 0x2E: "DELETE", 0x24: "HOME", 0x23: "END",
    0x21: "PGUP", 0x22: "PGDN", 0x6A: "NUM *", 0x6B: "NUM +", 0x6D: "NUM -",
}
for _code in range(0x30, 0x3A):
    VK_NAMES[_code] = chr(_code)
for _code in range(0x41, 0x5B):
    VK_NAMES[_code] = chr(_code)
for _i in range(10):
    VK_NAMES[0x60 + _i] = f"NUM {_i}"


def vk_to_name(vk: int) -> str:
    return VK_NAMES.get(vk, f"KEY_{vk}")


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass