"""Move text, speech text, PGN and .game files, following Apple Chess 3.18.

- Spoken moves are -[MBCBoard stringFromMove:withLocalization:] with the
  English Spoken.loctable: "pawn E 2 to E 4", "knight G 1 takes F 3",
  "castle king side", "drop knight at C 7", "..., promoting to queen".
- The move list and window title use -[MBCMove localizedText]: origin,
  operation and destination, e.g. " e2-e4", "Ng1-f3", "Nf3×e5", "0 - 0".
- PGN is -[MBCDocument saveMovesToFile:]: pgnHeader, optional Variant and
  WhiteType/BlackType tags, then -[MBCBoard saveMovesTo:] movetext.
- A .game file is the plist from -[MBCDocument saveGameToDict]: the game
  properties plus Variant, WhiteType, BlackType, Position, Holding, Moves.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

from chess_rules import (
    BISHOP,
    BLACK,
    CRAZYHOUSE,
    KING,
    KNIGHT,
    MOVED,
    PAWN,
    QUEEN,
    ROOK,
    STANDARD,
    SUICIDE,
    WHITE,
    Move,
    Position,
    VARIANT_NAMES,
    color_of,
    file_of,
    kind_of,
    parse_square,
    rank_of,
)

PROGRAM_NAME = "Apple Chess 3.18"
# off_1000571B0, the Variant value in .game files and PGN tags.
VARIANT_KEYS = {STANDARD: "normal", CRAZYHOUSE: "crazyhouse", SUICIDE: "suicide", 3: "losers"}

PIECE_WORD = {PAWN: "pawn", KNIGHT: "knight", BISHOP: "bishop", ROOK: "rook", QUEEN: "queen", KING: "king"}
LETTER = {KING: "K", QUEEN: "Q", BISHOP: "B", KNIGHT: "N", ROOK: "R", PAWN: "P"}


def check_state(pos: Position, move: Move) -> tuple[bool, bool]:
    """(check, mate) after the move. makeMove: skips this in suicide."""
    if pos.variant == SUICIDE:
        return False, False
    pos.push(move)
    check = pos.in_check(pos.side)
    mate = check and not pos.legal_moves()
    pos.pop()
    return check, mate


def is_capture(move: Move) -> bool:
    return bool(move.capture or move.ep)


def spoken_move(pos: Position, move: Move) -> str:
    """Spoken.loctable move_fmt, cmove_fmt, promo_fmt, cpromo_fmt, drop_fmt."""
    if move.castle == 2:
        return "castle king side"
    if move.castle == 1:
        return "castle queen side"
    to_file = "ABCDEFGH"[file_of(move.to)]
    to_rank = str(rank_of(move.to) + 1)
    if move.kind == 2:
        return f"drop {PIECE_WORD[kind_of(move.piece)]} at {to_file} {to_rank}"
    piece = PIECE_WORD[kind_of(move.piece)]
    from_file = "ABCDEFGH"[file_of(move.fr)]
    from_rank = str(rank_of(move.fr) + 1)
    verb = "takes" if is_capture(move) else "to"
    text = f"{piece} {from_file} {from_rank} {verb} {to_file} {to_rank}"
    if move.promo:
        text += f", promoting to {PIECE_WORD[move.promo]}"
    return text


def spoken_result(result: str, mate: bool, variant: int) -> str:
    """stringFromMove: for the engine's game-end moves (types 4-6)."""
    if result == "1/2-1/2":
        return "the game is a draw!"
    if mate and variant not in (SUICIDE, 3):
        return "check mate!"
    return "white wins!" if result == "1-0" else "black wins!"


def display_move(pos: Position, move: Move) -> str:
    """-[MBCMove localizedText]: origin, operation, destination and check."""
    check, mate = check_state(pos, move)
    mark = "≠" if mate else ("+" if check else "")
    if move.castle:
        text = "0 - 0" if move.castle == 2 else "0 - 0 - 0"
        return text + mark
    dest = "abcdefgh"[file_of(move.to)] + str(rank_of(move.to) + 1)
    if move.promo:
        dest += "=" + LETTER[move.promo]
    if move.kind == 2:
        return LETTER[kind_of(move.piece)] + "@" + dest + mark
    origin = (" " if kind_of(move.piece) == PAWN else LETTER[kind_of(move.piece)])
    origin += "abcdefgh"[file_of(move.fr)] + str(rank_of(move.fr) + 1)
    operation = "×" if is_capture(move) else "-"
    return origin + operation + dest + mark


def engine_move(move: Move) -> str:
    """-[MBCMove engineMove], the text Sjeng reads and .game files store."""
    if move.kind == 2:
        letter = LETTER[kind_of(move.piece)]
        letter = letter if color_of(move.piece) == WHITE else letter.lower()
        return f"{letter}@{'abcdefgh'[file_of(move.to)]}{rank_of(move.to) + 1}\n"
    text = "abcdefgh"[file_of(move.fr)] + str(rank_of(move.fr) + 1)
    text += "abcdefgh"[file_of(move.to)] + str(rank_of(move.to) + 1)
    if move.promo:
        letter = LETTER[move.promo]
        text += letter if color_of(move.piece) == WHITE else letter.lower()
    return text + "\n"


def find_move(pos: Position, text: str) -> Move | None:
    cleaned = text.strip().replace("0-0-0", "O-O-O").replace("0-0", "O-O")
    cleaned = cleaned.rstrip("+#")
    compact = cleaned.replace(" ", "")
    for move in pos.legal_moves():
        if pos.san(move).rstrip("+#") == cleaned or move.uci() == compact.lower():
            return move
    phrase = compact.lower()
    if phrase in ("castle", "castlekingside", "o-o"):
        cleaned = "O-O"
    elif phrase in ("castlequeen", "castlequeenside", "o-o-o"):
        cleaned = "O-O-O"
    for move in pos.legal_moves():
        if pos.san(move).rstrip("+#") == cleaned:
            return move
    return None


def find_spoken(pos: Position, phrase: str) -> Move | None:
    """Match a SpeechHelp.plist phrase to a legal move.

    "<Piece> <Square> to/takes <Square>", "Castle kingside",
    "Castle queenside", "Drop <Piece> at <Square>".
    """
    words = phrase.lower().split()
    text = " ".join(words)
    if text.startswith("castle"):
        want = 1 if "queen" in text else 2
        for move in pos.legal_moves():
            if move.castle == want:
                return move
        return None
    names = {word: kind for kind, word in PIECE_WORD.items()}
    squares = re.findall(r"\b([a-h])\s*([1-8])\b", text)
    if text.startswith("drop") and len(squares) == 1 and len(words) > 1:
        kind = names.get(words[1])
        to = parse_square("".join(squares[0]))
        for move in pos.legal_moves():
            if move.kind == 2 and move.to == to and kind_of(move.piece) == kind:
                return move
        return None
    if len(squares) != 2 or not words or words[0] not in names:
        return None
    kind = names[words[0]]
    fr = parse_square("".join(squares[0]))
    to = parse_square("".join(squares[1]))
    found = [m for m in pos.legal_moves() if m.kind == 1 and m.fr == fr and m.to == to and kind_of(m.piece) == kind]
    if not found:
        return None
    for move in found:
        if move.promo in (0, QUEEN):
            return move
    return found[0]


def moves_from_pgn(text: str) -> tuple[dict, list[str]]:
    tags = {}
    for key, value in re.findall(r'\[([A-Za-z0-9]+):?\s+"([^"]*)"\]', text):
        tags[key] = value
    body = re.sub(r"\[[^\]]*\]", " ", text)
    body = re.sub(r"\{[^}]*\}", " ", body)
    body = re.sub(r";.*", " ", body)
    tokens = []
    for token in body.split():
        if token in ("1-0", "0-1", "1/2-1/2", "*"):
            continue
        token = re.sub(r"^\d+\.+", "", token)
        if not token or token.startswith("$"):
            continue
        tokens.append(token)
    return tags, tokens


def _variant_from(text: str, default: int) -> int:
    text = (text or "").lower()
    for number, key in VARIANT_KEYS.items():
        if text == key or text == VARIANT_NAMES[number].lower():
            return number
    return default


def load_pgn(path: Path, variant: int = STANDARD) -> tuple[Position, list[Move], dict]:
    tags, tokens = moves_from_pgn(Path(path).read_text(encoding="utf-8", errors="replace"))
    pos = Position(_variant_from(tags.get("Variant", ""), STANDARD))
    moves: list[Move] = []
    for token in tokens:
        move = find_move(pos, token)
        if move is None:
            break
        moves.append(move)
        pos.push(move)
    props = {}
    for key in ("Event", "White", "Black", "Result"):
        if key in tags:
            props[key] = tags[key]
    if "Date" in tags:
        props["StartDate"] = tags["Date"]
    if "Time" in tags:
        props["StartTime"] = tags["Time"]
    site = tags.get("Site", "")
    if site:
        city, _, country = site.partition(",")
        props["City"] = city.strip()
        props["Country"] = country.strip()
    white_program = tags.get("WhiteType", "").lower() == "program"
    black_program = tags.get("BlackType", "").lower() == "program"
    props["_players"] = (2 if white_program else 0) + (1 if black_program else 0)
    return pos, moves, props


def _pgn_name(name: str) -> str:
    """-[MBCDocument parseName:intoFirst:last:] then "%@, %@" (last, first)."""
    words = (name or "").split()
    if len(words) < 2:
        return name or ""
    last = [words[-1]]
    index = len(words) - 2
    while index > 0 and words[index][:1].islower():
        last.insert(0, words[index])
        index -= 1
    first = words[: index + 1]
    return f"{' '.join(last)}, {' '.join(first)}"


def pgn_movetext(variant: int, moves: list[Move]) -> str:
    """-[MBCBoard saveMovesTo:]: a newline every five moves, no check marks."""
    pos = Position(variant)
    out = []
    for index, move in enumerate(moves):
        if index % 2 == 0:
            if index % 10 == 0:
                out.append("\n")
            out.append(f"{index // 2 + 1}. ")
        if move.kind == 2:
            out.append(f"{LETTER[kind_of(move.piece)]}@{'abcdefgh'[file_of(move.to)]}{rank_of(move.to) + 1} ")
        elif kind_of(move.piece) == PAWN:
            dest = "abcdefgh"[file_of(move.to)] + str(rank_of(move.to) + 1)
            if is_capture(move):
                out.append("abcdefgh"[file_of(move.fr)] + "x" + dest)
            else:
                out.append(dest)
            out.append(f"={LETTER[move.promo]} " if move.promo else " ")
        elif move.castle:
            out.append("O-O " if move.castle == 2 else "O-O-O ")
        else:
            san = pos.san(move).rstrip("+#")
            out.append(san + " ")
        pos.push(move)
    return "".join(out)


def save_pgn(path: Path, props: dict, variant: int, moves: list[Move], white_program: bool, black_program: bool) -> None:
    white = PROGRAM_NAME if white_program else _pgn_name(props.get("White", ""))
    black = PROGRAM_NAME if black_program else _pgn_name(props.get("Black", ""))
    lines = [
        f'[Event "{props.get("Event", "")}"]',
        f'[Site "{props.get("City", "")}, {props.get("Country", "")}"]',
        f'[Date "{props.get("StartDate", "")}"]',
        '[Round "-"]',
        f'[White "{white}"]',
        f'[Black "{black}"]',
        f'[Result "{props.get("Result", "*")}"]',
        f'[Time "{props.get("StartTime", "")}"]',
    ]
    text = "\n".join(lines) + "\n"
    if variant != STANDARD:
        text += f'[Variant "{VARIANT_KEYS[variant]}"]\n'
    if white_program:
        text += '[WhiteType: "program"]\n'
    if black_program:
        text += '[BlackType: "program"]\n'
    text += pgn_movetext(variant, moves) + "\n" + props.get("Result", "*") + "\n"
    Path(path).write_text(text, encoding="utf-8")


def fen_of(pos: Position) -> str:
    """-[MBCBoard fen]: placement, side, castling, en passant, clocks."""
    placement = pos.fen().split()[0]
    castle = ""
    for king_sq, rook_sq, letter, side in ((4, 7, "K", WHITE), (4, 0, "Q", WHITE), (60, 63, "k", BLACK), (60, 56, "q", BLACK)):
        king = pos.board[king_sq]
        rook = pos.board[rook_sq]
        if (kind_of(king) == KING and color_of(king) == side and not king & MOVED
                and kind_of(rook) == ROOK and color_of(rook) == side and not rook & MOVED):
            castle += letter
    ep = "-" if pos.ep < 0 else "abcdefgh"[file_of(pos.ep)] + str(rank_of(pos.ep) + 1)
    side = "w" if pos.side == WHITE else "b"
    return f"{placement} {side} {castle or '-'} {ep} {pos.halfmove} {pos.fullmove}"


def holding_of(pos: Position) -> str:
    """-[MBCBoard holding]: "[KQBNRP] [KQBNRP]" counts, white then black."""
    parts = []
    for side in (WHITE, BLACK):
        text = ""
        for kind in (KING, QUEEN, BISHOP, KNIGHT, ROOK, PAWN):
            text += LETTER[kind] * pos.hand[kind | side]
        parts.append(text)
    return f"[{parts[0]}] [{parts[1]}]"


def save_game(path: Path, props: dict, variant: int, moves: list[Move], white_program: bool, black_program: bool) -> None:
    pos = Position(variant)
    for move in moves:
        pos.push(move)
    payload = {key: value for key, value in props.items() if not key.startswith("_") and value is not None}
    payload.update({
        "Variant": VARIANT_KEYS[variant],
        "WhiteType": "program" if white_program else "human",
        "BlackType": "program" if black_program else "human",
        "Position": fen_of(pos),
        "Holding": holding_of(pos),
        "Moves": "".join(engine_move(move) for move in moves),
    })
    with Path(path).open("wb") as handle:
        plistlib.dump(payload, handle)


def _parse_engine_move(pos: Position, text: str) -> Move | None:
    text = text.strip()
    if not text:
        return None
    if "@" in text:
        letter, square = text.split("@", 1)
        kind = {v: k for k, v in LETTER.items()}.get(letter.upper())
        to = parse_square(square.lower())
        for move in pos.legal_moves():
            if move.kind == 2 and move.to == to and kind_of(move.piece) == kind:
                return move
        return None
    return find_move(pos, text.lower())


def load_game(path: Path) -> tuple[Position, list[Move], dict]:
    """-[MBCDocument loadGame:]; also reads the port's older plist layout."""
    with Path(path).open("rb") as handle:
        payload = plistlib.load(handle)
    variant = _variant_from(str(payload.get("Variant", "normal")), STANDARD)
    pos = Position(variant)
    moves: list[Move] = []
    raw = payload.get("Moves", "")
    items = raw.split() if isinstance(raw, str) else [str(item) for item in raw]
    for item in items:
        move = _parse_engine_move(pos, item)
        if move is None:
            break
        moves.append(move)
        pos.push(move)
    props = dict(payload)
    white_program = str(payload.get("WhiteType", "human")) != "human"
    black_program = str(payload.get("BlackType", "human")) != "human"
    if "WhiteType" not in payload and "BlackType" not in payload:
        white_program = black_program = False
    props["_players"] = (2 if white_program else 0) + (1 if black_program else 0)
    if "Site" in props and "City" not in props:
        props["City"] = props.pop("Site")
    return pos, moves, props
