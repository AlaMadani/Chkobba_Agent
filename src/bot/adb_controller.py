from __future__ import annotations

import logging
import random
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from src.bot.humanizer import Humanizer

try:
    import av  # pip install av
except ImportError:  # stream mode is optional, screencap fallback still works
    av = None

log = logging.getLogger("adb")

Point = Tuple[int, int]
# creationflags=0 is valid on every OS; it hides console windows on Windows
_POPEN_KW = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


# --------------------------------------------------------------------------- #
# Frame storage
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Frame:
    image: np.ndarray  # BGR, read-only
    seq: int  # increments with every new frame
    ts: float  # time.monotonic() when received


class FrameBuffer:
    """Thread-safe 'latest frame' slot. Frames are never mutated after put()."""

    def __init__(self):
        self._cond = threading.Condition()
        self._frame: Optional[Frame] = None
        self._seq = 0

    @property
    def seq(self) -> int:
        with self._cond:
            return self._seq

    def put(self, image: np.ndarray):
        image.flags.writeable = False
        with self._cond:
            self._seq += 1
            self._frame = Frame(image, self._seq, time.monotonic())
            self._cond.notify_all()

    def latest(self) -> Optional[Frame]:
        with self._cond:
            return self._frame

    def wait_newer(self, after_seq: int, timeout: Optional[float]) -> Optional[Frame]:
        with self._cond:
            self._cond.wait_for(
                lambda: self._frame is not None and self._frame.seq > after_seq,
                timeout,
            )
            f = self._frame
            return f if f is not None and f.seq > after_seq else None


# --------------------------------------------------------------------------- #
# Capture sources (each runs in its own daemon thread)
# --------------------------------------------------------------------------- #
class _CaptureSource:
    name = "base"

    def __init__(self, base_cmd, buffer, frame_size, screen_size, stop_evt, **opts):
        self.base_cmd = base_cmd
        self.buffer = buffer
        self.frame_size = frame_size  # what consumers receive (w, h)
        self.screen_size = screen_size  # native device size (w, h)
        self._stop = stop_evt
        self._local_stop = threading.Event()
        self.failed = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self.opts = opts
        self._thread = threading.Thread(
            target=self._guarded, daemon=True, name=self.name
        )

    def start(self):
        self._thread.start()

    def close(self):
        self._local_stop.set()
        self._kill()
        self._thread.join(timeout=2.0)

    def _kill(self):
        p = self._proc
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def _running(self):
        return not (self._stop.is_set() or self._local_stop.is_set())

    def _guarded(self):
        try:
            self._run()
        except Exception:
            log.exception("[%s] capture crashed", self.name)
        if self._running():
            self.failed.set()

    def _run(self):
        raise NotImplementedError


class ScreenrecordSource(_CaptureSource):
    """
    H.264 stream: adb exec-out screenrecord -> pipe -> PyAV parser/decoder.

    NOTE: we deliberately do NOT use av.open(pipe): containers try to seek(),
    which pipes can't do (OSError 22). A raw CodecContext only needs bytes.
    """

    name = "screenrecord"
    READ_SIZE = 64 * 1024

    @staticmethod
    def _drain_stderr(proc: subprocess.Popen):
        """Log the first stderr lines (diagnostics) and keep draining so adb never blocks."""
        shown = 0
        for raw in iter(proc.stderr.readline, b""):
            if shown < 5:
                msg = raw.decode(errors="ignore").strip()
                if msg:
                    log.warning("[screenrecord] device says: %s", msg)
                    shown += 1

    def _build_cmd(self, with_size: bool) -> List[str]:
        w, h = self.frame_size
        cmd = self.base_cmd + [
            "exec-out",
            "screenrecord",
            "--output-format=h264",
            f"--bit-rate={self.opts.get('bitrate', 8_000_000)}",
        ]
        if with_size:
            cmd.append(f"--size={w}x{h}")
        cmd.append("-")
        return cmd

    def _run(self):
        w, h = self.frame_size
        quick_failures = 0
        with_size = True
        warned_aspect = False

        while self._running():
            started = time.monotonic()
            got_frame = False
            self._proc = subprocess.Popen(
                self._build_cmd(with_size),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                **_POPEN_KW,
            )
            threading.Thread(
                target=self._drain_stderr, args=(self._proc,), daemon=True
            ).start()

            codec = av.CodecContext.create("h264", "r")
            try:
                codec.options = {"flags": "low_delay"}
            except Exception:
                pass  # older PyAV: harmless, just a bit more latency

            try:
                stdout = self._proc.stdout
                while self._running():
                    chunk = stdout.read(
                        self.READ_SIZE
                    )  # returns as soon as data is available
                    if not chunk:
                        break
                    for packet in codec.parse(chunk):
                        for frame in codec.decode(packet):
                            img = frame.to_ndarray(format="bgr24")
                            if (img.shape[1], img.shape[0]) != (w, h):
                                if not warned_aspect and (
                                    img.shape[1] > img.shape[0]
                                ) != (w > h):
                                    log.warning(
                                        "[screenrecord] stream orientation (%dx%d) differs from "
                                        "screen (%dx%d): frames will look stretched",
                                        img.shape[1],
                                        img.shape[0],
                                        w,
                                        h,
                                    )
                                    warned_aspect = True
                                img = cv2.resize(
                                    img, (w, h), interpolation=cv2.INTER_AREA
                                )
                            self.buffer.put(img)
                            got_frame = True
            except Exception as e:
                log.debug("[screenrecord] stream ended: %r", e)
            finally:
                self._kill()

            # Android caps screenrecord at 180 s -> a clean restart is normal.
            # Repeated very short runs without frames mean the device refuses the stream.
            if got_frame or time.monotonic() - started >= 3.0:
                quick_failures = 0
            else:
                quick_failures += 1
                if with_size:
                    log.warning(
                        "[screenrecord] failed with --size, retrying without it"
                    )
                    with_size = False
                if quick_failures >= 3:
                    log.error("[screenrecord] keeps failing, giving up")
                    return
            time.sleep(0.05)


class ScreencapSource(_CaptureSource):
    """Fallback: repeated `screencap -p` with adaptive pacing."""

    name = "screencap"

    def _run(self):
        cmd = self.base_cmd + ["exec-out", "screencap", "-p"]
        min_interval = 1.0 / self.opts.get("max_fps", 15.0)
        failures = 0
        while self._running():
            t0 = time.monotonic()
            try:
                out = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=3.0,
                    **_POPEN_KW,
                ).stdout  # run() kills the child itself on timeout
                img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    raise ValueError("undecodable screenshot")
                if (img.shape[1], img.shape[0]) != self.frame_size:
                    img = cv2.resize(img, self.frame_size, interpolation=cv2.INTER_AREA)
                self.buffer.put(img)
                failures = 0
            except (subprocess.TimeoutExpired, ValueError) as e:
                failures += 1
                log.warning("[screencap] %r (%d consecutive)", e, failures)
                if failures >= 10:
                    return
                self._local_stop.wait(min(0.2 * failures, 2.0))  # back off
            spare = min_interval - (time.monotonic() - t0)
            if spare > 0:
                self._local_stop.wait(spare)


# --------------------------------------------------------------------------- #
# Persistent shell: one adb process for all input commands
# --------------------------------------------------------------------------- #
class ShellSession:
    def __init__(self, base_cmd: List[str]):
        self._cmd = base_cmd + ["shell"]
        self._io_lock = threading.Lock()
        self._waiters: dict[str, threading.Event] = {}
        self._waiters_lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._spawn()

    def _spawn(self):
        self._kill()
        proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            **_POPEN_KW,
        )
        self._proc = proc
        threading.Thread(target=self._reader, args=(proc,), daemon=True).start()

    def _kill(self):
        p = self._proc
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def _reader(self, proc):
        for raw in iter(proc.stdout.readline, b""):
            token = raw.decode(errors="ignore").strip()
            with self._waiters_lock:
                ev = self._waiters.pop(token, None)
            if ev:
                ev.set()
        with self._waiters_lock:  # shell died: release anyone still waiting
            for ev in self._waiters.values():
                ev.set()
            self._waiters.clear()

    def run(self, script: str, wait: bool = False, timeout: float = 10.0) -> bool:
        """Send a multi-line shell script. If wait=True, block until it finished."""
        token = f"__done_{uuid.uuid4().hex[:10]}__"
        ev = threading.Event() if wait else None
        payload = (script.strip() + "\n" + (f"echo {token}\n" if wait else "")).encode()

        for attempt in (1, 2):
            with self._io_lock:
                if self._proc.poll() is not None:
                    self._spawn()
                if ev:
                    with self._waiters_lock:
                        self._waiters[token] = ev
                try:
                    self._proc.stdin.write(payload)
                    self._proc.stdin.flush()
                    break
                except (BrokenPipeError, OSError):
                    log.warning(
                        "adb shell pipe broke (attempt %d), respawning", attempt
                    )
                    self._spawn()
                    if attempt == 2:
                        return False
        if ev:
            return ev.wait(timeout) and self._proc.poll() is None
        return True

    def close(self):
        try:
            if self._proc and self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass
        self._kill()


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TapStep:
    x: int  # frame coordinates
    y: int
    radius: int = 15
    delay_after: Optional[Tuple[float, float]] = None  # (mean, std) seconds


class ADBController:
    # rough cost of one `input tap` on the phone (Java process start), used to
    # estimate when a fire-and-forget sequence will be finished
    _TAP_COST = 0.15

    def __init__(
        self,
        device_id: Optional[str] = None,
        *,
        capture_mode: str = "auto",  # "auto" | "stream" | "screencap"
        scale: float = 1.0,  # <1.0 = smaller frames = faster ML + decode
        max_fps: float = 15.0,  # only used by the screencap fallback
        bitrate: int = 8_000_000,
        startup_timeout: float = 10.0,
        settle_time: float = 0.8,  # min. delay between last tap and next usable frame
        stale_timeout: float = 4.0,  # give up waiting for a post-action frame after this
        latency_margin: float = 0.25,  # capture+encode latency: a frame this close to the
        # tap may still show the OLD screen
    ):
        self.device_id = device_id
        self.base_cmd = ["adb"] + (["-s", device_id] if device_id else [])
        self.capture_mode = capture_mode
        self.max_fps = max_fps
        self.bitrate = bitrate
        self.startup_timeout = startup_timeout
        self.settle_time = settle_time
        self.stale_timeout = stale_timeout
        self.latency_margin = latency_margin

        self._stop_evt = threading.Event()
        self._failover_lock = threading.Lock()
        self._buffer = FrameBuffer()
        self._source: Optional[_CaptureSource] = None
        self._shell: Optional[ShellSession] = None

        # Action tracking (prevents acting on a frame captured BEFORE our last tap)
        self._action_lock = threading.Lock()
        self._action_done_at: Optional[float] = (
            None  # monotonic time the last action ends
        )

        self._check_device()
        self.screen_size = self._probe_screen_size()  # (w, h), current orientation
        self.frame_size = self._compute_frame_size(scale)  # what get_frame() returns
        log.info("screen=%s frame=%s", self.screen_size, self.frame_size)

        self._shell = ShellSession(self.base_cmd)
        self._start_capture(capture_mode)

    # ---- setup helpers ---------------------------------------------------- #
    def _check_device(self):
        try:
            r = subprocess.run(
                self.base_cmd + ["get-state"],
                capture_output=True,
                text=True,
                timeout=8.0,
                **_POPEN_KW,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"adb not usable: {e!r}") from e
        if r.stdout.strip() != "device":
            raise RuntimeError(
                f"Device not ready ({(r.stderr or r.stdout).strip()}). "
                "Check `adb devices` / `adb connect <ip>:<port>`."
            )

    def _probe_screen_size(self) -> Tuple[int, int]:
        out = subprocess.run(
            self.base_cmd + ["exec-out", "screencap", "-p"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10.0,
            **_POPEN_KW,
        ).stdout
        img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("Could not grab a probe screenshot")
        return img.shape[1], img.shape[0]

    def _compute_frame_size(self, scale: float) -> Tuple[int, int]:
        w, h = self.screen_size
        if scale == 1.0:
            return w, h
        # encoders like multiples of 16
        return max(16, int(w * scale) // 16 * 16), max(16, int(h * scale) // 16 * 16)

    def _make_source(self, cls):
        return cls(
            self.base_cmd,
            self._buffer,
            self.frame_size,
            self.screen_size,
            self._stop_evt,
            bitrate=self.bitrate,
            max_fps=self.max_fps,
        )

    def _start_capture(self, mode: str):
        classes = []
        if mode in ("auto", "stream"):
            if av is not None:
                classes.append(ScreenrecordSource)
            elif mode == "stream":
                raise RuntimeError("PyAV missing: pip install av")
            else:
                log.warning("PyAV not installed -> screencap fallback (slower)")
        if mode in ("auto", "screencap"):
            classes.append(ScreencapSource)

        for cls in classes:
            start_seq = (
                self._buffer.seq
            )  # ignore frames left over from a previous backend
            src = self._make_source(cls)
            src.start()
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline and not src.failed.is_set():
                if self._buffer.wait_newer(start_seq, 0.2) is not None:
                    self._source = src
                    log.info("capture backend: %s", src.name)
                    return
            log.warning("backend %s produced no frame, trying next", src.name)
            src.close()
        raise RuntimeError("No capture backend could deliver a frame")

    def _failover(self):
        with self._failover_lock:
            if (
                self._source
                and self._source.failed.is_set()
                and self.capture_mode == "auto"
            ):
                log.error("%s died -> switching to screencap", self._source.name)
                self._source.close()
                self._start_capture("screencap")

    # ---- vision API ------------------------------------------------------- #
    def _wait_settled(self):
        """
        Block until the screen can be trusted after our last action:
          1) settle_time elapsed since the action finished, AND
          2) at least one frame captured after the action (+ latency margin) exists.
        Falls back to the latest frame after stale_timeout (the tap may have changed nothing).
        """
        done_at = self._action_done_at
        if done_at is None:
            return
        ready_at = done_at + self.settle_time
        give_up_at = done_at + self.stale_timeout

        while True:
            now = time.monotonic()
            f = self._buffer.latest()
            fresh = f is not None and f.ts >= done_at + self.latency_margin
            if now >= ready_at and fresh:
                return
            if now >= give_up_at:
                if not fresh:
                    log.warning(
                        "no new frame after action within %.1fs, using latest",
                        self.stale_timeout,
                    )
                return
            if fresh:  # only the settle delay remains
                time.sleep(max(0.0, min(0.02, ready_at - now)))
            else:  # wait for the next frame to arrive
                self._buffer.wait_newer(f.seq if f else 0, min(0.05, give_up_at - now))

    def get_frame(
        self, after_seq: int = 0, timeout: Optional[float] = None, settled: bool = True
    ) -> Optional[Frame]:
        """
        Latest frame, or (with after_seq) block until a newer one arrives.
        settled=True guarantees the frame reflects the game AFTER our last action.
        """
        if self._source and self._source.failed.is_set():
            self._failover()
        if settled:
            self._wait_settled()
        if after_seq or timeout:
            return self._buffer.wait_newer(after_seq, timeout)
        return self._buffer.latest()

    def get_screen_frame(
        self, copy: bool = True, settled: bool = True
    ) -> Optional[np.ndarray]:
        """Drop-in replacement. copy=False is free but the array is read-only."""
        f = self.get_frame(settled=settled)
        if f is None:
            return None
        return f.image.copy() if copy else f.image

    # ---- input API -------------------------------------------------------- #
    def _to_device(self, x: float, y: float) -> Point:
        sw, sh = self.screen_size
        fw, fh = self.frame_size
        dx = min(max(int(round(x * sw / fw)), 0), sw - 1)
        dy = min(max(int(round(y * sh / fh)), 0), sh - 1)
        return dx, dy

    @staticmethod
    def _delay(mean: float, std: float) -> float:
        return max(0.03, random.gauss(mean, std))

    def _execute(self, script: str, est_duration: float, wait: bool) -> bool:
        """Serialize actions and record when the screen is expected to have reacted."""
        with self._action_lock:
            start = max(time.monotonic(), self._action_done_at or 0.0)
            # Mark BEFORE sending so a concurrent get_screen_frame() already gates on it
            self._action_done_at = start + est_duration
            ok = self._shell.run(script, wait=wait, timeout=est_duration + 5.0)
            if wait:
                self._action_done_at = (
                    time.monotonic()
                )  # real end, confirmed by the device
            return ok

    def tap(self, x: int, y: int) -> bool:
        dx, dy = self._to_device(x, y)
        return self._execute(f"input tap {dx} {dy}", self._TAP_COST, wait=True)

    def tap_humanized(self, x: int, y: int, radius: int = 15) -> bool:
        hx, hy = Humanizer.randomize_coordinates(x, y, radius)
        return self.tap(hx, hy)

    def run_taps(self, steps: List[TapStep], wait: bool = True) -> bool:
        """Whole sequence = ONE script. Delays run on the phone (no host jitter/latency)."""
        lines, est = [], 0.0
        for s in steps:
            hx, hy = Humanizer.randomize_coordinates(s.x, s.y, s.radius)
            dx, dy = self._to_device(hx, hy)
            lines.append(f"input tap {dx} {dy}")
            est += self._TAP_COST
            if s.delay_after:
                d = self._delay(*s.delay_after)
                lines.append(f"sleep {d:.3f}")
                est += d
        return self._execute("\n".join(lines), est, wait)

    def action_7ot(
        self,
        card_x: int,
        card_y: int,
        btn_7ot_x: int,
        btn_7ot_y: int,
        wait: bool = True,
    ):
        """Throw a card."""
        log.info("Action: 7ot")
        return self.run_taps(
            [
                TapStep(card_x, card_y, 15, (0.3, 0.1)),
                TapStep(btn_7ot_x, btn_7ot_y, 30),
            ],
            wait=wait,
        )

    def action_koul(
        self,
        card_main_x: int,
        card_main_y: int,
        table_coords: List[Point],
        btn_koul_x: int,
        btn_koul_y: int,
        wait: bool = True,
    ):
        """Capture cards from the table."""
        log.info("Action: Koul (%d cards)", len(table_coords))
        steps = [TapStep(card_main_x, card_main_y, 15, (0.3, 0.1))]
        for i, (tx, ty) in enumerate(table_coords):
            last = i == len(table_coords) - 1
            steps.append(TapStep(tx, ty, 15, (0.3, 0.1) if last else (0.2, 0.05)))
        steps.append(TapStep(btn_koul_x, btn_koul_y, 30))
        return self.run_taps(steps, wait=wait)

    # ---- lifecycle -------------------------------------------------------- #
    def stop(self):
        self._stop_evt.set()
        if self._source:
            self._source.close()
        if self._shell:
            self._shell.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()


# --- Visual test ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    with ADBController(scale=1.0) as device:
        print("Press 'q' in the OpenCV window to quit.")
        last_seq, fps, t_prev = 0, 0.0, time.monotonic()
        while True:
            f = device.get_frame(after_seq=last_seq, timeout=0.05)
            if f is not None:
                n_new, last_seq = f.seq - last_seq, f.seq
                now = time.monotonic()
                fps = 0.9 * fps + 0.1 * (n_new / max(now - t_prev, 1e-6))
                t_prev = now
                cv2.imshow(
                    "Chkobba Bot - Vision", cv2.resize(f.image, None, fx=0.3, fy=0.3)
                )
                print(f"capture ~{fps:5.1f} FPS | frame #{f.seq}", end="\r")
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    cv2.destroyAllWindows()
