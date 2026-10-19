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
Add-Type -AssemblyName System.Speech
try {
    $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
    $engine.SetInputToDefaultAudioDevice()
} catch {
    Write-Output 'LISTEN_OFF'
    exit 0
}
function Choices($items) {
    $c = New-Object System.Speech.Recognition.Choices
    foreach ($item in $items) { [void]$c.Add($item) }
    return $c
}
$files = Choices @('a','b','c','d','e','f','g','h')
$ranks = Choices @('1','2','3','4','5','6','7','8')
$pieces = Choices @('pawn','knight','bishop','rook','queen','king')
$move = New-Object System.Speech.Recognition.GrammarBuilder
$move.Append($pieces)
$move.Append($files)
$move.Append($ranks)
$move.Append((Choices @('to','takes')))
$move.Append($files)
$move.Append($ranks)
$engine.LoadGrammar((New-Object System.Speech.Recognition.Grammar($move)))
$drop = New-Object System.Speech.Recognition.GrammarBuilder
$drop.Append('drop')
$drop.Append((Choices @('pawn','knight','bishop','rook','queen')))
$drop.Append('at')
$drop.Append($files)
$drop.Append($ranks)
$engine.LoadGrammar((New-Object System.Speech.Recognition.Grammar($drop)))
$other = New-Object System.Speech.Recognition.GrammarBuilder
$other.Append((Choices @('castle kingside','castle king side','castle queenside','castle queen side','take back move')))
$engine.LoadGrammar((New-Object System.Speech.Recognition.Grammar($other)))
$engine.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
Register-ObjectEvent -InputObject $engine -EventName SpeechRecognized -Action {
    $text = $Event.SourceEventArgs.Result.Text
    if ($text) { Write-Output $text }
} | Out-Null
while ($true) { Start-Sleep -Seconds 1 }
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
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.queue: list[str] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        try:
            self.proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LISTEN)],
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
