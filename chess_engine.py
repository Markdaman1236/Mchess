"""Search used by the Windows port.

Apple Chess does not search itself. -[MBCEngine setSearchTime:] sends
``sd N`` to the bundled Sjeng process, and movesForTime: uses N for levels
1 through 10, 20 for level 11, and 30 for level 12. Sjeng is a Mach-O
binary and does not run on Windows, so this module is a small alpha-beta
player with the same level numbers. The depth limit is that ``sd`` value,
and time_for_level caps each move at a few seconds. Sjeng is still much
stronger at the same settings.
"""

from __future__ import annotations

import random
import time

from chess_rules import (
    BISHOP,
    BLACK,
    KING,
    KNIGHT,
    LOSERS,
    PAWN,
    QUEEN,
    ROOK,
    STANDARD,
    SUICIDE,
    WHITE,
    Move,
    Position,
    depth_for_level,
    kind_of,

    color_of,
)

from sjeng_engine import ENGINE as SJENG

MATE = 100_000
MAX_PLY = 5

_VALUE = {QUEEN: 900, ROOK: 500, BISHOP: 330, KNIGHT: 320, PAWN: 100, KING: 0}

# a1 is index 0. Values are from White's point of view.
_PST = {
    PAWN: [
        0, 0, 0, 0, 0, 0, 0, 0,
        5, 10, 10, -20, -20, 10, 10, 5,
        5, -5, -10, 0, 0, -10, -5, 5,
        0, 0, 0, 20, 20, 0, 0, 0,
        5, 5, 10, 25, 25, 10, 5, 5,
        10, 10, 20, 30, 30, 20, 10, 10,
        50, 50, 50, 50, 50, 50, 50, 50,
        0, 0, 0, 0, 0, 0, 0, 0,
    ],
    KNIGHT: [
        -50, -40, -30, -30, -30, -30, -40, -50,
        -40, -20, 0, 0, 0, 0, -20, -40,
        -30, 0, 10, 15, 15, 10, 0, -30,
        -30, 5, 15, 20, 20, 15, 5, -30,
        -30, 0, 15, 20, 20, 15, 0, -30,
        -30, 5, 10, 15, 15, 10, 5, -30,
        -40, -20, 0, 5, 5, 0, -20, -40,
        -50, -40, -30, -30, -30, -30, -40, -50,
    ],
    BISHOP: [
        -20, -10, -10, -10, -10, -10, -10, -20,
        -10, 0, 0, 0, 0, 0, 0, -10,
        -10, 0, 5, 10, 10, 5, 0, -10,
        -10, 5, 5, 10, 10, 5, 5, -10,
        -10, 0, 10, 10, 10, 10, 0, -10,
        -10, 10, 10, 10, 10, 10, 10, -10,
        -10, 5, 0, 0, 0, 0, 5, -10,
        -20, -10, -10, -10, -10, -10, -10, -20,
    ],
    ROOK: [0] * 64,
    QUEEN: [0] * 64,
    KING: [
        20, 30, 10, 0, 0, 10, 30, 20,
        20, 20, 0, 0, 0, 0, 20, 20,
        -10, -20, -20, -20, -20, -20, -20, -10,
        -20, -30, -30, -40, -40, -30, -30, -20,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
        -30, -40, -40, -50, -50, -40, -40, -30,
    ],
}


def _score_white(pos: Position) -> int:
    score = 0
    losing = pos.variant in (SUICIDE, LOSERS)
    for sq, piece in enumerate(pos.board):
        kind = kind_of(piece)
        if not kind:
            continue
        value = _VALUE[kind] + _PST.get(kind, _PST[QUEEN])[sq if color_of(piece) == WHITE else sq ^ 56]
        if losing and kind != KING:
            value = -value
        score += value if color_of(piece) == WHITE else -value
    if pos.variant == 1:
        for code, count in enumerate(pos.hand):
            if not count:
                continue
            value = _VALUE[kind_of(code)] - 10
            score += count * value if color_of(code) == WHITE else -count * value
    return score


def _terminal(pos: Position, ply: int) -> int | None:
    result = pos.outcome()
    if result is None:
        return None
    if result == "1/2-1/2":
        return 0
    white_wins = result == "1-0"
    stm_wins = white_wins if pos.side == WHITE else not white_wins
    return (MATE - ply) if stm_wins else (-MATE + ply)


def _order(moves: list[Move]) -> list[Move]:
    def key(move: Move) -> int:
        return (_VALUE.get(kind_of(move.capture), 0) if move.capture else 0) + (800 if move.promo == QUEEN else 0)
    return sorted(moves, key=key, reverse=True)


def _evaluate(pos: Position) -> int:
    score = _score_white(pos)
    return score if pos.side == WHITE else -score


def _negamax(pos: Position, depth: int, ply: int, alpha: int, beta: int, deadline: float, state: dict) -> int:
    state["nodes"] += 1
    if state["nodes"] & 63 == 0 and time.perf_counter() >= deadline:
        state["stopped"] = True
    if state["stopped"]:
        return _evaluate(pos)
    terminal = _terminal(pos, ply)
    if terminal is not None:
        return terminal
    if depth == 0:
        return _quiesce(pos, ply, alpha, beta, deadline, state)
    best = -MATE
    for move in _order(pos.legal_moves()):
        pos.push(move)
        score = -_negamax(pos, depth - 1, ply + 1, -beta, -alpha, deadline, state)
        pos.pop()
        if score > best:
            best = score
        if score > alpha:
            alpha = score
        if alpha >= beta:
            break
    return best


def _quiesce(pos: Position, ply: int, alpha: int, beta: int, deadline: float, state: dict) -> int:
    if pos.variant in (SUICIDE, LOSERS):
        return _evaluate(pos)
    stand = _evaluate(pos)
    if stand >= beta:
        return stand
    if stand > alpha:
        alpha = stand
    for move in _order(pos.legal_moves()):
        if not move.capture and not move.promo and not move.ep:
            continue
        pos.push(move)
        score = -_quiesce(pos, ply + 1, -beta, -alpha, deadline, state)
        pos.pop()
        if state["stopped"]:
            return stand
        if score >= beta:
            return score
        if score > alpha:
            alpha = score
    return alpha


def time_for_level(level: int) -> float:
    """Most seconds a move may take.

    Sjeng reaches its ``sd`` depth long before the time the label shows,
    so the original answers in a second or two even at "64 seconds per
    move". This search is far slower and would always use the full label
    time, which looks like a hang. Levels 1-3 stop at their depth (capped
    at 5 s); level 4 gets 1 s and each level above adds 1 s, up to 9 s.
    """
    if level <= 3:
        return 5.0
    return float(min(level - 3, 9))


def choose_move(
    pos: Position,
    level: int,
    rng: random.Random | None = None,
    history: list[str] | None = None,
) -> tuple[Move | None, int, Move | None]:
    """Pick a move and the reply it expects.

    The score is from the side to move, in centipawns. The expected reply
    stands in for -[MBCEngine lastPonder], which Show Hint displays.

    Sjeng (sjeng_engine.py) plays when its Windows build is present, with
    the original's "sd" depth for the level, and returns no expected reply.
    The Python search below is the fallback.
    """
    if history is not None and SJENG.available():
        text = SJENG.best_move(pos.variant, depth_for_level(level), history)
        move = match_engine_move(pos, text)
        if move is not None:
            return move, 0, None
    move, score = _search(pos, level, rng or random.Random(), history or [])
    ponder = None
    if move is not None:
        after = pos.clone()
        after.push(move)
        if after.outcome() is None:
            ponder, _ = _search(after, min(level, 3), random.Random(0), (history or []) + [move.uci()], budget=0.5)
    return move, score, ponder


def match_engine_move(pos: Position, text: str | None) -> Move | None:
    """Turn engine text such as "e7e5", "e7e8q" or "n@f3" into a legal move."""
    if not text:
        return None
    want = text.strip().lower()
    for move in pos.legal_moves():
        if move.uci().lower() == want:
            return move
    return None


def _search(
    pos: Position,
    level: int,
    rng: random.Random,
    history: list[str],
    budget: float | None = None,
) -> tuple[Move | None, int]:
    from opening_book import lookup

    booked = lookup(history, pos)
    if booked is not None:
        return booked, 0
    moves = pos.legal_moves()
    if not moves:
        return None, 0
    sd = max(depth_for_level(level), 1)
    depth_cap = sd
    if budget is None:
        budget = time_for_level(level)
    deadline = time.perf_counter() + budget
    work = pos.clone()
    best_move = moves[0]
    best_score = 0
    for depth in range(1, depth_cap + 1):
        state = {"nodes": 0, "stopped": False}
        ranked: list[tuple[int, Move]] = []
        alpha, beta = -MATE, MATE
        window_best = -MATE
        window_move = None
        for move in _order(moves):
            work.push(move)
            score = -_negamax(work, depth - 1, 1, -beta, -alpha, deadline, state)
            work.pop()
            ranked.append((score, move))
            if score > window_best:
                window_best = score
                window_move = move
            if score > alpha:
                alpha = score
        if window_move is None:
            break
        if not state["stopped"]:
            near = [move for score, move in ranked if score >= window_best - 8]
            best_move = rng.choice(near)
            best_score = window_best
        elif depth == 1:
            best_move = window_move
            best_score = window_best
        if state["stopped"] or abs(window_best) > MATE - 500:
            break
    return best_move, best_score
