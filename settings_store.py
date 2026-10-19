"""Settings that match the keys in the original Defaults.plist."""

from __future__ import annotations

import getpass
import json
from pathlib import Path

PATH = Path.home() / "AppData" / "Roaming" / "Chess" / "settings.json"


def _user_name() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "Player"


DEFAULTS = {
    "board_angle": 60,          # MBCBoardAngle
    "board_spin": 180,          # MBCBoardSpin
    "board_style": "Wood",      # MBCBoardStyle
    "piece_style": "Wood",      # MBCPieceStyle
    "search_time": 1,           # MBCSearchTime
    "show_move_in_title": False,  # MBCShowMoveInTitle
    "speak_moves": True,        # MBCSpeakMoves
    "speak_human": False,       # MBCSpeakHumanMoves
    "listen": True,             # MBCListenForMoves
    "show_log": False,          # MBCShowGameLog
    "players": 1,               # MBCNewGamePlayers: 0 HvH, 1 HvC, 2 CvH, 3 CvC
    "variant": 0,               # MBCNewGameVariant
    "primary_voice": "",        # MBCDefaultVoice; empty is the system voice
    "alternate_voice": "",      # MBCAlternateVoice; empty is a female voice
    "human_name": _user_name(),  # MBCHumanName
    "human_name2": _user_name(),  # MBCHumanName2
    "city": "",                 # MBCGameCity
    "country": "",              # MBCGameCountry
    "event": "Casual Game",     # MBCGameEvent
    "recent": [],               # Open Recent
}


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        saved = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return data
    if isinstance(saved, dict):
        data.update({key: saved[key] for key in DEFAULTS if key in saved})
        if "players" not in saved and "mode" in saved:
            # Settings written before the New Game sheet used MBCNewGamePlayers.
            human_white = saved.get("human", "white") == "white"
            data["players"] = {"hh": 0, "cc": 3}.get(saved["mode"], 1 if human_white else 2)
    return data


def save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    stored = {key: data.get(key, DEFAULTS[key]) for key in DEFAULTS}
    PATH.write_text(json.dumps(stored, indent=2), encoding="utf-8")
