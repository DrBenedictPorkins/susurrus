import time

from rich.console import Console
from rich.rule import Rule

_console = Console(highlight=False)
_speak_start: float = 0.0
_last_tick: float = 0.0

# Minimum interval between SILENCE/SPEAKING line redraws (seconds)
_TICK_INTERVAL = 0.1


def server_ready(port: int) -> None:
    _console.print(f"  [bold green]UI[/bold green]  [dim]→[/dim]  [white]http://localhost:{port}[/white]")


def loading(msg: str) -> None:
    _console.print(f"  [dim]{msg}[/dim]")


def ready(port: int) -> None:
    _console.print(Rule(style="dim"))
    _console.print(f"  [dim]Listening on port[/dim] [white]{port}[/white]")
    _console.print(f"  [dim]Ctrl-C to stop[/dim]")
    _console.print(Rule(style="dim"))


def silence() -> None:
    global _last_tick
    now = time.monotonic()
    if now - _last_tick < _TICK_INTERVAL:
        return
    _last_tick = now
    _console.print("  [dim][ SILENCE ]  listening...[/dim]          ", end="\r")


def speech_start() -> None:
    global _speak_start, _last_tick
    _speak_start = time.monotonic()
    _last_tick = 0.0


def speaking() -> None:
    global _last_tick
    now = time.monotonic()
    if now - _last_tick < _TICK_INTERVAL:
        return
    _last_tick = now
    elapsed = now - _speak_start
    _console.print(
        f"  [bold yellow][ SPEAKING  {elapsed:.1f}s ][/bold yellow]          ",
        end="\r",
    )


def transcribing(duration_s: float) -> None:
    _console.print()  # end the \r line
    _console.print(
        f"  [bold cyan][ TRANSCRIBING ][/bold cyan]"
        f"  [dim]{duration_s:.1f}s of audio[/dim]"
    )


def print_transcript(text: str) -> None:
    _console.print(f"  [bold white]TRANSCRIPT:[/bold white] {text}")


def llm_start() -> None:
    _console.print("  [bold green][ LLM ][/bold green] ", end="")


def llm_token(token: str) -> None:
    _console.print(token, end="", highlight=False)


def llm_end() -> None:
    _console.print()
    _console.print(Rule(style="dim"))
