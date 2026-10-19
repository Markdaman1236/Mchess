"""Opening lines in the order Sjeng would play them from its books.

The Mac engine reads nbook.db and the variant .opn files. Those files are
Sjeng's private format. These main lines give the same early moves for
standard chess. Other variants still search.
"""

from __future__ import annotations

from chess_rules import Move, Position

LINES = (
    ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4", "g8f6"),
    ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "c2c3", "g8f6"),
    ("e2e4", "c7c5", "g1f3", "d7d6", "d2d4", "c5d4", "f3d4", "g8f6"),
    ("e2e4", "e7e6", "d2d4", "d7d5", "b1c3", "g8f6"),
    ("e2e4", "c7c6", "d2d4", "d7d5", "b1c3", "d5e4", "c3e4"),
    ("d2d4", "d7d5", "c2c4", "e7e6", "b1c3", "g8f6", "c4d5", "e6d5"),
    ("d2d4", "g8f6", "c2c4", "g7g6", "b1c3", "f8g7"),
    ("d2d4", "g8f6", "c2c4", "e7e6", "g1f3", "d7d5"),
    ("c2c4", "e7e5", "b1c3", "g8f6", "g1f3", "b8c6"),
    ("g1f3", "d7d5", "d2d4", "g8f6", "c2c4"),
    ("e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "g8f6", "e1g1"),
)


def lookup(history: list[str], pos: Position) -> Move | None:
    if pos.variant != 0:
        return None
    played = tuple(history)
    choices = []
    for line in LINES:
        if len(line) <= len(played):
            continue
        if line[: len(played)] != played:
            continue
        choices.append(line[len(played)])
    if not choices:
        return None
    want = choices[0]
    for move in pos.legal_moves():
        if move.uci() == want:
            return move
    return None
