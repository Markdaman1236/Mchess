"""Chess rules reconstructed from Apple Chess 3.18.

Observed in the IDA database of MacOS\\Chess (image base 0x100000000):

- Piece codes in -[MBCBoard resetWithVariant:]: king 1, queen 2, bishop 3,
  knight 4, rook 5, pawn 6 (" kqbnr" in -[MBCBoardView keyDown:]). This
  port swaps king and queen internally; files never store the codes.
  Black is the same code with bit 3 set (value | 8).
  Bit 0x10 means the piece was promoted. Bit 0x20 means it has moved.
- Squares are file + 8*rank with a1 = 0, from -[MBCEngine squareToCoord:].
- Variants written to Sjeng: 0 standard, 1 crazyhouse, 2 suicide, 3 losers.
- Crazyhouse captures flip the piece color into the capturer's hand. A
  promoted piece (bit 0x10) returns to the hand as a pawn.
- Checkmate sets the win for the mover, except variant 3 (losers), where the
  mover loses. A position with no legal move and no check is a draw (outcome
  6), except losers, where the side to move wins.
- Sjeng is driven with xboard commands: xboard / confirm_moves, ?new,
  variant ..., sd <depth>, e2e4, e7e8q, P@e4, go, force, remove, setboard.

-[MBCBoard makeMove:] arms en passant only when fromRank - toRank == 2
(a double step toward rank 1). This port enables en passant in both
directions so a game follows chess rules.
"""

from __future__ import annotations

from dataclasses import dataclass

EMPTY = 0
QUEEN, KING, BISHOP, KNIGHT, ROOK, PAWN = 1, 2, 3, 4, 5, 6
PROMOTED = 0x10
MOVED = 0x20
WHITE = 0
BLACK = 8

STANDARD, CRAZYHOUSE, SUICIDE, LOSERS = 0, 1, 2, 3
VARIANT_NAMES = {
    STANDARD: "Standard",
    CRAZYHOUSE: "Crazyhouse",
    SUICIDE: "Suicide",
    LOSERS: "Losers",
}

PIECE_LETTER = {KING: "K", QUEEN: "Q", ROOK: "R", BISHOP: "B", KNIGHT: "N", PAWN: "P"}
LETTER_PIECE = {v: k for k, v in PIECE_LETTER.items()}


def color_of(piece: int) -> int:
    return piece & 8


def kind_of(piece: int) -> int:
    return piece & 7


def is_piece(piece: int) -> bool:
    return kind_of(piece) != 0


def file_of(sq: int) -> int:
    return sq & 7


def rank_of(sq: int) -> int:
    return sq >> 3


def square_name(sq: int) -> str:
    return "abcdefgh"[file_of(sq)] + "12345678"[rank_of(sq)]


def parse_square(text: str) -> int:
    return "abcdefgh".index(text[0]) + 8 * "12345678".index(text[1])


@dataclass
class Move:
    kind: int  # 1 board move, 2 crazyhouse drop
    fr: int
    to: int
    piece: int
    promo: int = 0
    capture: int = 0
    castle: int = 0  # 1 queenside, 2 kingside, matching makeMove:
    ep: bool = False

    def uci(self) -> str:
        if self.kind == 2:
            return f"{PIECE_LETTER[kind_of(self.piece)]}@{square_name(self.to)}"
        text = square_name(self.fr) + square_name(self.to)
        if self.promo:
            text += PIECE_LETTER[self.promo].lower()
        return text


class Position:
    def __init__(self, variant: int = STANDARD):
        self.variant = variant
        self.board = [EMPTY] * 64
        self.hand = [0] * 16
        self.ep = -1
        self.halfmove = 0
        self.fullmove = 1
        self.side = WHITE
        self._stack: list[tuple] = []
        self.reset()

    def reset(self) -> None:
        self.board = [EMPTY] * 64
        back = [ROOK, KNIGHT, BISHOP, QUEEN, KING, BISHOP, KNIGHT, ROOK]
        for i, kind in enumerate(back):
            self.board[i] = kind
            self.board[56 + i] = kind | BLACK
            self.board[8 + i] = PAWN
            self.board[48 + i] = PAWN | BLACK
        self.hand = [0] * 16
        self.ep = -1
        self.halfmove = 0
        self.fullmove = 1
        self.side = WHITE
        self._stack.clear()

    def clone(self) -> "Position":
        other = Position(self.variant)
        other.board = self.board[:]
        other.hand = self.hand[:]
        other.ep = self.ep
        other.halfmove = self.halfmove
        other.fullmove = self.fullmove
        other.side = self.side
        other._stack.clear()
        return other

    def push(self, move: Move) -> None:
        self._stack.append(
            (self.board[:], self.hand[:], self.ep, self.halfmove, self.fullmove, self.side)
        )
        self._apply(move)

    def pop(self) -> None:
        self.board, self.hand, self.ep, self.halfmove, self.fullmove, self.side = self._stack.pop()

    def _apply(self, move: Move) -> None:
        us = self.side
        them = us ^ 8
        if move.kind == 2:
            code = kind_of(move.piece) | us
            self.hand[code] -= 1
            self.board[move.to] = code | MOVED
            self.ep = -1
            self.halfmove = 0
        else:
            placed = move.piece
            if move.promo:
                placed = (move.promo | color_of(move.piece) | PROMOTED)
            placed |= MOVED
            self.board[move.fr] = EMPTY
            captured = move.capture
            if move.ep:
                cap_sq = move.to - 8 if us == WHITE else move.to + 8
                captured = self.board[cap_sq]
                self.board[cap_sq] = EMPTY
            if captured and self.variant == CRAZYHOUSE:
                returned = (captured & 0xDF) ^ 8
                if captured & PROMOTED:
                    returned = (returned & 8) | PAWN
                self.hand[kind_of(returned) | color_of(returned)] += 1
            self.board[move.to] = placed
            if move.castle == 2:
                rook_fr = (move.to & ~7) + 7
                rook_to = (move.to & ~7) + 5
                self.board[rook_to] = self.board[rook_fr] | MOVED
                self.board[rook_fr] = EMPTY
            elif move.castle == 1:
                rook_fr = move.to & ~7
                rook_to = rook_fr + 3
                self.board[rook_to] = self.board[rook_fr] | MOVED
                self.board[rook_fr] = EMPTY
            pawnish = kind_of(move.piece) == PAWN
            if captured or pawnish:
                self.halfmove = 0
            else:
                self.halfmove += 1
            if pawnish and abs(move.to - move.fr) == 16:
                self.ep = (move.fr + move.to) // 2
            else:
                self.ep = -1
        if us == BLACK:
            self.fullmove += 1
        self.side = them

    def king_square(self, side: int) -> int | None:
        want = KING | side
        for sq, piece in enumerate(self.board):
            if (piece & 0x0F) == want or (kind_of(piece) == KING and color_of(piece) == side):
                if kind_of(piece) == KING and color_of(piece) == side:
                    return sq
        return None

    def attacked(self, sq: int, by: int) -> bool:
        f = file_of(sq)
        r = rank_of(sq)
        pawn_rank = r - 1 if by == WHITE else r + 1
        if 0 <= pawn_rank < 8:
            for df in (-1, 1):
                ff = f + df
                if 0 <= ff < 8 and self._is(pawn_rank * 8 + ff, PAWN, by):
                    return True
        for df, dr in ((1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)):
            ff, rr = f + df, r + dr
            if 0 <= ff < 8 and 0 <= rr < 8 and self._is(rr * 8 + ff, KNIGHT, by):
                return True
        for df, dr in ((1, 1), (1, -1), (-1, 1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)):
            ff, rr = f + df, r + dr
            if 0 <= ff < 8 and 0 <= rr < 8 and self._is(rr * 8 + ff, KING, by):
                return True
        if self._ray(sq, ((1, 1), (1, -1), (-1, 1), (-1, -1)), by, BISHOP):
            return True
        if self._ray(sq, ((1, 0), (-1, 0), (0, 1), (0, -1)), by, ROOK):
            return True
        return False

    def _is(self, sq: int, kind: int, by: int) -> bool:
        piece = self.board[sq]
        return kind_of(piece) == kind and color_of(piece) == by

    def _ray(self, sq: int, deltas, by: int, slider: int) -> bool:
        f0, r0 = file_of(sq), rank_of(sq)
        for df, dr in deltas:
            f, r = f0 + df, r0 + dr
            while 0 <= f < 8 and 0 <= r < 8:
                piece = self.board[r * 8 + f]
                if piece:
                    if color_of(piece) == by and kind_of(piece) in (slider, QUEEN):
                        return True
                    break
                f += df
                r += dr
        return False

    def in_check(self, side: int) -> bool:
        king = self.king_square(side)
        if king is None:
            return False
        return self.attacked(king, side ^ 8)

    def piece_count(self, side: int) -> int:
        return sum(1 for piece in self.board if piece and color_of(piece) == side)

    def legal_moves(self) -> list[Move]:
        moves = self._pseudo()
        if self.variant == SUICIDE:
            captures = [m for m in moves if m.capture or m.ep]
            return captures or moves
        legal = []
        us = self.side
        for move in moves:
            self.push(move)
            if not self.in_check(us):
                legal.append(move)
            self.pop()
        if self.variant == LOSERS:
            # Apple's move generator leaves this to Sjeng, which rejects a
            # quiet move in losers while a legal capture exists.
            captures = [m for m in legal if m.capture or m.ep]
            return captures or legal
        return legal

    def _pseudo(self) -> list[Move]:
        moves: list[Move] = []
        us = self.side
        them = us ^ 8
        forward = 8 if us == WHITE else -8
        start_rank = 1 if us == WHITE else 6
        last_rank = 7 if us == WHITE else 0
        for sq, piece in enumerate(self.board):
            if not piece or color_of(piece) != us:
                continue
            kind = kind_of(piece)
            if kind == PAWN:
                self._pawn_moves(moves, sq, piece, forward, start_rank, last_rank, them)
            elif kind == KNIGHT:
                self._step_moves(moves, sq, piece, ((1, 2), (2, 1), (-1, 2), (-2, 1),
                                                    (1, -2), (2, -1), (-1, -2), (-2, -1)), them)
            elif kind == BISHOP:
                self._slide_moves(moves, sq, piece, ((1, 1), (1, -1), (-1, 1), (-1, -1)), them)
            elif kind == ROOK:
                self._slide_moves(moves, sq, piece, ((1, 0), (-1, 0), (0, 1), (0, -1)), them)
            elif kind == QUEEN:
                self._slide_moves(moves, sq, piece, ((1, 1), (1, -1), (-1, 1), (-1, -1),
                                                     (1, 0), (-1, 0), (0, 1), (0, -1)), them)
            elif kind == KING:
                self._step_moves(moves, sq, piece, ((1, 0), (-1, 0), (0, 1), (0, -1),
                                                    (1, 1), (1, -1), (-1, 1), (-1, -1)), them)
                self._castle_moves(moves, sq, piece)
        if self.variant == CRAZYHOUSE:
            self._drop_moves(moves, us)
        return moves

    def _add_pawn_promo(self, moves: list[Move], fr: int, to: int, piece: int, capture: int, ep: bool) -> None:
        if rank_of(to) in (0, 7):
            # -[MBCBoardView mouseUp:] cycles the promotion through the king
            # only in suicide, where a pawn may become a king.
            kinds = (QUEEN, ROOK, BISHOP, KNIGHT, KING) if self.variant == SUICIDE else (QUEEN, ROOK, BISHOP, KNIGHT)
            for promo in kinds:
                moves.append(Move(1, fr, to, piece, promo, capture, 0, ep))
        else:
            moves.append(Move(1, fr, to, piece, 0, capture, 0, ep))

    def _pawn_moves(self, moves, sq, piece, forward, start_rank, last_rank, them) -> None:
        f = file_of(sq)
        one = sq + forward
        if 0 <= one < 64 and self.board[one] == EMPTY:
            self._add_pawn_promo(moves, sq, one, piece, 0, False)
            if rank_of(sq) == start_rank:
                two = sq + 2 * forward
                if self.board[two] == EMPTY:
                    moves.append(Move(1, sq, two, piece, 0, 0, 0, False))
        for df in (-1, 1):
            ff = f + df
            if not 0 <= ff < 8:
                continue
            to = sq + forward + df
            if not 0 <= to < 64:
                continue
            target = self.board[to]
            if target and color_of(target) == them:
                self._add_pawn_promo(moves, sq, to, piece, target, False)
            elif to == self.ep:
                cap_sq = to - forward
                captured = self.board[cap_sq]
                if captured and color_of(captured) == them and kind_of(captured) == PAWN:
                    moves.append(Move(1, sq, to, piece, 0, captured, 0, True))

    def _step_moves(self, moves, sq, piece, deltas, them) -> None:
        f0, r0 = file_of(sq), rank_of(sq)
        for df, dr in deltas:
            f, r = f0 + df, r0 + dr
            if not (0 <= f < 8 and 0 <= r < 8):
                continue
            to = r * 8 + f
            target = self.board[to]
            if target and color_of(target) != them:
                continue
            moves.append(Move(1, sq, to, piece, 0, target, 0, False))

    def _slide_moves(self, moves, sq, piece, deltas, them) -> None:
        f0, r0 = file_of(sq), rank_of(sq)
        for df, dr in deltas:
            f, r = f0 + df, r0 + dr
            while 0 <= f < 8 and 0 <= r < 8:
                to = r * 8 + f
                target = self.board[to]
                if target:
                    if color_of(target) == them:
                        moves.append(Move(1, sq, to, piece, 0, target, 0, False))
                    break
                moves.append(Move(1, sq, to, piece, 0, 0, 0, False))
                f += df
                r += dr

    def _castle_moves(self, moves, sq, piece) -> None:
        if piece & MOVED:
            return
        us = color_of(piece)
        home = 4 if us == WHITE else 60
        if sq != home:
            return
        rank = sq & ~7
        if self._rook_ready(rank + 7) and self.board[rank + 5] == EMPTY and self.board[rank + 6] == EMPTY:
            if self.variant == SUICIDE or not (
                self.attacked(sq, us ^ 8) or self.attacked(rank + 5, us ^ 8) or self.attacked(rank + 6, us ^ 8)
            ):
                moves.append(Move(1, sq, rank + 6, piece, 0, 0, 2, False))
        if (
            self._rook_ready(rank)
            and self.board[rank + 1] == EMPTY
            and self.board[rank + 2] == EMPTY
            and self.board[rank + 3] == EMPTY
        ):
            if self.variant == SUICIDE or not (
                self.attacked(sq, us ^ 8) or self.attacked(rank + 3, us ^ 8) or self.attacked(rank + 2, us ^ 8)
            ):
                moves.append(Move(1, sq, rank + 2, piece, 0, 0, 1, False))

    def _rook_ready(self, sq: int) -> bool:
        piece = self.board[sq]
        return kind_of(piece) == ROOK and color_of(piece) == self.side and not (piece & MOVED)

    def _drop_moves(self, moves, us) -> None:
        kinds = [k for k in (QUEEN, ROOK, BISHOP, KNIGHT, PAWN) if self.hand[k | us]]
        if not kinds:
            return
        for to, piece in enumerate(self.board):
            if piece:
                continue
            for kind in kinds:
                if kind == PAWN and rank_of(to) in (0, 7):
                    continue
                moves.append(Move(2, -1, to, kind | us, 0, 0, 0, False))

    def outcome(self) -> str | None:
        """Return '1-0', '0-1', '1/2-1/2', or None when the game continues.

        Codes follow -[MBCBoard outcome]: checkmate rewards the mover except
        in losers, and a no-move position that is not check is a draw except
        in losers, where the side that cannot move wins.
        """
        if self.variant == SUICIDE:
            if self.piece_count(self.side ^ 8) == 0:
                return "1-0" if self.side == BLACK else "0-1"
            if self.piece_count(self.side) == 0:
                return "1-0" if self.side == WHITE else "0-1"
            if not self.legal_moves():
                return "1/2-1/2"
            return None
        if self.variant == LOSERS:
            # -[MBCBoard outcome]: after a capture leaves a side with only
            # its king, that side wins.
            for side in (self.side, self.side ^ 8):
                if self.piece_count(side) == 1:
                    return "1-0" if side == WHITE else "0-1"
        moves = self.legal_moves()
        if self.variant == LOSERS:
            if not moves:
                return "1-0" if self.side == WHITE else "0-1"
            return None
        if not moves:
            if self.in_check(self.side):
                return "0-1" if self.side == WHITE else "1-0"
            return "1/2-1/2"
        if self.halfmove >= 100:
            return "1/2-1/2"
        if self._repetition() >= 3:
            return "1/2-1/2"
        if self._insufficient():
            return "1/2-1/2"
        return None

    def _repetition(self) -> int:
        key = self._key()
        count = 1
        board, hand, ep, half, full, side = self.board, self.hand, self.ep, self.halfmove, self.fullmove, self.side
        for snap in reversed(self._stack):
            self.board, self.hand, self.ep, self.halfmove, self.fullmove, self.side = snap
            if self._key() == key:
                count += 1
        self.board, self.hand, self.ep, self.halfmove, self.fullmove, self.side = board, hand, ep, half, full, side
        return count

    def _key(self) -> tuple:
        return (tuple(self.board), tuple(self.hand), self.side, self.ep, self._castle_key())

    def _castle_key(self) -> tuple:
        rights = []
        for sq in (4, 60, 0, 7, 56, 63):
            piece = self.board[sq]
            rights.append(piece if piece and not (piece & MOVED) else 0)
        return tuple(rights)

    def _insufficient(self) -> bool:
        if self.variant != STANDARD or any(self.hand):
            return False
        whites, blacks = [], []
        for sq, piece in enumerate(self.board):
            kind = kind_of(piece)
            if kind == EMPTY or kind == KING:
                continue
            if kind != BISHOP and kind != KNIGHT:
                return False
            (whites if color_of(piece) == WHITE else blacks).append((kind, sq))
        if whites and blacks:
            return False
        minors = whites or blacks
        if len(minors) > 1:
            return False
        return True

    def san(self, move: Move) -> str:
        if move.castle == 2:
            text = "O-O"
        elif move.castle == 1:
            text = "O-O-O"
        elif move.kind == 2:
            letter = PIECE_LETTER[kind_of(move.piece)]
            text = f"{letter}@{square_name(move.to)}"
        else:
            kind = kind_of(move.piece)
            dest = square_name(move.to)
            if kind == PAWN:
                text = square_name(move.fr)[0] + "x" + dest if (move.capture or move.ep) else dest
            else:
                text = PIECE_LETTER[kind] + self._disambiguate(move) + ("x" if move.capture else "") + dest
            if move.promo:
                text += "=" + PIECE_LETTER[move.promo]
        self.push(move)
        result = self.outcome()
        check = self.variant != SUICIDE and self.in_check(self.side)
        self.pop()
        if result in ("1-0", "0-1") and self.variant != SUICIDE:
            if self.variant == LOSERS and result == ("1-0" if self.side == WHITE else "0-1"):
                text += "#"
            elif self.variant != LOSERS:
                text += "#"
        elif check:
            text += "+"
        return text

    def _disambiguate(self, move: Move) -> str:
        kind = kind_of(move.piece)
        mates = []
        for other in self.legal_moves():
            if other is move or other.to != move.to or other.kind != 1:
                continue
            if kind_of(other.piece) == kind and other.fr != move.fr:
                mates.append(other)
        if not mates:
            return ""
        file_clash = any(file_of(m.fr) == file_of(move.fr) for m in mates)
        rank_clash = any(rank_of(m.fr) == rank_of(move.fr) for m in mates)
        if not file_clash:
            return "abcdefgh"[file_of(move.fr)]
        if not rank_clash:
            return "12345678"[rank_of(move.fr)]
        return square_name(move.fr)

    def fen(self) -> str:
        rows = []
        for rank in range(7, -1, -1):
            empty = 0
            row = ""
            for file in range(8):
                piece = self.board[rank * 8 + file]
                if not piece:
                    empty += 1
                    continue
                if empty:
                    row += str(empty)
                    empty = 0
                letter = PIECE_LETTER[kind_of(piece)]
                row += letter if color_of(piece) == WHITE else letter.lower()
            if empty:
                row += str(empty)
            rows.append(row)
        side = "w" if self.side == WHITE else "b"
        return "/".join(rows) + f" {side} - - {self.halfmove} {self.fullmove}"


def depth_for_level(level: int) -> int:
    """Match -[MBCEngine movesForTime:]. Levels 1-10 are the search depth.

    Level 11 sends sd 20 and level 12 sends sd 30. Other values send sd 0.
    """
    if level == 12:
        return 30
    if level == 11:
        return 20
    if 1 <= level <= 10:
        return level
    return 1


def seconds_for_level(level: int) -> int:
    """+[MBCEngine secondsForTime:]: levels 5-12 index {2, 4, ... 256}."""
    if 5 <= level <= 12:
        return 2 ** (level - 4)
    return 0


def level_text(level: int) -> str:
    """-[MBCBoardWin engineStrengthForTime:] with the Localizable strings."""
    if level == 1:
        return "Computer thinks 1 move ahead"
    if level in (2, 3):
        return f"Computer thinks {level} moves ahead"
    if level == 4:
        return "Computer thinks 1 second per move"
    return f"Computer thinks {seconds_for_level(level)} seconds per move"


def initial_engine_lines(variant: int, level: int) -> list[str]:
    """Lines -[MBCEngine initGame:] and setSearchTime: write to sjeng."""
    lines = ["xboard", "confirm_moves", "?new"]
    if variant == CRAZYHOUSE:
        lines.append("variant crazyhouse")
    elif variant == SUICIDE:
        lines.append("variant suicide")
    elif variant == LOSERS:
        lines.append("variant losers")
    lines.append(f"sd {depth_for_level(level)}")
    return lines
