import queue
import socket
import subprocess
import threading
from typing import IO, Callable

import numpy as np

import display
from config import CFG


class _Capture:
    """Unified capture handle — .terminate() ends the session cleanly."""
    def __init__(self, terminate_fn: Callable[[], None]):
        self._terminate_fn = terminate_fn

    def terminate(self) -> None:
        try:
            self._terminate_fn()
        except Exception:
            pass


def _accept_tcp(port: int) -> socket.socket:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(1)
    display.loading(f"Waiting for TCP capture on port {port}...")
    try:
        conn, addr = server.accept()
    finally:
        server.close()
    display.loading(f"Capture connected from {addr[0]}")
    return conn


def _accept_icecast_source(port: int) -> socket.socket:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(1)
    display.loading(f"Waiting for Icecast source on port {port}...")
    try:
        conn, addr = server.accept()
    finally:
        server.close()
    display.loading(f"Icecast source connected from {addr[0]}")

    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            raise ConnectionError("Connection closed before headers completed")
        buf += chunk
    conn.sendall(b"HTTP/1.0 200 OK\r\n\r\n")
    return conn


def _socket_to_queue(conn: socket.socket, chunk_queue: queue.Queue) -> None:
    bytes_per_chunk = CFG.vad_chunk_samples * 4
    buf = b""
    try:
        while True:
            needed = bytes_per_chunk - len(buf)
            data = conn.recv(needed)
            if not data:
                break
            buf += data
            if len(buf) == bytes_per_chunk:
                chunk_queue.put(np.frombuffer(buf, dtype=np.float32).copy())
                buf = b""
    except OSError:
        pass
    finally:
        chunk_queue.put(None)
        try:
            conn.close()
        except OSError:
            pass


def _socket_to_ffmpeg(conn: socket.socket, ffmpeg_stdin: IO[bytes]) -> None:
    try:
        while True:
            data = conn.recv(65536)
            if not data:
                break
            ffmpeg_stdin.write(data)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            ffmpeg_stdin.close()
        except OSError:
            pass
        conn.close()


def _ffmpeg_to_queue(stdout: IO[bytes], chunk_queue: queue.Queue) -> None:
    bytes_per_chunk = CFG.vad_chunk_samples * 4
    buf = b""
    while True:
        needed = bytes_per_chunk - len(buf)
        data = stdout.read(needed)
        if not data:
            chunk_queue.put(None)
            return
        buf += data
        if len(buf) == bytes_per_chunk:
            chunk_queue.put(np.frombuffer(buf, dtype=np.float32).copy())
            buf = b""


def _start_tcp() -> tuple[_Capture, queue.Queue]:
    conn = _accept_tcp(CFG.icecast_port)
    chunk_queue: queue.Queue = queue.Queue()
    threading.Thread(target=_socket_to_queue, args=(conn, chunk_queue), daemon=True).start()

    def _terminate() -> None:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass

    return _Capture(_terminate), chunk_queue


def _start_icecast() -> tuple[_Capture, queue.Queue]:
    conn = _accept_icecast_source(CFG.icecast_port)

    cmd = [
        "ffmpeg",
        "-loglevel", "warning",
        "-f", "mp3",
        "-i", "pipe:0",
        "-vn",
        "-ar", str(CFG.sample_rate),
        "-ac", "1",
        "-f", "f32le",
        "pipe:1",
    ]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
    if proc.stdin is None or proc.stdout is None:
        proc.kill()
        raise RuntimeError("ffmpeg process did not open stdin/stdout pipes")

    chunk_queue: queue.Queue = queue.Queue()
    threading.Thread(target=_socket_to_ffmpeg, args=(conn, proc.stdin), daemon=True).start()
    threading.Thread(target=_ffmpeg_to_queue, args=(proc.stdout, chunk_queue), daemon=True).start()

    return _Capture(proc.terminate), chunk_queue


def start_audio_reader() -> tuple[_Capture, queue.Queue]:
    mode = CFG.capture_mode
    if mode == "tcp":
        return _start_tcp()
    if mode == "icecast":
        return _start_icecast()
    raise ValueError(f"unknown capture_mode: {mode!r} (expected 'tcp' or 'icecast')")
