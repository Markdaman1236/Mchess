"""Rule checks against the Apple Chess piece codes and known perft counts."""

from chess_rules import (
    BISHOP,
    BLACK,
    CRAZYHOUSE,
    EMPTY,
    KING,
    KNIGHT,
    LETTER_PIECE,
    LOSERS,
    MOVED,
    PAWN,
    PROMOTED,
    QUEEN,
    ROOK,
    STANDARD,
    SUICIDE,
    WHITE,
    Position,
    depth_for_level,
    initial_engine_lines,
    kind_of,
    parse_square,
    color_of,
)


def load_fen(fen: str, variant: int = STANDARD) -> Position:
    board, side, castling, ep, half, full = fen.split()
    pos = Position(variant)
    pos.board = [EMPTY] * 64
    for r, row in enumerate(board.split("/")):
        rank = 7 - r
        file = 0
        for ch in row:
            if ch.isdigit():
                file += int(ch)
                continue
            piece = LETTER_PIECE[ch.upper()]
            if ch.islower():
                piece |= BLACK
            pos.board[rank * 8 + file] = piece
            file += 1
    for sq, piece in enumerate(pos.board):
        if kind_of(piece) in (KING, ROOK):
            pos.board[sq] |= MOVED
    def clear(sq: int) -> None:
        if pos.board[sq]:
            pos.board[sq] &= ~MOVED
    if "K" in castling:
        clear(4)
        clear(7)
    if "Q" in castling:
        clear(4)
        clear(0)
    if "k" in castling:
        clear(60)
        clear(63)
    if "q" in castling:
        clear(60)
        clear(56)
    pos.side = WHITE if side == "w" else BLACK
    pos.ep = -1 if ep == "-" else parse_square(ep)
    pos.halfmove = int(half)
    pos.fullmove = int(full)
    return pos


def perft(pos: Position, depth: int) -> int:
    if depth == 0:
        return 1
    total = 0
    for move in pos.legal_moves():
        pos.push(move)
        total += perft(pos, depth - 1)
        pos.pop()
    return total


def test_start_perft():
    pos = Position()
    assert perft(pos, 1) == 20
    assert perft(pos, 2) == 400
    assert perft(pos, 3) == 8902


def test_kiwipete():
    fen = "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1"
    pos = load_fen(fen)
    assert perft(pos, 1) == 48
    assert perft(pos, 2) == 2039


def test_en_passant_both_directions():
    white = load_fen("8/8/8/3pP3/8/8/8/4K2k w - d6 0 1")
    assert any(move.ep and move.to == parse_square("d6") for move in white.legal_moves())
    black = load_fen("4k2K/8/8/8/3pP3/8/8/8 b - e3 0 1")
    assert any(move.ep and move.to == parse_square("e3") for move in black.legal_moves())


def test_castling_rights():
    pos = load_fen("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    castles = {move.castle for move in pos.legal_moves()}
    assert castles >= {1, 2}


def test_checkmate_and_stalemate():
    mate = load_fen("k7/1Q6/2K5/8/8/8/8/8 b - - 0 1")
    assert mate.in_check(BLACK)
    assert mate.outcome() == "1-0"
    losers = load_fen("k7/1Q6/2K5/8/8/8/8/8 b - - 0 1", LOSERS)
    assert losers.outcome() == "0-1"
    stale = load_fen("k7/8/1QK5/8/8/8/8/8 b - - 0 1")
    assert stale.outcome() == "1/2-1/2"
    losers_stale = load_fen("k7/8/1QK5/8/8/8/8/8 b - - 0 1", LOSERS)
    assert losers_stale.outcome() == "0-1"


def test_crazyhouse_hand_and_promoted_pawn():
    pos = Position(CRAZYHOUSE)
    pos.board = [EMPTY] * 64
    pos.board[4] = KING
    pos.board[60] = KING | BLACK
    pos.board[28] = PAWN | PROMOTED | BLACK  # e4, promoted
    pos.board[21] = PAWN  # f3 captures on e4
    captures = [m for m in pos.legal_moves() if m.to == 28 and m.capture]
    assert captures
    pos.push(captures[0])
    assert pos.hand[PAWN | WHITE] == 1
    assert pos.hand[QUEEN | WHITE] == 0
    pos.side = WHITE
    assert any(m.kind == 2 and kind_of(m.piece) == PAWN for m in pos.legal_moves())


def test_suicide_must_capture():
    pos = load_fen("4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1", SUICIDE)
    moves = pos.legal_moves()
    assert moves
    assert all(m.capture or m.ep for m in moves)


def test_engine_commands_match_binary():
    assert depth_for_level(1) == 1
    assert depth_for_level(10) == 10
    assert depth_for_level(11) == 20
    assert depth_for_level(12) == 30
    lines = initial_engine_lines(CRAZYHOUSE, 4)
    assert lines[:4] == ["xboard", "confirm_moves", "?new", "variant crazyhouse"]
    assert lines[-1] == "sd 4"


def test_engine_plays_a_legal_move():
    from chess_engine import choose_move
    pos = Position()
    move, _score, _ponder = choose_move(pos, 2)
    assert move is not None
    assert move in pos.legal_moves()


def test_square_zero_is_a1():
    pos = Position()
    assert kind_of(pos.board[0]) == ROOK and color_of(pos.board[0]) == WHITE
    assert kind_of(pos.board[3]) == QUEEN
    assert kind_of(pos.board[4]) == KING
    assert kind_of(pos.board[8]) == PAWN
    assert color_of(pos.board[56]) == BLACK


if __name__ == "__main__":
    test_square_zero_is_a1()
    test_engine_commands_match_binary()
    test_en_passant_both_directions()
    test_castling_rights()
    test_checkmate_and_stalemate()
    test_crazyhouse_hand_and_promoted_pawn()
    test_suicide_must_capture()
    test_engine_plays_a_legal_move()
    test_start_perft()
    test_kiwipete()
    print("all rule checks passed")
