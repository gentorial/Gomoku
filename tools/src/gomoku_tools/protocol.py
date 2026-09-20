import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid


ROOT = Path(__file__).resolve().parents[3]


def binary(name="gomoku-worker"):
    if name == "gomoku-worker" and os.environ.get("GOMOKU_ENGINE_PATH"):
        return Path(os.environ["GOMOKU_ENGINE_PATH"])
    if name == "pbrain-gomoku" and os.name == "nt":
        name += "64"
    return ROOT / "build" / "dev" / "bin" / (name + (".exe" if os.name == "nt" else ""))


class EngineProcess:
    def __init__(self, executable):
        self.process = subprocess.Popen(
            [str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.messages = queue.Queue()
        self.diagnostic = ""
        self.closed = False
        self._threads = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _read_stdout(self):
        for line in self.process.stdout:
            self.messages.put(line.rstrip("\r\n"))
        self.messages.put(None)

    def _read_stderr(self):
        for line in self.process.stderr:
            self.diagnostic = (self.diagnostic + line)[-4096:]

    def send(self, line):
        if self.closed:
            raise RuntimeError("Engine has been closed")
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def receive(self, timeout=5):
        try:
            line = self.messages.get(timeout=timeout)
        except queue.Empty as error:
            self.close()
            raise TimeoutError("Engine did not respond before the deadline") from error
        if line is None:
            raise RuntimeError("Engine exited: " + self.diagnostic)
        return line

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        for thread in self._threads:
            thread.join(timeout=1)
        self.process.stdout.close()
        self.process.stderr.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Worker(EngineProcess):
    def __init__(self, executable=None):
        super().__init__(executable or binary())
        self._lock = threading.Lock()

    def request(self, method, **params):
        with self._lock:
            request_id = str(uuid.uuid4())
            self.send(json.dumps({"v": 1, "id": request_id, "method": method, **params}))
            timeout = params.get("limits", {}).get("timeMs", 0) / 1000 + 5
            response = json.loads(self.receive(timeout))
            if response.get("v") != 1 or response.get("id") != request_id:
                raise RuntimeError("Engine response does not match request")
            if not response.get("ok"):
                raise ValueError(response["error"]["message"])
            return response["result"]


class Pbrain(EngineProcess):
    def __init__(self, executable, size=15, rule="freestyle", time_ms=100):
        super().__init__(executable)
        self.time_ms = time_ms
        try:
            self.send("INFO rule " + str({"freestyle": 0, "standard": 1}[rule]))
            self.send("INFO timeout_turn " + str(time_ms))
            self.send("START " + str(size))
            if not self.receive().startswith("OK"):
                raise RuntimeError("Gomocup initialization failed")
        except Exception:
            self.close()
            raise

    def move(self, moves):
        self.send("INFO time_left 2147483647")
        self.send("BOARD")
        own_parity = len(moves) % 2
        for index, move in enumerate(moves):
            field = 1 if index % 2 == own_parity else 2
            self.send(str(move["x"]) + "," + str(move["y"]) + "," + str(field))
        self.send("DONE")
        deadline = time.monotonic() + self.time_ms / 1000 + 5
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close()
                raise TimeoutError("Gomocup response deadline exceeded")
            line = self.receive(remaining)
            if line.startswith(("MESSAGE ", "DEBUG ")) or not line:
                continue
            parts = line.split(",")
            if len(parts) != 2:
                raise ValueError("Invalid Gomocup move: " + line)
            return {"x": int(parts[0]), "y": int(parts[1])}
