"""Windows 11 chess window modeled on Apple Chess 3.18.

Run: python chess_app.py

The board, variants, menus, dialogs, speech and files follow the Chess
Mach-O that IDA has open (Chess.i64, base 0x100000000) and the strings and
nibs in its Resources folder. The 3D board is the OpenGL scene from
MBCBoardView. Sjeng is Mach-O, so the computer opponent is the local
search in chess_engine.py.
"""

from __future__ import annotations

import math
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from board_gl import HAND_BASE, REGION_PROMO_BLACK, REGION_PROMO_WHITE, GLBoard, hand_center, square_center
from chess_engine import SJENG, choose_move
from chess_rules import (
    BISHOP,
    BLACK,
    KING,
    KNIGHT,
    QUEEN,
    ROOK,
    SUICIDE,
    WHITE,
    Move,
    Position,
    color_of,
    kind_of,
    level_text,
    parse_square,
)
from pgn_io import (
    check_state,
    display_move,
    find_spoken,
    load_game,
    load_pgn,
    save_game,
    save_pgn,
    spoken_move,
    spoken_result,
)
from settings_store import load as load_settings
from settings_store import save as save_settings
from speech_win import Listener, Speaker, installed_voices

BOARD_STYLES = ("Wood", "Marble", "Metal", "Grass")
PIECE_STYLES = ("Wood", "Marble", "Metal", "Fur")
PLAYER_CHOICES = ("Human vs Human", "Human vs Computer", "Computer vs Human", "Computer vs Computer")
VARIANT_CHOICES = ("Regular", "Crazyhouse", "Suicide", "Losers")
# dword_10003CE70: MBCNewGamePlayers to the human side (0 white, 1 black,
# 2 both, 3 none).
HUMAN_SIDE = (2, 0, 1, 3)
OUTCOME_TEXT = {"1-0": "White wins!", "0-1": "Black wins!", "1/2-1/2": "Draw!"}
# -[MBCBoardView mouseUp:] cycles the promotion piece Q, N, R, B (and K in
# suicide).
PROMO_CYCLE = {QUEEN: KNIGHT, KNIGHT: ROOK, ROOK: BISHOP, BISHOP: QUEEN}
PROMO_CYCLE_SUICIDE = {QUEEN: KING, KING: KNIGHT, KNIGHT: ROOK, ROOK: BISHOP, BISHOP: QUEEN}
BG = "#ececec"


class ChessApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Chess")
        self.root.configure(bg=BG)
        self.settings = load_settings()
        self.board_style = self.settings["board_style"]
        self.piece_style = self.settings["piece_style"]
        self.level = int(self.settings["search_time"])
        self.players = int(self.settings["players"])
        self.elevation = float(self.settings["board_angle"])
        self.azimuth = float(self.settings["board_spin"]) % 360.0
        self.speak_moves = bool(self.settings["speak_moves"])
        self.speak_human = bool(self.settings["speak_human"])
        self.listen_moves = bool(self.settings["listen"])
        self.show_log = tk.BooleanVar(value=bool(self.settings["show_log"]))
        self.show_title = bool(self.settings["show_move_in_title"])
        self.fullscreen = False
        self.recent: list[str] = list(self.settings.get("recent", []))
        self.pos = Position(int(self.settings["variant"]))
        self.moves: list[Move] = []
        self.texts: list[str] = []
        self.props: dict = {}
        self.doc_path: Path | None = None
        self.anim = None
        self.drag = None
        self.picked = None          # square, or HAND_BASE + code for a hand piece
        self.key_buffer = ""
        self.default_promo = {WHITE: QUEEN, BLACK: QUEEN}
        self.hint_move = None
        self.last_arrow = None
        self.ponder: Move | None = None
        self.game_over: str | None = None
        self.thinking = False
        self.token = 0
        self.dont_move_before = 0.0
        self.primary = Speaker(self.settings["primary_voice"])
        self.alternate = Speaker(self.settings["alternate_voice"], female_fallback=True)
        self.listener = Listener()
        self._engine_results: queue.Queue = queue.Queue()
        self._hints: queue.Queue = queue.Queue()
        self._build()
        self.root.minsize(900, 640)
        self.root.geometry("1280x900")
        self._start_game(self.players, self.pos.variant)
        self.root.after(30, self._poll)

    # ----- window -----------------------------------------------------

    def _build(self) -> None:
        self._menu()
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)
        self.board = GLBoard(outer, self.on_square, self.on_orbit)
        self.board.on_click_at = self.on_click_at
        self.board.on_drop = self.on_drop
        self.board.on_dragging = self.on_dragging
        self.board.pack(side="left", fill="both", expand=True)
        self.log_frame = tk.Frame(outer, bg=BG, width=280)
        style = ttk.Style(self.root)
        log_font = tkfont.Font(family="Segoe UI", size=10)
        style.configure("Log.Treeview", rowheight=int(log_font.metrics("linespace") * 1.5), font=log_font)
        self.log = ttk.Treeview(self.log_frame, columns=("n", "white", "black"), show="headings", style="Log.Treeview")
        self.log.heading("n", text="")
        self.log.heading("white", text="White")
        self.log.heading("black", text="Black")
        self.log.column("n", width=36, anchor="e", stretch=False)
        self.log.column("white", width=110, anchor="w")
        self.log.column("black", width=110, anchor="w")
        self.log.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        ttk.Button(self.log_frame, text="Edit Game Info…", command=self.edit_info).pack(pady=(0, 8))
        self.root.bind("<Key>", self.on_key)
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

    def _menu(self) -> None:
        bar = tk.Menu(self.root)
        chess = tk.Menu(bar, tearoff=0)
        chess.add_command(label="About Chess", command=self.about)
        chess.add_separator()
        chess.add_command(label="Preferences…", command=self.preferences, accelerator="Ctrl+,")
        chess.add_separator()
        chess.add_command(label="Quit Chess", command=self.quit_app, accelerator="Ctrl+Q")
        bar.add_cascade(label="Chess", menu=chess)
        game = tk.Menu(bar, tearoff=0)
        game.add_command(label="New…", command=self.new_game, accelerator="Ctrl+N")
        game.add_command(label="Open…", command=self.open_file, accelerator="Ctrl+O")
        self.recent_menu = tk.Menu(game, tearoff=0)
        game.add_cascade(label="Open Recent", menu=self.recent_menu)
        game.add_separator()
        game.add_command(label="Close", command=self.quit_app, accelerator="Ctrl+W")
        game.add_command(label="Save", command=self.save, accelerator="Ctrl+S")
        game.add_command(label="Save As…", command=self.save_as, accelerator="Ctrl+Shift+S")
        game.add_separator()
        game.add_command(label="Resign", command=self.resign)
        # -[MBCDocument offerDraw] only reaches a Game Center opponent.
        game.add_command(label="Offer Draw", state="disabled")
        bar.add_cascade(label="Game", menu=game)
        moves = tk.Menu(bar, tearoff=0)
        moves.add_command(label="Take Back Move", command=self.take_back, accelerator="Ctrl+Z")
        moves.add_command(label="Show Hint", command=self.show_hint, accelerator="Ctrl+]")
        moves.add_command(label="Show Last Move", command=self.show_last_move, accelerator="Ctrl+[")
        bar.add_cascade(label="Moves", menu=moves)
        window = tk.Menu(bar, tearoff=0)
        window.add_command(label="Minimize", command=self.root.iconify, accelerator="Ctrl+M")
        window.add_command(label="Zoom", command=self.zoom)
        window.add_separator()
        window.add_checkbutton(label="Game Log", variable=self.show_log, command=self.toggle_log, accelerator="Ctrl+L")
        window.add_command(label="Enter Full Screen", command=self.toggle_fullscreen, accelerator="F11")
        bar.add_cascade(label="Window", menu=window)
        help_menu = tk.Menu(bar, tearoff=0)
        help_menu.add_command(label="Chess Help", command=self.help, accelerator="Ctrl+?")
        bar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=bar)
        self._refresh_recent()
        binds = {
            "<Control-comma>": self.preferences,
            "<Control-q>": self.quit_app,
            "<Control-n>": self.new_game,
            "<Control-o>": self.open_file,
            "<Control-w>": self.quit_app,
            "<Control-s>": self.save,
            "<Control-S>": self.save_as,
            "<Control-z>": self.take_back,
            "<Control-bracketright>": self.show_hint,
            "<Control-bracketleft>": self.show_last_move,
            "<Control-m>": self.root.iconify,
            "<Control-l>": lambda: (self.show_log.set(not self.show_log.get()), self.toggle_log()),
            "<F11>": self.toggle_fullscreen,
            "<Control-question>": self.help,
        }
        for key, func in binds.items():
            self.root.bind(key, lambda _e, f=func: (f(), "break")[1])

    def _refresh_recent(self) -> None:
        self.recent_menu.delete(0, "end")
        for path in self.recent:
            self.recent_menu.add_command(label=Path(path).name, command=lambda p=path: self._open_path(Path(p)))
        if self.recent:
            self.recent_menu.add_separator()
        self.recent_menu.add_command(label="Clear Menu", command=self._clear_recent)

    def _clear_recent(self) -> None:
        self.recent = []
        self._persist()
        self._refresh_recent()

    def _remember(self, path: Path) -> None:
        text = str(path)
        self.recent = [text] + [item for item in self.recent if item != text]
        self.recent = self.recent[:10]
        self._persist()
        self._refresh_recent()

    # ----- sides --------------------------------------------------------

    def human_side(self) -> int:
        return HUMAN_SIDE[self.players]

    def is_human(self, side: int) -> bool:
        human = self.human_side()
        return human == 2 or (human == 0 and side == WHITE) or (human == 1 and side == BLACK)

    def human_to_move(self) -> bool:
        return not self.game_over and self.is_human(self.pos.side)

    # ----- game state -----------------------------------------------------

    def _start_game(self, players: int, variant: int, props: dict | None = None,
                    pos: Position | None = None, moves: list[Move] | None = None) -> None:
        self.token += 1
        self.thinking = False
        self.players = players
        self.pos = pos or Position(variant)
        self.moves = list(moves or [])
        self.texts = []
        replay = Position(self.pos.variant)
        for move in self.moves:
            self.texts.append(display_move(replay, move))
            replay.push(move)
        self.anim = None
        self.drag = None
        self.picked = None
        self.key_buffer = ""
        self.default_promo = {WHITE: QUEEN, BLACK: QUEEN}
        self.hint_move = None
        self.last_arrow = None
        self.ponder = None
        self.dont_move_before = 0.0
        self.game_over = self.pos.outcome() if self.moves else None
        self.props = self._default_props()
        if props:
            self.props.update({k: v for k, v in props.items() if not k.startswith("_")})
        if self.game_over and self.props.get("Result", "*") == "*":
            self.props["Result"] = self.game_over
        # -[MBCBoardView startGame:playing:] turns the board to the human.
        human = self.human_side()
        if human != 3:
            side = (len(self.moves) & 1) if human == 2 else human
            facing = 0 if 90.0 < self.azimuth <= 270.0 else 1
            if facing != side:
                self.azimuth = (self.azimuth + 180.0) % 360.0
        self.listener.stop()
        if self.listen_moves:
            self.listener.start()
        self.refresh()
        self.root.after(200, self.maybe_engine)

    def _default_props(self) -> dict:
        """-[MBCGameInfo startGame:playing:] fills in names, date and place."""
        now = datetime.now()
        human = self.human_side()
        white_human = human in (0, 2)
        black_human = human in (1, 2)
        return {
            "White": self.settings["human_name"] if white_human else "Computer",
            "Black": (self.settings["human_name2"] if human == 2 else self.settings["human_name"]) if black_human else "Computer",
            "City": self.settings["city"],
            "Country": self.settings["country"],
            "Event": self.settings["event"],
            "StartDate": now.strftime("%Y.%m.%d"),
            "StartTime": now.strftime("%H:%M:%S"),
            "Result": "*",
            "MBCSearchTime": self.level,
        }

    def new_game(self) -> None:
        """The "Start a New Game?" sheet from Board.nib."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Start a New Game?")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.configure(bg=BG, padx=16, pady=12)
        tk.Label(dialog, text="Start a New Game?", bg=BG, font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        tk.Label(dialog, text="Players:", bg=BG).grid(row=1, column=0, sticky="e", padx=(0, 8))
        players = tk.StringVar(value=PLAYER_CHOICES[self.players])
        ttk.Combobox(dialog, textvariable=players, values=PLAYER_CHOICES, state="readonly", width=24).grid(row=1, column=1, columnspan=2, sticky="w")
        tk.Label(dialog, text="Variant:", bg=BG).grid(row=2, column=0, sticky="e", padx=(0, 8), pady=6)
        variant = tk.StringVar(value=VARIANT_CHOICES[int(self.settings["variant"])])
        ttk.Combobox(dialog, textvariable=variant, values=VARIANT_CHOICES, state="readonly", width=24).grid(row=2, column=1, columnspan=2, sticky="w")
        strength = tk.Frame(dialog, bg=BG)
        level = tk.IntVar(value=self.level)
        caption = tk.Label(strength, text=level_text(self.level), bg=BG)
        caption.pack(anchor="w")
        row = tk.Frame(strength, bg=BG)
        row.pack(fill="x")
        tk.Label(row, text="Faster", bg=BG).pack(side="left")
        tk.Scale(row, from_=1, to=12, orient="horizontal", showvalue=False, variable=level, length=220, bg=BG,
                 highlightthickness=0, command=lambda _v: caption.configure(text=level_text(level.get()))).pack(side="left", padx=6)
        tk.Label(row, text="Stronger", bg=BG).pack(side="left")
        strength.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 4))

        def update_strength(*_args) -> None:
            # -[MBCBoardWin hideEngineStrength]: no computer, no slider.
            if PLAYER_CHOICES.index(players.get()) == 0:
                strength.grid_remove()
            else:
                strength.grid()

        players.trace_add("write", update_strength)
        update_strength()
        picked = {"ok": False}

        def accept() -> None:
            picked["ok"] = True
            dialog.destroy()

        buttons = tk.Frame(dialog, bg=BG)
        buttons.grid(row=4, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="Start", command=accept, default="active").pack(side="left")
        dialog.bind("<Return>", lambda _e: accept())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.grab_set()
        self.root.wait_window(dialog)
        if not picked["ok"]:
            return
        self.level = level.get()
        self.settings["players"] = PLAYER_CHOICES.index(players.get())
        self.settings["variant"] = VARIANT_CHOICES.index(variant.get())
        self.doc_path = None
        self._persist()
        self._start_game(self.settings["players"], self.settings["variant"])

    def preferences(self) -> None:
        """The Preferences panel from Board.nib: style, speech and strength."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Preferences")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.configure(bg=BG, padx=16, pady=12)
        voices = ["Default Voice"] + installed_voices()
        style = ttk.LabelFrame(dialog, text="Style", padding=8)
        style.grid(row=0, column=0, sticky="ew")
        board = tk.StringVar(value=self.board_style)
        pieces = tk.StringVar(value=self.piece_style)
        ttk.Label(style, text="Board").grid(row=0, column=0, sticky="w")
        ttk.Combobox(style, textvariable=board, values=BOARD_STYLES, state="readonly", width=14).grid(row=1, column=0, padx=(0, 12))
        ttk.Label(style, text="Pieces").grid(row=0, column=1, sticky="w")
        ttk.Combobox(style, textvariable=pieces, values=PIECE_STYLES, state="readonly", width=14).grid(row=1, column=1)
        speech = ttk.LabelFrame(dialog, text="Speech", padding=8)
        speech.grid(row=1, column=0, sticky="ew", pady=10)
        speak = tk.BooleanVar(value=self.speak_moves)
        speak_human = tk.BooleanVar(value=self.speak_human)
        listen = tk.BooleanVar(value=self.listen_moves)
        primary = tk.StringVar(value=self.settings["primary_voice"] or "Default Voice")
        alternate = tk.StringVar(value=self.settings["alternate_voice"] or "Default Voice")
        ttk.Checkbutton(speech, text="Speak Computer Moves", variable=speak).grid(row=0, column=0, sticky="w")
        ttk.Combobox(speech, textvariable=primary, values=voices, state="readonly", width=30).grid(row=0, column=1, padx=8)
        ttk.Checkbutton(speech, text="Speak Human Moves", variable=speak_human).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(speech, textvariable=alternate, values=voices, state="readonly", width=30).grid(row=1, column=1, padx=8)
        ttk.Checkbutton(speech, text="Allow Player to Speak Moves", variable=listen).grid(row=2, column=0, sticky="w")
        strength = ttk.LabelFrame(dialog, text="Computer", padding=8)
        strength.grid(row=2, column=0, sticky="ew")
        level = tk.IntVar(value=self.level)
        caption = ttk.Label(strength, text=level_text(self.level))
        caption.pack(anchor="w")
        row = tk.Frame(strength)
        row.pack(fill="x")
        ttk.Label(row, text="Faster").pack(side="left")
        tk.Scale(row, from_=1, to=12, orient="horizontal", showvalue=False, variable=level, length=260,
                 highlightthickness=0, command=lambda _v: caption.configure(text=level_text(level.get()))).pack(side="left", padx=6)
        ttk.Label(row, text="Stronger").pack(side="left")
        picked = {"ok": False}

        def accept() -> None:
            picked["ok"] = True
            dialog.destroy()

        buttons = tk.Frame(dialog, bg=BG)
        buttons.grid(row=3, column=0, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="OK", command=accept, default="active").pack(side="left")
        dialog.bind("<Return>", lambda _e: accept())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.grab_set()
        self.root.wait_window(dialog)
        if not picked["ok"]:
            return
        self.board_style = board.get()
        self.piece_style = pieces.get()
        self.speak_moves = speak.get()
        self.speak_human = speak_human.get()
        self.level = level.get()
        self.props["MBCSearchTime"] = self.level
        self.settings["primary_voice"] = "" if primary.get() == "Default Voice" else primary.get()
        self.settings["alternate_voice"] = "" if alternate.get() == "Default Voice" else alternate.get()
        self.primary.set_voice(self.settings["primary_voice"])
        self.alternate.set_voice(self.settings["alternate_voice"])
        if listen.get() != self.listen_moves:
            self.listen_moves = listen.get()
            if self.listen_moves:
                self.listener.start()
            else:
                self.listener.stop()
        self._persist()
        self.refresh()

    def edit_info(self) -> None:
        """"Edit Game Information" from Board.nib."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Game Information")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.configure(bg=BG, padx=12, pady=10)
        fields = {}
        for row, (key, label) in enumerate((("White", "White:"), ("Black", "Black:"), ("City", "Town/City:"),
                                             ("Country", "Country:"), ("Event", "Event:"))):
            tk.Label(dialog, text=label, bg=BG).grid(row=row, column=0, sticky="e", padx=(0, 6), pady=3)
            var = tk.StringVar(value=self.props.get(key, ""))
            ttk.Entry(dialog, textvariable=var, width=30).grid(row=row, column=1, pady=3)
            fields[key] = var

        def accept() -> None:
            for key, var in fields.items():
                self.props[key] = var.get()
            human = self.human_side()
            if human in (0, 2):
                self.settings["human_name"] = self.props["White"]
            if human == 1:
                self.settings["human_name"] = self.props["Black"]
            if human == 2:
                self.settings["human_name2"] = self.props["Black"]
            self.settings["city"] = self.props["City"]
            self.settings["country"] = self.props["Country"]
            self.settings["event"] = self.props["Event"]
            self._persist()
            dialog.destroy()
            self.refresh()

        buttons = tk.Frame(dialog, bg=BG)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="OK", command=accept, default="active").pack(side="left")
        dialog.grab_set()

    # ----- moving -----------------------------------------------------------

    def _wants_input(self) -> bool:
        return self.human_to_move() and not self.thinking and self.anim is None

    def _promo_side(self):
        """canPromote: for a side the human plays: a pawn on its 7th rank."""
        if self.game_over:
            return None
        human = self.human_side()
        side = self.pos.side
        if not self.is_human(side) or human == 3:
            return None
        rank = 6 if side == WHITE else 1
        for sq in range(rank * 8, rank * 8 + 8):
            piece = self.pos.board[sq]
            if kind_of(piece) == 6 and color_of(piece) == side:
                return side
        return None

    def on_click_at(self, x: int, y: int, _sq: int | None) -> None:
        region = self.board.region_at(x, y)
        if region in (REGION_PROMO_WHITE, REGION_PROMO_BLACK):
            side = WHITE if region == REGION_PROMO_WHITE else BLACK
            cycle = PROMO_CYCLE_SUICIDE if self.pos.variant == SUICIDE else PROMO_CYCLE
            self.default_promo[side] = cycle.get(self.default_promo[side], QUEEN)
            self.refresh()
            return
        if region is None:
            return
        self.select(region, animate=True)

    def on_square(self, sq: int) -> None:
        self.select(sq, animate=True)

    def select(self, target: int, animate: bool = False) -> None:
        """startSelection: / endSelection:animate: for clicks and keys.
        The original only animates typed moves; here clicked moves slide
        too, and dragged ones glide from the drop point (see on_drop)."""
        if not self._wants_input():
            self.root.bell()
            return
        side = self.pos.side
        if target >= HAND_BASE:
            code = target - HAND_BASE
            if color_of(code) == side and self.pos.hand[code] > 0:
                self.picked = None if self.picked == target else target
            self.refresh()
            return
        if not 0 <= target < 64:
            return
        piece = self.pos.board[target]
        if self.picked is None:
            if piece and color_of(piece) == side:
                self.picked = target
                self.refresh()
            return
        if target == self.picked:
            self.picked = None
            self.refresh()
            return
        if piece and color_of(piece) == side and self.picked < HAND_BASE:
            self.picked = target
            self.refresh()
            return
        self._try_move(self.picked, target, animate)

    def _try_move(self, source: int, dest: int, animate: bool = False,
                  start: tuple[float, float, float] | None = None) -> None:
        candidates = []
        for move in self.pos.legal_moves():
            if source >= HAND_BASE:
                if move.kind == 2 and move.to == dest and (kind_of(move.piece) | color_of(move.piece)) == source - HAND_BASE:
                    candidates.append(move)
            elif move.kind == 1 and move.fr == source and move.to == dest:
                candidates.append(move)
        self.picked = None
        if not candidates:
            # MBCIlMove: the original beeps and puts the piece back.
            self.root.bell()
            self.refresh()
            return
        chosen = candidates[0]
        if any(move.promo for move in candidates):
            want = self.default_promo[self.pos.side]
            chosen = next((m for m in candidates if m.promo == want), next(m for m in candidates if m.promo == QUEEN))
        self.play(chosen, animate=animate, start=start)

    def on_drop(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self.drag = None
        source = self.board.region_at(x0, y0)
        dest = self.board.region_at(x1, y1)
        if source is None or not self._wants_input() or dest is None or dest >= 64:
            self.refresh()
            return
        if source < 64:
            piece = self.pos.board[source]
            if not piece or color_of(piece) != self.pos.side:
                self.refresh()
                return
        elif source < HAND_BASE:
            self.refresh()
            return
        point = self.board.hit_point(x1, y1)
        start = (point[0], 0.0, point[1]) if point is not None else None
        self._try_move(source, dest, animate=True, start=start)

    def on_dragging(self, x0: int, y0: int, x1: int, y1: int) -> None:
        if not x0 and not y0 and not x1 and not y1:
            if self.drag is not None:
                self.drag = None
                self.refresh()
            return
        if not self._wants_input():
            return
        source = self.board.region_at(x0, y0)
        point = self.board.hit_point(x1, y1)
        if source is None or point is None:
            return
        if source < 64:
            piece = self.pos.board[source]
            if not piece or color_of(piece) != self.pos.side:
                return
        elif source >= HAND_BASE:
            code = source - HAND_BASE
            if color_of(code) != self.pos.side or not self.pos.hand[code]:
                return
        else:
            return
        self.picked = None
        self.drag = (source, point[0], point[1])
        self.refresh()

    def on_key(self, event) -> str | None:
        """-[MBCBoardView keyDown:]: "e2" then "e4" moves, "=q" sets the
        promotion piece, Return or Delete cancels."""
        if event.state & 0x4:
            return None
        char = event.char
        if not char:
            return None
        if char in "\r\x7f\b":
            if self.key_buffer:
                self.key_buffer = ""
            elif self.picked is not None:
                self.picked = None
                self.refresh()
            return "break"
        lower = char.lower()
        if char in "12345678":
            if self.key_buffer[:1] in tuple("abcdefgh") and self.human_to_move():
                self.select(parse_square(self.key_buffer[0] + char), animate=True)
            else:
                self.root.bell()
            self.key_buffer = ""
            return "break"
        if lower in "abcdefgh" and not (lower == "b" and self.key_buffer == "="):
            self.key_buffer = lower
            return "break"
        if char == "=":
            self.key_buffer = "="
            return "break"
        if lower in "qrnbk" and self.key_buffer == "=":
            kinds = {"q": QUEEN, "r": ROOK, "n": KNIGHT, "b": BISHOP, "k": KING}
            if lower == "k" and self.pos.variant != SUICIDE:
                self.root.bell()
            else:
                self.default_promo[WHITE] = kinds[lower]
                self.default_promo[BLACK] = kinds[lower]
                self.refresh()
            self.key_buffer = ""
            return "break"
        return None

    def play(self, move: Move, animate: bool = True, start: tuple[float, float, float] | None = None) -> None:
        by_human = self.is_human(self.pos.side)
        side = self.pos.side
        text = display_move(self.pos, move)
        spoken = spoken_move(self.pos, move)
        check, mate = check_state(self.pos, move)
        self.texts.append(text)
        self.moves.append(move)
        before = self.pos.clone() if animate else None
        # -[MBCBoardWin executeMove:] calls hideMoves.
        self.hint_move = None
        self.last_arrow = None
        self.pos.push(move)
        self.picked = None
        self.drag = None
        self.game_over = self.pos.outcome()
        if self.game_over:
            self.props["Result"] = self.game_over
        if by_human:
            # -[MBCEngine opponentMoved:]: the engine waits two seconds.
            self.dont_move_before = time.perf_counter() + 2.0
            self.ponder = None
        self._speak_move(side, by_human, spoken, check and not mate)
        if self.game_over:
            self._speak_end(side, by_human, mate)
        if animate:
            # +[MBCMoveAnimation moveAnimation:board:view:]: the board keeps
            # showing the old position while the piece slides, as the
            # selected piece, from its square (or the hand) to the target.
            # A drag only glides the last bit, at the pace of a two-square
            # slide (20 units a second), so it doesn't creep.
            dropped = start is not None
            if move.kind == 2:
                source = HAND_BASE + (kind_of(move.piece) | color_of(move.piece))
                start = start or hand_center(source - HAND_BASE)
            else:
                source = move.fr
                start = start or square_center(move.fr)
            self.anim = {
                "pos": before,
                "source": source,
                "start": start,
                "end": square_center(move.to),
                "dest": move.to,
                "duration": 1.0,
                "began": time.perf_counter(),
                "elapsed": 0.0,
                "at": start,
            }
            if dropped:
                end = self.anim["end"]
                distance = math.hypot(end[0] - start[0], end[2] - start[2])
                self.anim["duration"] = min(1.0, max(0.12, distance / 20.0))
            self._step_anim()
        else:
            self.refresh()
            self._after_move()

    def _step_anim(self) -> None:
        """-[MBCAnimation doStep:]: one second, linear in wall time, and
        a frame that would jump more than half way finishes it."""
        anim = self.anim
        if anim is None:
            return
        t = min(1.0, (time.perf_counter() - anim["began"]) / anim["duration"])
        if t - anim["elapsed"] > 0.5:
            t = 1.0
        anim["elapsed"] = t
        (x0, y0, z0), (x1, y1, z1) = anim["start"], anim["end"]
        anim["at"] = (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, z0 + (z1 - z0) * t)
        if t >= 1.0:
            self.anim = None
            self.refresh()
            self._after_move()
            return
        self.refresh()
        self.root.after(16, self._step_anim)

    def _after_move(self) -> None:
        if not self.game_over:
            self.root.after(40, self.maybe_engine)

    # ----- speech ------------------------------------------------------------

    def _synth_for(self, side: int) -> Speaker:
        """-[MBCInteractivePlayer useAlternateSynthForMove:]."""
        human = self.human_side()
        if human in (2, 3):
            return self.alternate if side == BLACK else self.primary
        return self.alternate if (side == WHITE and human == 0) or (side == BLACK and human == 1) else self.primary

    def _speak_move(self, side: int, by_human: bool, text: str, check: bool) -> None:
        if not (self.speak_human if by_human else self.speak_moves):
            return
        synth = self._synth_for(side)
        synth.say(text)
        if check:
            self.root.after(200, lambda: synth.say("check!"))

    def _speak_end(self, side: int, by_human: bool, mate: bool) -> None:
        """MBCInteractivePlayer gameEnded: speaks the result move."""
        human = self.human_side()
        if human == 3:
            wanted = self.speak_moves
        elif human == 2 or by_human:
            wanted = self.speak_human
        else:
            wanted = self.speak_moves
        if wanted:
            self._synth_for(side).say(spoken_result(self.game_over, mate, self.pos.variant))

    # ----- engine -------------------------------------------------------------

    def maybe_engine(self) -> None:
        if self.thinking or self.game_over or self.anim is not None:
            return
        if self.is_human(self.pos.side):
            return
        self.token += 1
        token = self.token
        snap = self.pos.clone()
        level = self.level
        history = [move.uci() for move in self.moves]
        self.thinking = True
        self.refresh()

        def work() -> None:
            try:
                move, _score, ponder = choose_move(snap, level, history=history)
            except Exception:
                move, ponder = None, None
            self._engine_results.put((token, move, ponder))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                token, move, ponder = self._engine_results.get_nowait()
                self._engine_done(token, move, ponder)
        except queue.Empty:
            pass
        try:
            while True:
                self._show_hint_move(self._hints.get_nowait())
        except queue.Empty:
            pass
        if self.listen_moves:
            for phrase in self.listener.pull():
                self._hear(phrase)
        self.root.after(30, self._poll)

    def _engine_done(self, token: int, move: Move | None, ponder: Move | None) -> None:
        if token != self.token:
            return
        wait = self.dont_move_before - time.perf_counter()
        if wait > 0:
            self.root.after(int(wait * 1000) + 1, lambda: self._engine_done(token, move, ponder))
            return
        self.thinking = False
        if move is None or self.game_over:
            self.refresh()
            return
        self.ponder = ponder
        if self.human_side() == 3:
            # handlePortMessage: computer vs computer waits four seconds.
            self.dont_move_before = max(self.dont_move_before, time.perf_counter()) + 4.0
        self.play(move)

    def show_hint(self) -> None:
        """-[MBCBoardWin showHint:] shows the engine's expected reply."""
        if self.game_over or not self.human_to_move():
            return
        if self.ponder is not None and self.ponder in self.pos.legal_moves():
            self._show_hint_move(self.ponder)
            return
        snap = self.pos.clone()
        history = [move.uci() for move in self.moves]

        def work() -> None:
            try:
                # Sjeng answers at the game's level in about a second; the
                # Python fallback stays at depth 3 so a hint stays quick.
                level = self.level if SJENG.available() else min(self.level, 3)
                move, _score, _ponder = choose_move(snap, level, history=history)
            except Exception:
                move = None
            self._hints.put(move)

        threading.Thread(target=work, daemon=True).start()

    def _show_hint_move(self, move: Move | None) -> None:
        if move is None or move not in self.pos.legal_moves():
            return
        self.hint_move = (move.fr if move.fr >= 0 else None, move.to)
        self.refresh()
        # announceHint: "I would suggest "%@"".
        if self.speak_moves or self.speak_human:
            self._synth_for(self.pos.side).say(f'I would suggest "{spoken_move(self.pos, move)}"')

    def show_last_move(self) -> None:
        """-[MBCBoardWin showLastMove:]: blue arrow and "The last move was"."""
        if not self.moves:
            return
        move = self.moves[-1]
        self.last_arrow = (move.fr if move.fr >= 0 else None, move.to)
        self.refresh()
        if self.speak_moves or self.speak_human:
            before = Position(self.pos.variant)
            for earlier in self.moves[:-1]:
                before.push(earlier)
            self._synth_for(before.side).say(f'The last move was "{spoken_move(before, move)}"')

    def take_back(self) -> None:
        """-[MBCEngine takeback]: back to the human's previous turn."""
        if not self.moves or self.players == 3 or self.anim is not None:
            return
        self.token += 1
        self.thinking = False
        self._pop()
        while self.moves and not self.is_human(self.pos.side):
            self._pop()
        self.game_over = None
        self.props["Result"] = "*"
        self.picked = None
        self.drag = None
        self.hint_move = None
        self.last_arrow = None
        self.ponder = None
        self.refresh()
        self.root.after(200, self.maybe_engine)

    def _pop(self) -> None:
        self.pos.pop()
        self.moves.pop()
        self.texts.pop()

    def resign(self) -> None:
        """-[MBCDocument resign]: the human side to move loses."""
        if self.game_over or not self.moves or self.players == 3:
            return
        self.token += 1
        self.thinking = False
        side = self.pos.side if self.is_human(self.pos.side) else self.pos.side ^ 8
        self.game_over = "0-1" if side == WHITE else "1-0"
        self.props["Result"] = self.game_over
        self.refresh()

    def _hear(self, phrase: str) -> None:
        text = phrase.lower()
        if text == "take back move":
            self.take_back()
            return
        if not self._wants_input():
            return
        move = find_spoken(self.pos, text)
        if move is None:
            self.root.bell()
            return
        if move.promo:
            want = self.default_promo[self.pos.side]
            for other in self.pos.legal_moves():
                if other.fr == move.fr and other.to == move.to and other.promo == want:
                    move = other
                    break
        self.play(move)

    # ----- drawing --------------------------------------------------------------

    def _title(self) -> str:
        """-[MBCGameInfo gameTitle] in game_title_fmt "%@ - %@   (%@)"."""
        count = len(self.moves)
        if count and self.show_title:
            number = (count + 1) // 2
            dots = "… " if count % 2 == 0 else ""
            status = f"{number}. {dots}{self.texts[-1]}"
        elif count:
            status = "Black to Move" if count & 1 else "White to Move"
        else:
            status = "New Game"
        if self.game_over:
            status = OUTCOME_TEXT[self.game_over]
        return f"{self.props.get('White', '')} - {self.props.get('Black', '')}   ({status})"

    def refresh(self) -> None:
        self.root.title(self._title())
        self.board.set_busy(self.thinking)
        self._draw_log()
        promo_side = self._promo_side()
        promo = None if promo_side is None else (promo_side, self.default_promo[promo_side])
        self.board.set_scene(
            pos=self.anim["pos"] if self.anim else self.pos,
            picked=self.picked if self.picked is not None and self.picked < 64 else None,
            hint_move=self.hint_move,
            last_move=self.last_arrow,
            board_style=self.board_style,
            piece_style=self.piece_style,
            elevation=self.elevation,
            azimuth=self.azimuth,
            anim=self.anim["dest"] if self.anim else None,
            drag=(self.anim["source"], self.anim["at"][0], self.anim["at"][2]) if self.anim else self.drag,
            promo_piece=promo,
        )

    def _draw_log(self) -> None:
        if not self.show_log.get():
            self.log_frame.pack_forget()
            return
        if not self.log_frame.winfo_ismapped():
            self.log_frame.pack(side="left", fill="y")
        self.log.delete(*self.log.get_children())
        for index in range(0, len(self.texts), 2):
            black = self.texts[index + 1] if index + 1 < len(self.texts) else ""
            self.log.insert("", "end", values=(f"{index // 2 + 1}.", self.texts[index], black))
        children = self.log.get_children()
        if children:
            self.log.see(children[-1])

    def on_orbit(self, azimuth: float, elevation: float, done: bool) -> None:
        self.azimuth = azimuth
        self.elevation = elevation
        self.refresh()
        if done:
            # -[MBCBoardWin setAngle:spin:].
            self._persist()

    # ----- files ------------------------------------------------------------------

    def _program_sides(self) -> tuple[bool, bool]:
        human = self.human_side()
        return human not in (0, 2), human not in (1, 2)

    def save(self) -> None:
        if self.doc_path is None:
            self.save_as()
            return
        self._write(self.doc_path)

    def save_as(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".game",
            filetypes=[("Chess Game", "*.game"), ("Portable Game Notation", "*.pgn")],
        )
        if not path:
            return
        self._write(Path(path))

    def _write(self, path: Path) -> None:
        white_program, black_program = self._program_sides()
        self.props["MBCSearchTime"] = self.level
        if path.suffix.lower() == ".pgn":
            save_pgn(path, self.props, self.pos.variant, self.moves, white_program, black_program)
        else:
            save_game(path, self.props, self.pos.variant, self.moves, white_program, black_program)
            self.doc_path = path
        self._remember(path)

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("Chess Games", "*.game *.pgn"), ("Chess Game", "*.game"), ("Portable Game Notation", "*.pgn"), ("All", "*.*")]
        )
        if path:
            self._open_path(Path(path))

    def _open_path(self, path: Path) -> None:
        try:
            if path.suffix.lower() == ".pgn":
                pos, moves, props = load_pgn(path)
            else:
                pos, moves, props = load_game(path)
        except Exception as exc:
            messagebox.showerror("Chess", f"The game could not be opened.\n\n{exc}")
            return
        players = int(props.get("_players", 0))
        if "MBCSearchTime" in props:
            try:
                self.level = int(props["MBCSearchTime"])
            except (TypeError, ValueError):
                pass
        self.doc_path = path if path.suffix.lower() == ".game" else None
        self._start_game(players, pos.variant, props, pos, moves)
        self._remember(path)

    # ----- window commands ------------------------------------------------------------

    def toggle_log(self) -> None:
        self._persist()
        self.refresh()

    def toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)

    def zoom(self) -> None:
        self.root.state("normal" if self.root.state() == "zoomed" else "zoomed")

    def about(self) -> None:
        messagebox.showinfo(
            "About Chess",
            "Chess\nWindows port of Apple Chess 3.18 (Chess-466.4.1).\n\n"
            "Rebuilt from the IDA database of\nApple_apps\\Chess\\MacOS\\Chess.\n"
            "The computer opponent is Sjeng 11.2 by Gian-Carlo\n"
            "Pascutto (GNU GPL), built for Windows from Apple's\n"
            "open-source copy.",
        )

    def help(self) -> None:
        messagebox.showinfo(
            "Chess Help",
            "Moving: click a piece and then a square, drag a piece, or type\n"
            "squares such as e2 then e4. Type =q, =r, =b or =n to choose the\n"
            "promotion piece, or click the piece shown beside the board.\n\n"
            "Turning the board: drag on the wooden frame.\n\n"
            "Spoken moves:\n"
            "  <Piece> <Square> to/takes <Square>  (e.g. Knight g1 to f3)\n"
            "  Castle kingside\n  Castle queenside\n"
            "  Drop <Piece> at <Square> (e.g. Drop knight at c7) [Crazyhouse only]\n"
            "  Take back move",
        )

    def _persist(self) -> None:
        self.settings.update({
            "board_angle": self.elevation,
            "board_spin": self.azimuth,
            "board_style": self.board_style,
            "piece_style": self.piece_style,
            "search_time": self.level,
            "show_move_in_title": self.show_title,
            "speak_moves": self.speak_moves,
            "speak_human": self.speak_human,
            "listen": self.listen_moves,
            "show_log": self.show_log.get(),
            "recent": self.recent,
        })
        save_settings(self.settings)

    def quit_app(self) -> None:
        self._persist()
        self.primary.close()
        self.alternate.close()
        self.listener.stop()
        SJENG.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    import sys
    from board_gl import enable_dpi
    enable_dpi()
    app = ChessApp()
    # A .game or .pgn passed on the command line opens like a Finder double-click.
    if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
        app.root.after(300, lambda: app._open_path(Path(sys.argv[1])))
    app.run()


if __name__ == "__main__":
    main()
