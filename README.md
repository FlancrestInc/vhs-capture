# VHS Capture UI

This repository adds a containerized VHS capture workflow and a lightweight web UI to manage Elgato Video Capture devices on Linux.

## Quick start (Docker Compose)

1. Plug in the Elgato Video Capture device.
2. Ensure you can see the video device and ALSA device on the host (see device discovery below).
3. Start the UI:

```bash
docker compose up --build
```

The UI is available at `http://localhost:8099` by default.

### Optional basic auth

Set the following environment variables to enable HTTP Basic authentication:

```bash
export VHS_UI_USER=admin
export VHS_UI_PASS=change-me
```

If these are not set, the UI runs without auth and logs a warning.

## Device discovery

### Video devices

List V4L2 devices on the host:

```bash
v4l2-ctl --list-devices
ls -l /dev/video*
```

Pick the correct `/dev/videoX` and pass it to Docker via:

```bash
VHS_VIDEO_DEVICE=/dev/video2 docker compose up --build
```

### Audio devices

List ALSA capture devices:

```bash
arecord -l
```

In the UI, select the matching `hw:X,Y` entry (for example `hw:1,0`).

## Web UI usage

### Capture page

* Choose the video device (`/dev/videoX`).
* Choose the audio device (`hw:X,Y`).
* Select composite or S-video (best effort via `v4l2-ctl --set-input`).
* Enter a duration in `HH:MM:SS` format.
* Choose a preset:
  * `archival_lossless` → FFV1 + FLAC in MKV.
  * `high_quality_h264` → H.264 + AAC.
  * `passthrough_if_possible` → attempts stream copy (best effort).
* Optional filename prefix and tape label.
* Start capture.

### Status page

* Shows whether a capture is running.
* Displays elapsed and remaining time.
* Shows the last 50 lines of FFmpeg stderr.
* Allows stopping the capture (SIGINT then SIGKILL fallback).

### Recordings page

Lists files from `/output` with size, timestamps, and download links.

## API endpoints

* `POST /api/start` — start capture.
* `POST /api/stop` — stop capture.
* `GET /api/status` — capture status.
* `GET /api/recordings` — list recordings.

## Troubleshooting

* **Permissions / device not found**: ensure `/dev/videoX` and `/dev/snd` are mapped into the container and the service has `video` + `audio` group access. You may need to enable privileged mode with `VHS_PRIVILEGED=true`.
* **No audio**: verify the `hw:X,Y` value with `arecord -l`, and confirm the audio capture device is connected.
* **Wrong input type**: switch between composite and S-video in the UI. Some devices ignore input selection; verify with `v4l2-ctl --all`.
* **Dropped frames**: try reducing other system load, use a faster disk, or switch to the H.264 preset.

## Migration note

Previous usage was CLI-only via `vhs-capture.sh` with flags. The new workflow keeps similar capture behavior but exposes the settings in a web UI so you no longer need to remember CLI flags. The shell script remains available for legacy workflows.
