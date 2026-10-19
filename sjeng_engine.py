"""Sjeng 11.2, the engine Apple Chess runs, built for Windows.

engine\\sjeng.exe is Apple's sjeng source (apple-oss-distributions/Chess)
compiled with MSVC; see sjeng_src\\README.txt. It speaks the same text
protocol -[MBCEngine init] uses: "xboard", "confirm_moves", "?new",
"variant ...", "sd N", moves such as "e2e4" or "N@f3", "force" and "go",
and answers "move e7e5".

Each request replays the game from the start ("?new", "force", the moves,
"go"), so takebacks and loaded games need no extra bookkeeping. Pondering
is switched off with "easy": in losers its proof-number search does not
stop cleanly for the next request, and it would also use the CPU while
the player thinks. A "ping"/"pong" before each request makes sure no
output from an earlier request is mistaken for the answer.
"""

from __future__ import annotations

import itertools
import queue
import subprocess
import threading
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().with_name("engine")
ENGINE_EXE = ENGINE_DIR / "sjeng.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VARIANT_COMMANDS = {0: "", 1: "variant crazyhouse\n", 2: "variant suicide\n", 3: "variant losers\n"}


class Sjeng:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self._lines: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._pings = itertools.count(1)
        self.failed = False

    def available(self) -> bool:
        return ENGINE_EXE.exists() and not self.failed

    def _ensure(self) -> bool:
        if self.proc is not None and self.proc.poll() is None:
            return True
        try:
            self.proc = subprocess.Popen(
                [str(ENGINE_EXE)],
                cwd=str(ENGINE_DIR),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="ascii",
                errors="replace",
                bufsize=1,
                creationflags=NO_WINDOW,
            )
        except OSError:
            self.proc = None
            self.failed = True
            return False
        self._lines = queue.Queue()
        threading.Thread(target=self._read, args=(self.proc, self._lines), daemon=True).start()
        return self._send("xboard\nconfirm_moves\neasy\n")

    @staticmethod
    def _read(proc: subprocess.Popen, lines: queue.Queue) -> None:
        for raw in proc.stdout:
            lines.put(raw.strip())
        lines.put(None)

    def _send(self, text: str) -> bool:
        try:
            self.proc.stdin.write(text)
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError, AttributeError):
            self.proc = None
            return False

    def _sync(self, timeout: float) -> bool:
        """Send "ping N" and drop everything up to "pong N"."""
        number = next(self._pings)
        if not self._send(f"ping {number}\n"):
            return False
        while True:
            try:
                line = self._lines.get(timeout=timeout)
            except queue.Empty:
                return False
            if line is None:
                self.proc = None
                return False
            if line == f"pong {number}":
                return True

    def best_move(self, variant: int, depth: int, moves: list[str], timeout: float = 600.0) -> str | None:
        """Return Sjeng's move text (e.g. "e7e5", "N@f3") or None."""
        with self._lock:
            if not self._ensure() or not self._sync(10.0):
                return None
            script = "?new\n" + VARIANT_COMMANDS.get(variant, "") + f"sd {depth}\nforce\n"
            script += "".join(move + "\n" for move in moves)
            if not self._send(script) or not self._sync(10.0):
                return None
            if not self._send("go\n"):
                return None
            while True:
                try:
                    line = self._lines.get(timeout=timeout)
                except queue.Empty:
                    self._send("?\n")
                    return None
                if line is None:
                    self.proc = None
                    return None
                if line.startswith("Illegal move"):
                    return None
                if line.startswith("move "):
                    return line[5:].strip()
                if line.startswith(("1-0", "0-1", "1/2-1/2")):
                    return None

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self._send("quit\n")
            try:
                self.proc.wait(2)
            except Exception:
                self.proc.kill()
        self.proc = None


ENGINE = Sjeng()
