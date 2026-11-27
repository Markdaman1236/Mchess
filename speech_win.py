"""Windows speech for the moves the original speaks and hears.

MBCSpeakMoves and MBCListenForMoves default to on in Defaults.plist.
-[MBCBoardWin primarySynth] uses MBCDefaultVoice and alternateSynth uses
MBCAlternateVoice, which is Victoria on the Mac. Victoria does not exist
on Windows, so the alternate voice defaults to the first installed female
voice. The listener's phrases are the ones in SpeechHelp.plist.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEAK = ROOT / "speak.ps1"
LISTEN = ROOT / "listen.ps1"
VOICES = ROOT / "voices.ps1"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SPEAK.write_text(
    """
param([string]$Voice = '', [string]$Female = '')
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($Voice) {
    try { $synth.SelectVoice($Voice) } catch {}
} elseif ($Female) {
    $pick = $synth.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Gender -eq 'Female' } | Select-Object -First 1
    if ($pick) { $synth.SelectVoice($pick.VoiceInfo.Name) }
}
while ($null -ne ($line = [Console]::In.ReadLine())) {
    if ($line -eq 'QUIT') { break }
    try { $synth.Speak($line) } catch {}
}
""".strip(),
    encoding="utf-8",
)

VOICES.write_text(
    """
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.GetInstalledVoices() | ForEach-Object { Write-Output $_.VoiceInfo.Name }
""".strip(),
    encoding="utf-8",
)

LISTEN.write_text(
    """
param([string]$Grammar)
Add-Type -AssemblyName System.Speech
try {
    $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
    $engine.SetInputToDefaultAudioDevice()
} catch {
    [Console]::Out.WriteLine('LISTEN_OFF')
    exit 0
}
# The phrase file is rewritten for every position, like
# -[MBCInteractivePlayer doUpdateNeedMouse] rebuilding its language model
# from the legal moves. An empty file means: not listening right now.
$stamp = -1
while ($true) {
    if (-not (Test-Path -LiteralPath $Grammar)) { exit 0 }
    $now = (Get-Item -LiteralPath $Grammar).LastWriteTimeUtc.Ticks
    if ($now -ne $stamp) {
        $stamp = $now
        $engine.UnloadAllGrammars()
        $lines = @(Get-Content -LiteralPath $Grammar -Encoding UTF8 | Where-Object { $_ })
        if ($lines.Count -gt 0) {
            $choices = New-Object System.Speech.Recognition.Choices
            foreach ($line in $lines) { [void]$choices.Add($line) }
            $builder = New-Object System.Speech.Recognition.GrammarBuilder($choices)
            $engine.LoadGrammar((New-Object System.Speech.Recognition.Grammar($builder)))
        }
    }
    if ($engine.Grammars.Count -eq 0) { Start-Sleep -Milliseconds 200; continue }
    try { $result = $engine.Recognize([TimeSpan]::FromMilliseconds(500)) } catch { $result = $null }
    if ($result -and $result.Text) {
        [Console]::Out.WriteLine($result.Text)
        [Console]::Out.Flush()
    }
}
""".strip(),
    encoding="utf-8",
)


def installed_voices() -> list[str]:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(VOICES)],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


class Speaker:
    """One synthesizer. ``voice`` empty means the system default voice."""

    def __init__(self, voice: str = "", female_fallback: bool = False) -> None:
        self.voice = voice
        self.female_fallback = female_fallback
        self.proc: subprocess.Popen | None = None

    def set_voice(self, voice: str) -> None:
        if voice != self.voice:
            self.close()
            self.voice = voice

    def _ensure(self) -> bool:
        if self.proc is not None and self.proc.poll() is None:
            return True
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SPEAK)]
        if self.voice:
            args += ["-Voice", self.voice]
        elif self.female_fallback:
            args += ["-Female", "1"]
        try:
            self.proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                creationflags=NO_WINDOW,
            )
        except OSError:
            self.proc = None
            return False
        return True

    def say(self, text: str) -> None:
        if not text or not self._ensure() or self.proc is None or self.proc.stdin is None:
            return
        try:
            self.proc.stdin.write(text.replace("\n", " ") + "\n")
            self.proc.stdin.flush()
        except OSError:
            self.proc = None

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None and self.proc.stdin is not None:
            try:
                self.proc.stdin.write("QUIT\n")
                self.proc.stdin.flush()
            except OSError:
                pass
        self.proc = None


class Listener:
    """Speech recognition limited to the phrases set_phrases() last gave."""

    def __init__(self) -> None:
        import os
        import tempfile
        self.proc: subprocess.Popen | None = None
        self.queue: list[str] = []
        self._lock = threading.Lock()
        self._phrases: list[str] | None = None
        self.grammar = Path(tempfile.gettempdir()) / f"chess-speech-{os.getpid()}.txt"

    def set_phrases(self, phrases: list[str]) -> None:
        """Listen for exactly these phrases; an empty list pauses listening."""
        if phrases == self._phrases:
            return
        self._phrases = list(phrases)
        temp = self.grammar.with_suffix(".tmp")
        try:
            temp.write_text("\n".join(phrases) + ("\n" if phrases else ""), encoding="utf-8")
            temp.replace(self.grammar)
        except OSError:
            pass

    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        if self._phrases is None:
            self.set_phrases([])
        try:
            self.proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LISTEN),
                 "-Grammar", str(self.grammar)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                creationflags=NO_WINDOW,
            )
        except OSError:
            self.proc = None
            return
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            words = line.strip()
            if not words or words == "LISTEN_OFF":
                continue
            with self._lock:
                self.queue.append(words)

    def pull(self) -> list[str]:
        with self._lock:
            found = self.queue[:]
            self.queue.clear()
        return found

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None

    def close(self) -> None:
        self.stop()
        for path in (self.grammar, self.grammar.with_suffix(".tmp")):
            try:
                path.unlink()
            except OSError:
                pass
