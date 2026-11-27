"""Windows 11 chess app modeled on Apple Chess 3.18.

Run: python chess_app.py [game.game | game.pgn]

The board, variants, menus, dialogs, speech and files follow Apple's
source for this exact build (apple-oss-distributions/Chess, tag
Chess-466.4.1, which is the bundle's MBCBuildTag), the Mach-O in IDA, and
the strings and nibs in its Resources folder. The 3D board is the OpenGL
scene from MBCBoardView. The computer opponent is Sjeng 11.2 built for
Windows (sjeng_engine.py), with chess_engine.py as a fallback.

Like the NSDocument app, every game has its own window (GameWindow):
New… and Open… open more windows, the Window menu lists them, saved games
are written back as they change (autosavesInPlace), and the windows open
at quit come back on the next launch.
"""

from __future__ import annotations

import math
import queue
import re
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from board_gl import (
    HAND_BASE,
    REGION_FRAME,
    REGION_PROMO_BLACK,
    REGION_PROMO_WHITE,
    SYNTHETIC_SQUARE,
    GLBoard,
    hand_center,
    square_center,
)
from chess_engine import SJENG, choose_move
from chess_rules import (
    BISHOP,
    BLACK,
    CRAZYHOUSE,
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
from log_view import MoveLog
from pgn_io import (
    check_state,
    display_move,
    find_spoken,
    load_game,
    load_pgn,
    move_parts,
    save_game,
    save_pgn,
    speech_phrases,
    spoken_move,
    spoken_result,
)
from settings_store import AUTOSAVE_DIR
from settings_store import load as load_settings
from settings_store import save as save_settings
from speech_win import Listener, Speaker, installed_voices

ROOT = Path(__file__).resolve().parent
RESOURCES = ROOT / "Resources"
ICON = RESOURCES / "Chess.ico"
ABOUT_IMAGE = RESOURCES / "Chess_128x128.png"
LICENSE = RESOURCES / "APPLE_LICENSE.txt"
VERSION = "3.18"
BUILD_TAG = "Chess-466.4.1"
SOURCE_URL = "https://github.com/apple-oss-distributions/Chess/tree/Chess-466.4.1"

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
# Board.nib: the main window's content is 971 x 680 points (a 741 point
# board and the 230 point log), at least 500 x 500.
WINDOW_W, WINDOW_H, WINDOW_MIN = 971, 680, 500
# +[MBCBoardAnimation boardAnimation:]: half a turn in two seconds.
BOARD_TURN_SECONDS = 2.0


def facing(azimuth: float) -> int | None:
    """-[MBCBoardView facing]: white (0), black (1), or neither within
    5 degrees of a side-on view."""
    azimuth %= 360.0
    if 95.0 < azimuth < 265.0:
        return 0
    if azimuth < 85.0 or azimuth > 275.0:
        return 1
    return None


class ChessApp:
    """NSApplication and NSDocumentController: the hidden Tk root, shared
    settings, voices, the speech listener, and the open game windows."""

    def __init__(self) -> None:
        self.tk = tk.Tk()
        self.tk.withdraw()
        if ICON.exists():
            try:
                self.tk.iconbitmap(default=str(ICON))
            except tk.TclError:
                pass
        self.scale = max(self.tk.winfo_fpixels("1i") / 96.0, 1.0)
        self.settings = load_settings()
        self.recent: list[str] = list(self.settings.get("recent", []))
        self.windows: list[GameWindow] = []
        self.primary = Speaker(self.settings["primary_voice"])
        self.alternate = Speaker(self.settings["alternate_voice"], female_fallback=True)
        self.listener = Listener()
        self.listen_owner: GameWindow | None = None
        self.quitting = False
        if self.settings["listen"]:
            self.listener.start()
        if not self._restore_sessions():
            players = int(self.settings["players"])
            if players in (2, 3):
                # -[MBCDocument initWithType:]: accept computer-first games,
                # but reset the default for the next untitled game.
                self.settings["players"] = 1
                save_settings(self.settings)
            self.open_window(players=players, variant=int(self.settings["variant"]))
        self.tk.after(30, self._poll)

    # ----- windows ---------------------------------------------------------

    def open_window(self, **kwargs) -> "GameWindow":
        window = GameWindow(self, **kwargs)
        self.windows.append(window)
        self.refresh_menus()
        return window

    def window_closed(self, window: "GameWindow") -> None:
        if window in self.windows:
            self.windows.remove(window)
        if self.listen_owner is window:
            self.listen_owner = None
            self.listener.set_phrases([])
        if not self.windows and not self.quitting:
            self._shutdown()
        else:
            self.refresh_menus()

    def window_for(self, path: Path) -> "GameWindow | None":
        for window in self.windows:
            if window.doc_path is not None and window.doc_path.resolve() == path.resolve():
                return window
        return None

    def open_path(self, path: Path, origin: "GameWindow | None" = None) -> None:
        """-[NSDocumentController openDocument:]: a game already open comes
        to the front; an untouched untitled window is reused; otherwise the
        game gets a window of its own."""
        existing = self.window_for(path)
        if existing is not None:
            existing.bring_to_front()
            return
        try:
            if path.suffix.lower() == ".pgn":
                pos, moves, props = load_pgn(path)
            else:
                pos, moves, props = load_game(path)
        except Exception as exc:
            messagebox.showerror("Chess", f"The game could not be opened.\n\n{exc}",
                                 parent=origin.root if origin else None)
            return
        doc = path if path.suffix.lower() == ".game" else None
        target = origin if origin is not None and origin.is_ephemeral() else None
        if target is None:
            target = next((w for w in self.windows if w.is_ephemeral()), None)
        if target is not None:
            target.load(pos, moves, props, doc)
            target.bring_to_front()
        else:
            self.open_window(loaded=(pos, moves, props, doc))
        self.remember(path)

    def new_game(self, origin: "GameWindow | None" = None) -> None:
        """-[MBCController newGame:]: a new window with the "Start a New
        Game?" sheet; Cancel closes that window again."""
        # Until the sheet is answered the window shows an idle board (two
        # humans), so no computer starts thinking behind the sheet.
        window = self.open_window(players=0, variant=int(self.settings["variant"]))
        window.root.after(150, window.new_game_sheet)

    def refresh_menus(self) -> None:
        for window in self.windows:
            window.refresh_recent()

    def remember(self, path: Path) -> None:
        text = str(path)
        self.recent = [text] + [item for item in self.recent if item != text]
        self.recent = self.recent[:10]
        self.persist()
        self.refresh_menus()

    def clear_recent(self) -> None:
        self.recent = []
        self.persist()
        self.refresh_menus()

    def persist(self, window: "GameWindow | None" = None) -> None:
        if window is not None:
            self.settings.update({
                "board_angle": window.elevation,
                "board_spin": window.azimuth % 360.0,
                "board_style": window.board_style,
                "piece_style": window.piece_style,
                "search_time": window.level,
                "show_move_in_title": window.show_title,
                "speak_moves": window.speak_moves,
                "speak_human": window.speak_human,
                "listen": window.listen_moves,
                "show_log": window.show_log.get(),
            })
        self.settings["recent"] = self.recent
        save_settings(self.settings)

    # ----- speech ------------------------------------------------------------

    def set_listening(self, window: "GameWindow", phrases: list[str] | None) -> None:
        """Only the main window listens; phrases None means it stopped."""
        if phrases:
            self.listen_owner = window
            self.listener.set_phrases(phrases)
        elif self.listen_owner is window or phrases is not None:
            if self.listen_owner is window:
                self.listen_owner = None
            if self.listen_owner is None:
                self.listener.set_phrases([])

    def set_listen_enabled(self, enabled: bool) -> None:
        self.settings["listen"] = enabled
        for window in self.windows:
            window.listen_moves = enabled
            window.listen_key = None
        if enabled:
            self.listener.start()
        else:
            self.listen_owner = None
            self.listener.set_phrases([])
            self.listener.stop()
        for window in self.windows:
            window.update_listening()

    def _poll(self) -> None:
        if self.quitting:
            return
        for phrase in self.listener.pull():
            if self.listen_owner is not None and self.listen_owner in self.windows:
                self.listen_owner.hear(phrase)
        self.tk.after(30, self._poll)

    # ----- quitting and restoring ------------------------------------------------

    def quit(self, window: "GameWindow | None" = None) -> None:
        """Quit Chess: every open game is autosaved and comes back next time."""
        if self.quitting:
            return
        self.quitting = True
        source = window or (self.windows[-1] if self.windows else None)
        if source is not None:
            source.save_frame()
            self.persist(source)
        sessions = []
        try:
            AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)
            for old in AUTOSAVE_DIR.glob("*.game"):
                old.unlink()
        except OSError:
            pass
        for index, game in enumerate(list(self.windows), 1):
            entry = game.autosave(AUTOSAVE_DIR / f"Game {index}.game")
            if entry is not None:
                sessions.append(entry)
        self.settings["sessions"] = sessions
        for game in list(self.windows):
            game.destroy()
        self.windows.clear()
        self._shutdown()

    def _shutdown(self) -> None:
        self.quitting = True
        self.persist()
        self.primary.close()
        self.alternate.close()
        self.listener.close()
        SJENG.close()
        self.tk.destroy()

    def _restore_sessions(self) -> bool:
        restored = False
        for entry in self.settings.get("sessions") or []:
            path = AUTOSAVE_DIR / str(entry.get("file", ""))
            if not path.is_file():
                continue
            try:
                pos, moves, props = load_game(path)
            except Exception:
                continue
            doc = Path(entry["doc"]) if entry.get("doc") else None
            if doc is not None and not doc.exists():
                doc = None
            window = self.open_window(loaded=(pos, moves, props, doc))
            window.dirty = doc is None and bool(moves)
            restored = True
        self.settings["sessions"] = []
        return restored

    def run(self) -> None:
        self.tk.mainloop()


class GameWindow:
    """NSDocument + MBCBoardWin: one game and its window."""

    def __init__(self, app: ChessApp, players: int = 1, variant: int = 0,
                 loaded: tuple | None = None) -> None:
        self.app = app
        self.settings = app.settings
        self.scale = app.scale
        self.root = tk.Toplevel(app.tk)
        self.root.withdraw()
        self.root.title("Chess")
        self.root.configure(bg=BG)
        self.board_style = self.settings["board_style"]
        self.piece_style = self.settings["piece_style"]
        self.level = int(self.settings["search_time"])
        self.players = players
        self.elevation = float(self.settings["board_angle"])
        self.azimuth = float(self.settings["board_spin"]) % 360.0
        self.speak_moves = bool(self.settings["speak_moves"])
        self.speak_human = bool(self.settings["speak_human"])
        self.listen_moves = bool(self.settings["listen"])
        self.show_log = tk.BooleanVar(value=bool(self.settings["show_log"]))
        self.show_title = bool(self.settings["show_move_in_title"])
        self.fullscreen = False
        self.pos = Position(variant)
        self.moves: list[Move] = []
        self.texts: list[str] = []
        self.parts: list[tuple[str, str, str]] = []
        self.props: dict = {}
        self.doc_path: Path | None = None
        self.dirty = False
        self.closed = False
        self.anim = None
        self.board_turn = None
        self.drag = None
        self.picked = None          # square, or HAND_BASE + code for a hand piece
        self.key_buffer = ""
        self.default_promo = {WHITE: QUEEN, BLACK: QUEEN}
        self.hint_move = None
        self.hint_ready = False
        self.last_arrow = None
        self.ponder: Move | None = None
        self.game_over: str | None = None
        self.thinking = False
        self.token = 0
        self.dont_move_before = 0.0
        self.focused = False
        self.listen_key = None
        self.primary = app.primary
        self.alternate = app.alternate
        self._engine_results: queue.Queue = queue.Queue()
        self._hints: queue.Queue = queue.Queue()
        self._build()
        self._place_window()
        if loaded is not None:
            self.load(*loaded)
        else:
            self._start_game(players, variant)
        self.root.deiconify()
        if self.settings.get("window_zoomed"):
            self.root.state("zoomed")
        self.root.after(30, self._poll)

    # ----- window -----------------------------------------------------

    def _px(self, points: float) -> int:
        return int(round(points * self.scale))

    def _build(self) -> None:
        self._menu()
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)
        self.log = MoveLog(outer, self.scale, self.edit_info)
        self.log.pack(side="right", fill="y")
        self.board = GLBoard(outer, self.on_square, self.on_orbit)
        self.board.on_click_at = self.on_click_at
        self.board.on_drop = self.on_drop
        self.board.on_dragging = self.on_dragging
        self.board.pack(side="left", fill="both", expand=True)
        self.log.show(self.show_log.get(), animate=False)
        self.root.bind("<Key>", self.on_key)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _place_window(self) -> None:
        """Every window opens at the MBCMainWindowFrame autosave (the
        original turns off cascading), or at the nib size."""
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.root.minsize(self._px(WINDOW_MIN), self._px(WINDOW_MIN))
        frame = str(self.settings.get("window_frame") or "")
        match = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", frame)
        if match:
            w, h, x, y = (int(part) for part in match.groups())
            if w <= screen_w and h <= screen_h and -w // 2 < x < screen_w - 80 and 0 <= y < screen_h - 80:
                self.root.geometry(frame)
                return
        w = min(self._px(WINDOW_W), screen_w - 40)
        h = min(self._px(WINDOW_H), screen_h - 120)
        self.root.geometry(f"{w}x{h}+{max((screen_w - w) // 2, 0)}+{max((screen_h - h) // 3, 0)}")

    def save_frame(self) -> None:
        if self.fullscreen:
            self.toggle_fullscreen()
        zoomed = self.root.state() == "zoomed"
        self.settings["window_zoomed"] = zoomed
        if not zoomed and self.root.state() == "normal":
            self.settings["window_frame"] = self.root.geometry()

    def _menu(self) -> None:
        """MainMenu.nib: Chess, Game, Edit, Moves, Window, Help."""
        app = self.app
        bar = tk.Menu(self.root)
        chess = tk.Menu(bar, tearoff=0)
        chess.add_command(label="About Chess", command=self.about)
        chess.add_separator()
        chess.add_command(label="Preferences…", command=self.preferences, accelerator="Ctrl+,")
        chess.add_separator()
        chess.add_command(label="Hide Chess", command=self.hide_app, accelerator="Ctrl+H")
        chess.add_separator()
        chess.add_command(label="Quit Chess", command=lambda: app.quit(self), accelerator="Ctrl+Q")
        bar.add_cascade(label="Chess", menu=chess)
        game = tk.Menu(bar, tearoff=0)
        game.add_command(label="New…", command=lambda: app.new_game(self), accelerator="Ctrl+N")
        game.add_separator()
        game.add_command(label="Open…", command=self.open_file, accelerator="Ctrl+O")
        self.recent_menu = tk.Menu(game, tearoff=0)
        game.add_cascade(label="Open Recent", menu=self.recent_menu)
        game.add_separator()
        game.add_command(label="Close", command=self.close, accelerator="Ctrl+W")
        game.add_command(label="Save", command=self.save, accelerator="Ctrl+S")
        game.add_command(label="Save As…", command=self.save_as, accelerator="Ctrl+Shift+S")
        bar.add_cascade(label="Game", menu=game)
        self.edit_menu = tk.Menu(bar, tearoff=0, postcommand=self._validate_edit)
        for label, event, accel in (
            ("Undo", "<<Undo>>", ""), ("Redo", "<<Redo>>", ""), (None, None, None),
            ("Cut", "<<Cut>>", "Ctrl+X"), ("Copy", "<<Copy>>", "Ctrl+C"), ("Paste", "<<Paste>>", "Ctrl+V"),
            ("Delete", "<<Clear>>", ""), ("Select All", "<<SelectAll>>", "Ctrl+A"),
        ):
            if label is None:
                self.edit_menu.add_separator()
            else:
                self.edit_menu.add_command(label=label, accelerator=accel,
                                           command=lambda e=event: self._edit_event(e))
        bar.add_cascade(label="Edit", menu=self.edit_menu)
        self.moves_menu = tk.Menu(bar, tearoff=0, postcommand=self._validate_moves)
        self.moves_menu.add_command(label="Take Back Move", command=self.take_back, accelerator="Ctrl+Z")
        self.moves_menu.add_separator()
        self.moves_menu.add_command(label="Resign", command=self.resign)
        # -[MBCDocument offerDraw] only reaches a Game Center opponent.
        self.moves_menu.add_command(label="Offer Draw", state="disabled")
        self.moves_menu.add_separator()
        self.moves_menu.add_command(label="Show Hint", command=self.show_hint, accelerator="Ctrl+]")
        self.moves_menu.add_command(label="Show Last Move", command=self.show_last_move, accelerator="Ctrl+[")
        self.moves_menu.add_separator()
        self.moves_menu.add_checkbutton(label="Game Log", variable=self.show_log, command=self.toggle_log,
                                        accelerator="Ctrl+L")
        # Game Center achievements have no Windows counterpart.
        self.moves_menu.add_command(label="Achievements", state="disabled")
        bar.add_cascade(label="Moves", menu=self.moves_menu)
        self.window_menu = tk.Menu(bar, tearoff=0, postcommand=self._validate_window)
        bar.add_cascade(label="Window", menu=self.window_menu)
        help_menu = tk.Menu(bar, tearoff=0)
        help_menu.add_command(label="Chess Help", command=self.help, accelerator="Ctrl+?")
        bar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=bar)
        self._validate_window()
        binds = {
            "<Control-comma>": self.preferences,
            "<Control-h>": self.hide_app,
            "<Control-q>": lambda: app.quit(self),
            "<Control-n>": lambda: app.new_game(self),
            "<Control-o>": self.open_file,
            "<Control-w>": self.close,
            "<Control-s>": self.save,
            "<Control-S>": self.save_as,
            "<Control-z>": self.take_back,
            "<Control-bracketright>": self.show_hint,
            "<Control-bracketleft>": self.show_last_move,
            "<Control-l>": lambda: (self.show_log.set(not self.show_log.get()), self.toggle_log()),
            "<Control-m>": self.root.iconify,
            "<F11>": self.toggle_fullscreen,
            "<Control-question>": self.help,
            "<F1>": self.help,
        }
        for key, func in binds.items():
            self.root.bind(key, lambda _e, f=func: (f(), "break")[1])

    def _validate_moves(self) -> None:
        """-[MBCBoardWin validateMenuItem:]: Take Back follows canTakeback."""
        self.moves_menu.entryconfigure("Take Back Move", state="normal" if self.can_take_back() else "disabled")

    def _validate_window(self) -> None:
        """The Window menu, with the open windows listed at the bottom."""
        menu = self.window_menu
        menu.delete(0, "end")
        menu.add_command(label="Close", command=self.close, accelerator="Ctrl+W")
        menu.add_command(label="Minimize", command=self.root.iconify, accelerator="Ctrl+M")
        menu.add_command(label="Zoom", command=self.zoom)
        menu.add_command(label="Exit Full Screen" if self.fullscreen else "Enter Full Screen",
                         command=self.toggle_fullscreen, accelerator="F11")
        menu.add_separator()
        menu.add_command(label="Bring All to Front", command=self.app_to_front)
        if self.app.windows:
            menu.add_separator()
            for window in self.app.windows:
                mark = "✓ " if window is self else "    "
                menu.add_command(label=mark + window.title_text(), command=window.bring_to_front)

    def _validate_edit(self) -> None:
        widget = self._focus_widget()
        text = isinstance(widget, (tk.Entry, ttk.Entry, tk.Text))
        for index in range(self.edit_menu.index("end") + 1):
            if self.edit_menu.type(index) == "command":
                self.edit_menu.entryconfigure(index, state="normal" if text else "disabled")

    def _focus_widget(self):
        try:
            return self.root.focus_get()
        except (KeyError, tk.TclError):
            return None

    def _edit_event(self, event: str) -> None:
        widget = self._focus_widget()
        if widget is not None:
            widget.event_generate(event)

    def bring_to_front(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def app_to_front(self) -> None:
        for window in self.app.windows:
            if window is not self:
                window.root.deiconify()
                window.root.lift()
        self.bring_to_front()

    def hide_app(self) -> None:
        for window in self.app.windows:
            window.root.iconify()

    def refresh_recent(self) -> None:
        self.recent_menu.delete(0, "end")
        for path in self.app.recent:
            self.recent_menu.add_command(label=Path(path).name,
                                         command=lambda p=path: self.app.open_path(Path(p), self))
        if self.app.recent:
            self.recent_menu.add_separator()
        self.recent_menu.add_command(label="Clear Menu", command=self.app.clear_recent)

    # ----- sides --------------------------------------------------------

    def human_side(self) -> int:
        return HUMAN_SIDE[self.players]

    def engine_side(self) -> int:
        """-[MBCDocument engineSide]: 0 white, 1 black, 2 both, 3 neither."""
        return {0: 3, 1: 1, 2: 0, 3: 2}[self.players]

    def is_human(self, side: int) -> bool:
        human = self.human_side()
        return human == 2 or (human == 0 and side == WHITE) or (human == 1 and side == BLACK)

    def human_to_move(self) -> bool:
        return not self.game_over and self.is_human(self.pos.side)

    # ----- game state -----------------------------------------------------

    def is_ephemeral(self) -> bool:
        """An untitled window nobody has played in yet (setEphemeral:)."""
        return self.doc_path is None and not self.moves and not self.closed

    def _start_game(self, players: int, variant: int, props: dict | None = None,
                    pos: Position | None = None, moves: list[Move] | None = None) -> None:
        self.token += 1
        self.thinking = False
        self.players = players
        self.pos = pos or Position(variant)
        self.moves = list(moves or [])
        self.texts = []
        self.parts = []
        replay = Position(self.pos.variant)
        for move in self.moves:
            self.texts.append(display_move(replay, move))
            self.parts.append(move_parts(replay, move))
            replay.push(move)
        self.anim = None
        self.board_turn = None
        self.drag = None
        self.picked = None
        self.key_buffer = ""
        self.default_promo = {WHITE: QUEEN, BLACK: QUEEN}
        self.hint_move = None
        self.hint_ready = False
        self.last_arrow = None
        self.ponder = None
        self.dont_move_before = 0.0
        self.game_over = self.pos.outcome() if self.moves else None
        self.props = self._default_props()
        if props:
            self.props.update({k: v for k, v in props.items() if not k.startswith("_")})
        if self.game_over and self.props.get("Result", "*") == "*":
            self.props["Result"] = self.game_over
        # -[MBCBoardView startGame:playing:] turns the board to the human
        # unless it is side-on.
        human = self.human_side()
        if human != 3 and facing(self.azimuth) is not None:
            side = (len(self.moves) & 1) if human == 2 else human
            if facing(self.azimuth) != side:
                self.azimuth = (self.azimuth + 180.0) % 360.0
        self.listen_key = None
        self.refresh()
        self.app.refresh_menus()
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

    def load(self, pos: Position, moves: list[Move], props: dict, doc_path: Path | None) -> None:
        players = int(props.get("_players", 0))
        if "MBCSearchTime" in props:
            try:
                self.level = int(props["MBCSearchTime"])
            except (TypeError, ValueError):
                pass
        self.doc_path = doc_path
        self.dirty = False
        self._start_game(players, pos.variant, props, pos, moves)

    def _changed(self) -> None:
        """updateChangeCount:, with autosavesInPlace writing a saved game
        back to its file."""
        self.dirty = True
        if self.doc_path is not None:
            try:
                self._write(self.doc_path, remember=False)
            except OSError:
                pass

    def _keep_untitled(self) -> bool:
        """Closing an untitled, autosaved game asks whether to keep it.
        Returns False to cancel the close."""
        if self.doc_path is not None or not self.moves or not self.dirty:
            return True
        self.bring_to_front()
        box = tk.Toplevel(self.root)
        box.title("Chess")
        box.resizable(False, False)
        box.transient(self.root)
        box.configure(bg=BG, padx=self._px(18), pady=self._px(14))
        tk.Label(box, text='Do you want to keep this new document "Untitled"?', bg=BG,
                 font=("Segoe UI", 10, "bold"), justify="left").pack(anchor="w")
        tk.Label(box, text="You can choose to save your changes, or delete this document\n"
                           "immediately. You can't undo this action.", bg=BG, justify="left").pack(anchor="w", pady=(6, 12))
        choice = {"value": "cancel"}

        def pick(value: str) -> None:
            choice["value"] = value
            box.destroy()

        row = tk.Frame(box, bg=BG)
        row.pack(fill="x")
        ttk.Button(row, text="Delete", command=lambda: pick("delete")).pack(side="left")
        ttk.Button(row, text="Save…", command=lambda: pick("save"), default="active").pack(side="right")
        ttk.Button(row, text="Cancel", command=lambda: pick("cancel")).pack(side="right", padx=6)
        box.bind("<Escape>", lambda _e: pick("cancel"))
        box.bind("<Return>", lambda _e: pick("save"))
        box.grab_set()
        self.root.wait_window(box)
        if choice["value"] == "save":
            return self.save_as()
        return choice["value"] == "delete"

    def close(self) -> None:
        """performClose:. Closing the last window quits, as Windows apps do,
        and like Quit it keeps the game for the next launch."""
        if len(self.app.windows) <= 1:
            self.app.quit(self)
            return
        if not self._keep_untitled():
            return
        if self.doc_path is not None and self.dirty:
            try:
                self._write(self.doc_path, remember=False)
            except OSError:
                pass
        self.save_frame()
        self.app.persist(self)
        self.destroy()
        self.app.window_closed(self)

    def destroy(self) -> None:
        self.closed = True
        self.token += 1
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def autosave(self, path: Path) -> dict | None:
        """Write the game for window restoration; None if there is nothing
        worth restoring (an untouched untitled game)."""
        if not self.moves and self.doc_path is None:
            return None
        white_program, black_program = self._program_sides()
        self.props["MBCSearchTime"] = self.level
        try:
            if self.doc_path is not None and self.dirty:
                self._write(self.doc_path, remember=False)
            save_game(path, self.props, self.pos.variant, self.moves, white_program, black_program)
        except OSError:
            return None
        return {"file": path.name, "doc": str(self.doc_path or "")}

    def new_game_sheet(self) -> None:
        """The "Start a New Game?" sheet from Board.nib. Cancel closes the
        window it was opened for (-[MBCBoardWin cancelNewGame:])."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Start a New Game?")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.configure(bg=BG, padx=16, pady=12)
        tk.Label(dialog, text="Start a New Game?", bg=BG, font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        tk.Label(dialog, text="Players:", bg=BG).grid(row=1, column=0, sticky="e", padx=(0, 8))
        players = tk.StringVar(value=PLAYER_CHOICES[int(self.settings["players"])])
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
        if self.closed:
            return
        if not picked["ok"]:
            self.destroy()
            self.app.window_closed(self)
            return
        self.level = level.get()
        self.settings["players"] = PLAYER_CHOICES.index(players.get())
        self.settings["variant"] = VARIANT_CHOICES.index(variant.get())
        self.doc_path = None
        self.dirty = False
        self.app.persist(self)
        self._start_game(self.settings["players"], self.settings["variant"])

    def preferences(self) -> None:
        """The Preferences sheet from Board.nib: style, speech and strength.
        Speak Computer Moves hides in human vs human (hideSpeakMoves), Speak
        Human Moves in computer vs computer (hideSpeakHumanMoves), and the
        strength box without a computer (hideEngineProperties)."""
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
        speak_box = ttk.Checkbutton(speech, text="Speak Computer Moves", variable=speak)
        speak_box.grid(row=0, column=0, sticky="w")
        ttk.Label(speech, text="Primary Voice").grid(row=0, column=1, sticky="w", padx=(12, 4))
        ttk.Combobox(speech, textvariable=primary, values=voices, state="readonly", width=30).grid(row=0, column=2)
        human_box = ttk.Checkbutton(speech, text="Speak Human Moves", variable=speak_human)
        human_box.grid(row=1, column=0, sticky="w", pady=4)
        ttk.Label(speech, text="Alternate Voice").grid(row=1, column=1, sticky="w", padx=(12, 4))
        ttk.Combobox(speech, textvariable=alternate, values=voices, state="readonly", width=30).grid(row=1, column=2)
        ttk.Checkbutton(speech, text="Allow Player to Speak Moves", variable=listen).grid(row=2, column=0, sticky="w")
        if self.human_side() == 2:
            speak_box.grid_remove()
        if self.engine_side() == 2:
            human_box.grid_remove()
        strength = ttk.LabelFrame(dialog, text="Computer", padding=8)
        strength.grid(row=2, column=0, sticky="ew")
        if self.engine_side() == 3:
            strength.grid_remove()
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
        if not picked["ok"] or self.closed:
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
        self.app.persist(self)
        if listen.get() != self.listen_moves:
            self.app.set_listen_enabled(listen.get())
        self.refresh()

    def edit_info(self) -> None:
        """"Edit Game Information" from Board.nib. The name fields are only
        editable for human players (whiteEditable / blackEditable)."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Game Information")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.configure(bg=BG, padx=12, pady=10)
        human = self.human_side()
        editable = {"White": human in (0, 2), "Black": human in (1, 2)}
        fields = {}
        for row, (key, label) in enumerate((("White", "White:"), ("Black", "Black:"), ("City", "Town/City:"),
                                             ("Country", "Country:"), ("Event", "Event:"))):
            tk.Label(dialog, text=label, bg=BG).grid(row=row, column=0, sticky="e", padx=(0, 6), pady=3)
            var = tk.StringVar(value=self.props.get(key, ""))
            entry = ttk.Entry(dialog, textvariable=var, width=30)
            if not editable.get(key, True):
                entry.state(["disabled"])
            entry.grid(row=row, column=1, pady=3)
            fields[key] = var

        def accept() -> None:
            for key, var in fields.items():
                if editable.get(key, True):
                    self.props[key] = var.get()
            if human in (0, 2):
                self.settings["human_name"] = self.props["White"]
            if human == 1:
                self.settings["human_name"] = self.props["Black"]
            if human == 2:
                self.settings["human_name2"] = self.props["Black"]
            self.settings["city"] = self.props["City"]
            self.settings["country"] = self.props["Country"]
            self.settings["event"] = self.props["Event"]
            self.app.persist(self)
            dialog.destroy()
            self._changed()
            self.refresh()
            self.app.refresh_menus()

        buttons = tk.Frame(dialog, bg=BG)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="OK", command=accept, default="active").pack(side="left")
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.grab_set()

    # ----- moving -----------------------------------------------------------

    def _wants_input(self) -> bool:
        """fWantMouse and not fInAnimation."""
        return self.human_to_move() and not self.thinking and self.anim is None and self.board_turn is None

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

    def _own_piece_at(self, target: int | None) -> bool:
        """-[MBCInteractivePlayer startSelection:] accepts the square."""
        if target is None:
            return False
        side = self.pos.side
        if target >= HAND_BASE:
            code = target - HAND_BASE
            return self.pos.variant == CRAZYHOUSE and color_of(code) == side and self.pos.hand[code] > 0
        if 0 <= target < 64:
            piece = self.pos.board[target]
            return bool(piece) and color_of(piece) == side
        return False

    def _press_source(self, region: int | None, height: float, plane: int | None) -> int | None:
        """-[MBCBoardView mouseDown:]: a press starts a selection only on a
        piece (hit at y >= 0.1) of the side to move. A press on the picked
        piece retries on the board plane, since a tall piece can hide the
        square behind it."""
        if region is None or height < 0.1 or not self._wants_input():
            return None
        if region == self.picked and plane is not None and plane < SYNTHETIC_SQUARE:
            region = plane
        return region if self._own_piece_at(region) else None

    def on_click_at(self, x: int, y: int, _sq: int | None) -> None:
        self._mouse_click(*self.board.press_info(x, y))

    def _mouse_click(self, region: int | None, height: float, plane: int | None) -> None:
        """A click: mouseDown: and then either its own tracking loop (a
        piece of the side to move) or mouseUp: (anything else)."""
        if region in (REGION_PROMO_WHITE, REGION_PROMO_BLACK):
            # mouseUp: cycles the default promotion piece.
            if self._wants_input():
                side = WHITE if region == REGION_PROMO_WHITE else BLACK
                cycle = PROMO_CYCLE_SUICIDE if self.pos.variant == SUICIDE else PROMO_CYCLE
                self.default_promo[side] = cycle.get(self.default_promo[side], QUEEN)
                self.refresh()
            return
        if region is None or region == REGION_FRAME or not self._wants_input():
            return
        if height >= 0.1 and region == self.picked and plane is not None and plane < SYNTHETIC_SQUARE:
            region = plane
        if height >= 0.1 and self._own_piece_at(region):
            # clickPiece, and a second click on the picked piece drops it.
            self.picked = None if region == self.picked else region
            self.refresh()
            return
        # mouseUp: moves the picked piece to the clicked square.
        if self.picked is None or region == self.picked or region >= SYNTHETIC_SQUARE:
            return
        self._try_move(self.picked, region, animate=True)

    def on_square(self, sq: int) -> None:
        self.select(sq, animate=True)

    def select(self, target: int, animate: bool = False) -> None:
        """startSelection: / endSelection:animate: for typed squares."""
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
        """The end of a drag. endSelection:animate: with the square under
        the mouse on the board plane (positionToSquare:)."""
        self.drag = None
        region, height, plane = self.board.press_info(x0, y0)
        source = self._press_source(region, height, plane)
        if source is None:
            # mouseDown: returned without tracking, so this was a click.
            self._mouse_click(region, height, plane)
            self.refresh()
            return
        dest = self.board.region_at(x1, y1)
        if dest == source:
            self.picked = None if source == self.picked else source
            self.refresh()
            return
        if dest is None or dest >= SYNTHETIC_SQUARE:
            self.picked = None
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
        source = self._press_source(*self.board.press_info(x0, y0))
        point = self.board.hit_point(x1, y1)
        if source is None or point is None:
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
        parts = move_parts(self.pos, move)
        spoken = spoken_move(self.pos, move)
        check, mate = check_state(self.pos, move)
        self.texts.append(text)
        self.parts.append(parts)
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
        self._changed()
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
        if anim is None or self.closed:
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
        """-[MBCBoardWin commitMove:]: with two humans the board turns
        half way round after every move, unless it is side-on."""
        if self.human_side() == 2 and facing(self.azimuth) is not None:
            self._start_board_turn()
        if not self.game_over:
            self.root.after(40, self.maybe_engine)

    def _start_board_turn(self) -> None:
        turn = {"from": self.azimuth, "began": time.perf_counter(), "elapsed": 0.0}
        self.board_turn = turn
        self._step_board_turn(turn)

    def _step_board_turn(self, turn: dict) -> None:
        """MBCBoardAnimation step: fAzimuth = from + 180 * done, then
        endState sets fmod(from + 180, 360)."""
        if self.board_turn is not turn or self.closed:
            return
        t = min(1.0, (time.perf_counter() - turn["began"]) / BOARD_TURN_SECONDS)
        if t - turn["elapsed"] > 0.5:
            t = 1.0
        turn["elapsed"] = t
        if t >= 1.0:
            self.azimuth = (turn["from"] + 180.0) % 360.0
            self.board_turn = None
            self.refresh()
            return
        self.azimuth = turn["from"] + 180.0 * t
        self.refresh()
        self.root.after(10, lambda: self._step_board_turn(turn))

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

    def update_listening(self) -> None:
        """-[MBCInteractivePlayer doUpdateNeedMouse]: listen only while the
        window is main and the human is to move, for that position's legal
        moves (and "take back move" / "undo" when canUndo)."""
        if not self.listen_moves or self.closed:
            return
        active = self.focused and self._wants_input()
        key = (active, len(self.moves), self.pos.side, self.can_take_back())
        if key == self.listen_key:
            return
        self.listen_key = key
        self.app.set_listening(self, speech_phrases(self.pos, self.can_take_back()) if active else None)

    def hear(self, phrase: str) -> None:
        text = phrase.lower()
        if text in ("take back move", "undo"):
            self.take_back()
            return
        if not self._wants_input():
            return
        move = find_spoken(self.pos, text)
        if move is None:
            self.root.bell()
            return
        if move.promo and "promoting" not in text:
            # tryPromotion: fills in the default promotion piece.
            want = self.default_promo[self.pos.side]
            for other in self.pos.legal_moves():
                if other.fr == move.fr and other.to == move.to and other.promo == want:
                    move = other
                    break
        self.play(move)

    # ----- engine -------------------------------------------------------------

    def maybe_engine(self) -> None:
        if self.thinking or self.game_over or self.anim is not None or self.closed:
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
        if self.closed:
            return
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
        widget = self._focus_widget()
        focused = widget is not None and str(widget.winfo_toplevel()) == str(self.root)
        if focused != self.focused:
            # windowDidBecomeMain: / windowDidResignMain:
            self.focused = focused
            self.update_listening()
        self.root.after(30, self._poll)

    def _engine_done(self, token: int, move: Move | None, ponder: Move | None) -> None:
        if token != self.token or self.closed:
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
        # Sjeng starts pondering on the human's reply once it has moved.
        self.hint_ready = True
        if self.human_side() == 3:
            # handlePortMessage: computer vs computer waits four seconds.
            self.dont_move_before = max(self.dont_move_before, time.perf_counter()) + 4.0
        self.play(move)

    def show_hint(self) -> None:
        """-[MBCBoardWin showHint:] shows [engine lastPonder]: the move
        Sjeng expects from the human while it ponders. Sjeng only ponders
        against a human after it has moved (not in human vs human, where it
        is in force mode, nor straight after a game is loaded), so there is
        no hint then."""
        if self.game_over or not self.human_to_move() or self.human_side() not in (0, 1) or not self.hint_ready:
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

    def can_take_back(self) -> bool:
        """-[MBCDocument canTakeback]: [board canUndo] (two moves or more),
        and not computer vs computer."""
        return len(self.moves) > 1 and self.players != 3 and self.anim is None and self.board_turn is None

    def take_back(self) -> None:
        """-[MBCEngine takeback] sends "remove", which takes back two
        moves: the human's last move and the reply. If the computer is
        still thinking, its search is dropped and only the human's move goes."""
        if not self.can_take_back():
            return
        self.token += 1
        self.thinking = False
        if self.human_side() == 2:
            self._pop()
            self._pop()
        else:
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
        self.hint_ready = bool(self.moves) and self.human_side() in (0, 1)
        self._changed()
        self.refresh()
        self.root.after(200, self.maybe_engine)

    def _pop(self) -> None:
        self.pos.pop()
        self.moves.pop()
        self.texts.pop()
        self.parts.pop()

    def resign(self) -> None:
        """The human side to move loses. (Apple's -[MBCDocument resign] only
        reaches Game Center opponents; here it ends the game.)"""
        if self.game_over or not self.moves or self.players == 3:
            return
        self.token += 1
        self.thinking = False
        side = self.pos.side if self.is_human(self.pos.side) else self.pos.side ^ 8
        self.game_over = "0-1" if side == WHITE else "1-0"
        self.props["Result"] = self.game_over
        self._changed()
        self.refresh()

    # ----- drawing --------------------------------------------------------------

    def title_text(self) -> str:
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
        if self.closed:
            return
        self.root.title(self.title_text())
        self.board.set_busy(self.thinking)
        self.log.set_names(self.props.get("White", ""), self.props.get("Black", ""))
        self.log.set_moves(self.parts)
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
        self.update_listening()

    def on_orbit(self, azimuth: float, elevation: float, done: bool) -> None:
        self.azimuth = azimuth
        self.elevation = elevation
        self.refresh()
        if done:
            # -[MBCBoardWin setAngle:spin:].
            self.app.persist(self)

    # ----- files ------------------------------------------------------------------

    def _program_sides(self) -> tuple[bool, bool]:
        human = self.human_side()
        return human not in (0, 2), human not in (1, 2)

    def save(self) -> bool:
        if self.doc_path is None:
            return self.save_as()
        self._write(self.doc_path)
        return True

    def save_as(self) -> bool:
        path = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension=".game",
            filetypes=[("Chess Game", "*.game"), ("Portable Game Notation", "*.pgn")],
        )
        if not path:
            return False
        self._write(Path(path))
        return True

    def _write(self, path: Path, remember: bool = True) -> None:
        white_program, black_program = self._program_sides()
        self.props["MBCSearchTime"] = self.level
        if path.suffix.lower() == ".pgn":
            save_pgn(path, self.props, self.pos.variant, self.moves, white_program, black_program)
        else:
            save_game(path, self.props, self.pos.variant, self.moves, white_program, black_program)
            self.doc_path = path
            self.dirty = False
        if remember:
            self.app.remember(path)

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            filetypes=[("Chess Games", "*.game *.pgn"), ("Chess Game", "*.game"), ("Portable Game Notation", "*.pgn"), ("All", "*.*")],
        )
        if path:
            self.app.open_path(Path(path), self)

    # ----- window commands ------------------------------------------------------------

    def toggle_log(self) -> None:
        """-[MBCBoardWin toggleLogView:] slides the drawer in or out."""
        self.log.show(self.show_log.get(), animate=True)
        self.app.persist(self)

    def toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)

    def zoom(self) -> None:
        self.root.state("normal" if self.root.state() == "zoomed" else "zoomed")

    def about(self) -> None:
        """About.nib: icon, name, version, the license, credits, "Download
        Source Code…" and the build tag."""
        box = tk.Toplevel(self.root)
        box.title("")
        box.resizable(False, False)
        box.transient(self.root)
        box.configure(bg=BG, padx=self._px(18), pady=self._px(8))
        small = tkfont.Font(family="Segoe UI", size=-self._px(10))
        try:
            from PIL import Image, ImageTk
            image = Image.open(ABOUT_IMAGE).resize((self._px(64), self._px(64)), Image.LANCZOS)
            box._icon = ImageTk.PhotoImage(image)
            tk.Label(box, image=box._icon, bg=BG).pack()
        except Exception:
            pass
        tk.Label(box, text="Chess", bg=BG, font=("Segoe UI", -self._px(14), "bold")).pack(pady=(self._px(6), 0))
        tk.Label(box, text=f"Version {VERSION}", bg=BG, font=small).pack(pady=(self._px(2), self._px(12)))
        text_frame = tk.Frame(box, bg=BG)
        text_frame.pack(fill="both", expand=True)
        license_text = tk.Text(text_frame, width=72, height=14, wrap="word", font=("Consolas", -self._px(11)),
                               relief="solid", borderwidth=1, padx=4, pady=4)
        bar = ttk.Scrollbar(text_frame, orient="vertical", command=license_text.yview)
        license_text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        license_text.pack(side="left", fill="both", expand=True)
        note = ("Windows port of Apple Chess 3.18. The computer opponent is Sjeng 11.2 by "
                "Gian-Carlo Pascutto (GNU General Public License, see engine\\COPYING.txt), "
                "built for Windows from Apple's open-source copy.\n\n")
        try:
            body = LICENSE.read_text(encoding="utf-8", errors="replace")
        except OSError:
            body = ""
        license_text.insert("1.0", note + body)
        license_text.configure(state="disabled")
        bottom = tk.Frame(box, bg=BG)
        bottom.pack(fill="x", pady=(self._px(12), 0))
        tk.Label(bottom, text="© 2003–2017\n© 2000\n© 2002", bg=BG, font=small, justify="left").pack(side="left")
        tk.Label(bottom, text="Apple Inc.\nGian-Carlo Pascutto\nR. Ancell & M. Duelli", bg=BG, font=small,
                 justify="left").pack(side="left", padx=(self._px(8), 0))
        right = tk.Frame(bottom, bg=BG)
        right.pack(side="right")
        ttk.Button(right, text="Download Source Code…", command=lambda: webbrowser.open(SOURCE_URL)).pack()
        tk.Label(right, text=f"Build {BUILD_TAG}", bg=BG, font=small).pack(pady=(self._px(4), 0))
        box.bind("<Escape>", lambda _e: box.destroy())

    def help(self) -> None:
        messagebox.showinfo(
            "Chess Help",
            "Moving: click a piece and then a square, drag a piece, or type\n"
            "squares such as e2 then e4. Type =q, =r, =b or =n to choose the\n"
            "promotion piece, or click the piece shown beside the board.\n\n"
            "Turning the board: drag on the wooden frame.\n\n"
            "Spoken moves (on your turn, while the window is active):\n"
            "  <Piece> <Square> to/takes <Square>  (e.g. Knight g1 to f3)\n"
            "  ... promoting to <Piece>\n"
            "  Castle king side\n  Castle queen side\n"
            "  Drop <Piece> at <Square> (e.g. Drop knight at c7) [Crazyhouse only]\n"
            "  Take back move",
            parent=self.root,
        )


def main() -> None:
    import sys
    from board_gl import enable_dpi
    enable_dpi()
    app = ChessApp()
    # A .game or .pgn passed on the command line opens like a Finder double-click.
    if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
        app.tk.after(300, lambda: app.open_path(Path(sys.argv[1])))
    app.run()


if __name__ == "__main__":
    main()
