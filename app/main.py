import datetime as dt
import logging
import os
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app.config import load_config
from scripts.capture import (
    CaptureManager,
    CaptureOptions,
    format_duration,
    iter_recent_recordings,
    list_audio_devices,
    list_video_devices,
    parse_duration,
)

config = load_config()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("vhs-ui")

Path(config.output_dir).mkdir(parents=True, exist_ok=True)
file_handler = logging.FileHandler(config.log_file)
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.getLogger().addHandler(file_handler)

if not config.auth_enabled:
    logger.warning("VHS UI auth disabled. Set VHS_UI_USER and VHS_UI_PASS to enable basic auth.")

app = FastAPI()
security = HTTPBasic(auto_error=False)

templates = Jinja2Templates(directory="app/templates")
manager = CaptureManager(config.output_dir, config.log_file)


def auth_dependency(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not config.auth_enabled:
        return None
    if credentials is None:
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})
    correct_user = secrets.compare_digest(credentials.username, config.auth_user or "")
    correct_pass = secrets.compare_digest(credentials.password, config.auth_pass or "")
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})
    return None


@app.get("/", response_class=RedirectResponse)
async def index() -> RedirectResponse:
    return RedirectResponse(url="/capture")


@app.get("/capture", response_class=HTMLResponse, dependencies=[Depends(auth_dependency)])
async def capture_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "capture.html",
        {
            "request": request,
            "video_devices": list_video_devices(),
            "audio_devices": list_audio_devices(),
            "presets": [
                ("archival_lossless", "Archival (FFV1 + FLAC)"),
                ("high_quality_h264", "High quality H.264 + AAC"),
                ("passthrough_if_possible", "Passthrough if possible"),
            ],
            "output_formats": ["mkv", "mp4"],
        },
    )


@app.post("/capture", response_class=HTMLResponse, dependencies=[Depends(auth_dependency)])
async def start_capture_form(
    request: Request,
    video_device: str = Form(...),
    audio_device: str = Form(...),
    input_type: str = Form("composite"),
    duration: str = Form("00:30:00"),
    preset: str = Form("archival_lossless"),
    output_format: str = Form("mkv"),
    filename_prefix: str = Form("capture"),
    tape_label: str = Form(""),
    dry_run: str | None = Form(None),
    test_preview: str | None = Form(None),
) -> HTMLResponse:
    options = build_options(
        video_device,
        audio_device,
        input_type,
        duration,
        preset,
        output_format,
        filename_prefix,
        tape_label,
        dry_run,
        test_preview,
    )
    success, message, output_file = manager.start_capture(options)
    status = manager.status()
    return templates.TemplateResponse(
        "status.html",
        {
            "request": request,
            "message": message,
            "output_file": output_file,
            "status": status,
            "elapsed": elapsed_time(status),
            "remaining": remaining_time(status),
        },
    )


@app.get("/status", response_class=HTMLResponse, dependencies=[Depends(auth_dependency)])
async def status_page(request: Request) -> HTMLResponse:
    status = manager.status()
    return templates.TemplateResponse(
        "status.html",
        {
            "request": request,
            "status": status,
            "elapsed": elapsed_time(status),
            "remaining": remaining_time(status),
        },
    )


@app.post("/status/stop", response_class=HTMLResponse, dependencies=[Depends(auth_dependency)])
async def stop_capture_form(request: Request) -> HTMLResponse:
    success, message = manager.stop_capture()
    status = manager.status()
    return templates.TemplateResponse(
        "status.html",
        {
            "request": request,
            "message": message,
            "status": status,
            "elapsed": elapsed_time(status),
            "remaining": remaining_time(status),
        },
    )


@app.get("/recordings", response_class=HTMLResponse, dependencies=[Depends(auth_dependency)])
async def recordings_page(request: Request) -> HTMLResponse:
    recordings = iter_recent_recordings(config.output_dir)
    return templates.TemplateResponse(
        "recordings.html",
        {"request": request, "recordings": recordings, "output_dir": config.output_dir},
    )


@app.get("/recordings/{filename}", dependencies=[Depends(auth_dependency)])
async def recordings_download(filename: str):
    safe_name = os.path.basename(filename)
    file_path = Path(config.output_dir) / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)


@app.get("/api/status", response_class=JSONResponse, dependencies=[Depends(auth_dependency)])
async def api_status() -> JSONResponse:
    status = manager.status()
    data = {
        "running": status.running,
        "output_file": status.output_file,
        "started_at": status.started_at.isoformat() if status.started_at else None,
        "duration_seconds": status.duration_seconds,
        "elapsed": elapsed_time(status),
        "remaining": remaining_time(status),
        "stderr_tail": status.stderr_tail,
    }
    return JSONResponse(content=data)


@app.post("/api/start", response_class=JSONResponse, dependencies=[Depends(auth_dependency)])
async def api_start(request: Request) -> JSONResponse:
    payload = await request.json()
    options = build_options(
        payload.get("video_device", ""),
        payload.get("audio_device", ""),
        payload.get("input_type", "composite"),
        payload.get("duration", "00:30:00"),
        payload.get("preset", "archival_lossless"),
        payload.get("output_format", "mkv"),
        payload.get("filename_prefix", "capture"),
        payload.get("tape_label", ""),
        payload.get("dry_run"),
        payload.get("test_preview"),
    )
    success, message, output_file = manager.start_capture(options)
    return JSONResponse(content={"success": success, "message": message, "output_file": output_file})


@app.post("/api/stop", response_class=JSONResponse, dependencies=[Depends(auth_dependency)])
async def api_stop() -> JSONResponse:
    success, message = manager.stop_capture()
    return JSONResponse(content={"success": success, "message": message})


@app.get("/api/recordings", response_class=JSONResponse, dependencies=[Depends(auth_dependency)])
async def api_recordings() -> JSONResponse:
    recordings = [
        {
            "name": rec["name"],
            "size": rec["size"],
            "mtime": rec["mtime"].isoformat(),
        }
        for rec in iter_recent_recordings(config.output_dir)
    ]
    return JSONResponse(content={"recordings": recordings})


def build_options(
    video_device: str,
    audio_device: str,
    input_type: str,
    duration: str,
    preset: str,
    output_format: str,
    filename_prefix: str,
    tape_label: str,
    dry_run: str | bool | None,
    test_preview: str | bool | None,
) -> CaptureOptions:
    if not video_device or not audio_device:
        raise HTTPException(status_code=400, detail="Video and audio devices are required.")
    try:
        duration_seconds = parse_duration(duration)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CaptureOptions(
        video_device=video_device,
        audio_device=audio_device,
        input_type=input_type,
        duration_seconds=duration_seconds,
        preset=preset,
        output_format=output_format,
        filename_prefix=filename_prefix,
        tape_label=tape_label or None,
        dry_run=bool(dry_run),
        test_preview=bool(test_preview),
    )


def elapsed_time(status) -> str:
    if not status.started_at:
        return ""
    elapsed = dt.datetime.now() - status.started_at
    if elapsed is None:
        return ""
    return str(elapsed).split(".")[0]


def remaining_time(status) -> str:
    if not status.started_at or status.duration_seconds is None:
        return ""
    elapsed = (dt.datetime.now() - status.started_at).total_seconds()
    remaining = max(status.duration_seconds - int(elapsed), 0)
    return format_duration(remaining)
