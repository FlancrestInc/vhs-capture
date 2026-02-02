import dataclasses
import datetime as dt
import logging
import os
import re
import shlex
import signal
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

FILENAME_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclasses.dataclass
class CaptureOptions:
    video_device: str
    audio_device: str
    input_type: str
    duration_seconds: int
    preset: str
    output_format: str
    filename_prefix: str
    tape_label: str | None
    dry_run: bool
    test_preview: bool


@dataclasses.dataclass
class CaptureStatus:
    running: bool
    output_file: str | None
    started_at: dt.datetime | None
    duration_seconds: int | None
    stderr_tail: list[str]


class CaptureManager:
    def __init__(self, output_dir: str, log_file: str) -> None:
        self.output_dir = output_dir
        self.log_file = log_file
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._stderr_tail: deque[str] = deque(maxlen=50)
        self._started_at: dt.datetime | None = None
        self._duration_seconds: int | None = None
        self._output_file: str | None = None
        self._stderr_thread: threading.Thread | None = None

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start_capture(self, options: CaptureOptions) -> tuple[bool, str, str | None]:
        with self._lock:
            if self.is_running():
                return False, "Capture already running.", None
            duration = options.duration_seconds
            if options.test_preview:
                duration = 10
            output_file = build_output_path(
                self.output_dir,
                options.filename_prefix,
                options.tape_label,
                options.output_format,
            )
            cmd = build_ffmpeg_command(options, duration, output_file)
            if options.dry_run:
                return True, f"Dry run command: {' '.join(shlex.quote(part) for part in cmd)}", output_file
            ensure_directory(self.output_dir)
            set_input_type(options.video_device, options.input_type)
            log_handle = open(self.log_file, "a", encoding="utf-8")
            log_handle.write(f"\n== Capture start {dt.datetime.now().isoformat()} ==\n")
            log_handle.flush()
            process = subprocess.Popen(
                cmd,
                stdout=log_handle,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._process = process
            self._stderr_tail.clear()
            self._started_at = dt.datetime.now()
            self._duration_seconds = duration
            self._output_file = output_file
            self._stderr_thread = threading.Thread(
                target=self._capture_stderr,
                args=(process, log_handle),
                daemon=True,
            )
            self._stderr_thread.start()
            return True, "Capture started.", output_file

    def _capture_stderr(self, process: subprocess.Popen[str], log_handle) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            stripped = line.rstrip("\n")
            self._stderr_tail.append(stripped)
            log_handle.write(line)
            log_handle.flush()
        log_handle.write(f"\n== Capture end {dt.datetime.now().isoformat()} ==\n")
        log_handle.flush()
        log_handle.close()

    def stop_capture(self) -> tuple[bool, str]:
        with self._lock:
            if not self.is_running():
                return False, "No capture running."
            assert self._process is not None
            self._process.send_signal(signal.SIGINT)
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            return True, "Stop signal sent."

    def status(self) -> CaptureStatus:
        running = self.is_running()
        return CaptureStatus(
            running=running,
            output_file=self._output_file,
            started_at=self._started_at,
            duration_seconds=self._duration_seconds,
            stderr_tail=list(self._stderr_tail),
        )


def parse_duration(value: str) -> int:
    if not value:
        raise ValueError("Duration is required.")
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError("Duration must be in HH:MM:SS format.")
    hours, minutes, seconds = [int(part) for part in parts]
    return hours * 3600 + minutes * 60 + seconds


def list_video_devices() -> list[tuple[str, str]]:
    devices = []
    paths = sorted(Path("/dev").glob("video*"))
    for path in paths:
        devices.append((str(path), str(path)))
    try:
        output = subprocess.check_output(["v4l2-ctl", "--list-devices"], text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return devices
    current_name = None
    for line in output.splitlines():
        if not line.startswith("\t") and line.strip():
            current_name = line.strip().rstrip(":")
        elif line.startswith("\t") and current_name:
            dev_path = line.strip()
            devices.append((dev_path, f"{current_name} ({dev_path})"))
    unique = {}
    for path, label in devices:
        unique[path] = label
    return [(path, label) for path, label in unique.items()]


def list_audio_devices() -> list[tuple[str, str]]:
    devices: list[tuple[str, str]] = []
    try:
        output = subprocess.check_output(["arecord", "-l"], text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return devices
    for line in output.splitlines():
        if "card" in line and ", device" in line:
            match = re.search(r"card (\d+): ([^\[]+) \[([^\]]+)\], device (\d+): ([^\[]+) \[([^\]]+)\]", line)
            if match:
                card = match.group(1)
                device = match.group(4)
                label = f"card {card}: {match.group(2).strip()} ({match.group(3).strip()}), device {device}: {match.group(5).strip()}"
                devices.append((f"hw:{card},{device}", label))
    return devices


def build_output_path(output_dir: str, prefix: str, tape_label: str | None, output_format: str) -> str:
    safe_prefix = sanitize_filename(prefix or "capture")
    label = sanitize_filename(tape_label) if tape_label else ""
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    parts = [safe_prefix]
    if label:
        parts.append(label)
    parts.append(timestamp)
    filename = "_".join(filter(None, parts)) + f".{output_format}"
    return str(Path(output_dir) / filename)


def sanitize_filename(value: str) -> str:
    cleaned = FILENAME_SAFE.sub("_", value.strip())
    return cleaned.strip("_") or "capture"


def ensure_directory(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def set_input_type(video_device: str, input_type: str) -> None:
    if input_type not in {"composite", "s-video"}:
        return
    index = "0" if input_type == "composite" else "1"
    try:
        subprocess.run(["v4l2-ctl", "--device", video_device, "--set-input", index], check=False)
    except FileNotFoundError:
        logger.warning("v4l2-ctl not available to set input type")


def build_ffmpeg_command(options: CaptureOptions, duration: int, output_file: str) -> list[str]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-f",
        "v4l2",
        "-thread_queue_size",
        "4096",
        "-i",
        options.video_device,
        "-f",
        "alsa",
        "-thread_queue_size",
        "4096",
        "-i",
        options.audio_device,
        "-t",
        str(duration),
    ]
    cmd.extend(encode_flags(options.preset))
    cmd.append(output_file)
    return cmd


def encode_flags(preset: str) -> list[str]:
    if preset == "archival_lossless":
        return [
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-g",
            "1",
            "-slicecrc",
            "1",
            "-c:a",
            "flac",
        ]
    if preset == "passthrough_if_possible":
        return ["-c:v", "copy", "-c:a", "copy"]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
    ]


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    return str(dt.timedelta(seconds=seconds))


def iter_recent_recordings(output_dir: str) -> Iterable[dict]:
    path = Path(output_dir)
    if not path.exists():
        return []
    items = []
    for file in path.iterdir():
        if file.name == "vhs-ui.log" or not file.is_file():
            continue
        stat = file.stat()
        items.append(
            {
                "name": file.name,
                "size": stat.st_size,
                "mtime": dt.datetime.fromtimestamp(stat.st_mtime),
            }
        )
    return sorted(items, key=lambda entry: entry["mtime"], reverse=True)
