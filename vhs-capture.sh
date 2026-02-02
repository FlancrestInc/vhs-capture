#!/usr/bin/env bash
set -euo pipefail

# ===== Defaults (edit to taste) =====
VID_DEFAULT="/dev/video0"
PULSE_SRC_DEFAULT="alsa_input.usb-Elgato_Video_Capture_2VC309901000031200136-01.stereo-fallback"
STANDARD_DEFAULT="ntsc"            # ntsc | pal
MODE_DEFAULT="double"              # double=59.94p/50p, single=29.97p/25p
INPUT_FORMAT_DEFAULT="yuyv422"     # yuyv422 | mjpeg
OUTDIR_DEFAULT="/mnt/Data/Pettit Video"

# Notifier defaults (set URL to enable; token optional)
NTFY_URL_DEFAULT="https://ntfy.flan.party"
NTFY_TOPIC_DEFAULT="vhs-captures"
NTFY_TOKEN_DEFAULT="tk_rn8rgqfy2uvvq4xp5lm7d035fvgsi"

usage() {
  cat <<EOF
Usage: ${0##*/} <DURATION> [options]
<DURATION> uses GNU timeout syntax: 90m | 2h5m | 5400 | 45s

Options:
  -v, --video DEV         V4L2 device (default: $VID_DEFAULT)
  -a, --audio NAME        Pulse/PipeWire source (default: $PULSE_SRC_DEFAULT)
  -s, --standard STD      ntsc | pal (default: $STANDARD_DEFAULT)
  -m, --mode MODE         double | single (default: $MODE_DEFAULT)
  -f, --format FMT        yuyv422 | mjpeg (default: $INPUT_FORMAT_DEFAULT)
  -o, --outdir DIR        Output directory (default: "$OUTDIR_DEFAULT")
  -b, --basename NAME     Basename for output (default auto)
  -n, --notify URL        ntfy base URL (e.g. http://localhost:2586)
  -t, --topic TOPIC       ntfy topic (default: $NTFY_TOPIC_DEFAULT)
  -k, --token TOKEN       ntfy bearer token (optional)
  -d, --detached          Run under user systemd (survives logout)
  -x, --debug             Verbose tracing
  -h, --help              Show help
EOF
}

# ----- Parse args -----
[[ $# -lt 1 ]] && { usage; exit 1; }
DUR="$1"; shift || true

VID="$VID_DEFAULT"
PULSE_SRC="$PULSE_SRC_DEFAULT"
STANDARD="$STANDARD_DEFAULT"
MODE="$MODE_DEFAULT"
INPUT_FORMAT="$INPUT_FORMAT_DEFAULT"
OUTDIR="$OUTDIR_DEFAULT"
BASENAME=""
DETACHED="0"
DEBUG="0"
NTFY_URL="$NTFY_URL_DEFAULT"
NTFY_TOPIC="$NTFY_TOPIC_DEFAULT"
NTFY_TOKEN="$NTFY_TOKEN_DEFAULT"

while (( "$#" )); do
  case "$1" in
    -v|--video)        VID="$2"; shift 2;;
    -a|--audio)        PULSE_SRC="$2"; shift 2;;
    -s|--standard)     STANDARD="$2"; shift 2;;
    -m|--mode)         MODE="$2"; shift 2;;
    -f|--format)       INPUT_FORMAT="$2"; shift 2;;
    -o|--outdir)       OUTDIR="$2"; shift 2;;
    -b|--basename)     BASENAME="$2"; shift 2;;
    -n|--notify)       NTFY_URL="$2"; shift 2;;
    -t|--topic)        NTFY_TOPIC="$2"; shift 2;;
    -k|--token)        NTFY_TOKEN="$2"; shift 2;;
    -d|--detached)     DETACHED="1"; shift;;
    -x|--debug)        DEBUG="1"; shift;;
    -h|--help)         usage; exit 0;;
    *) echo "Unknown option: $1" >&2; usage; exit 2;;
  esac
done

command -v ffmpeg >/dev/null || { echo "ERROR: ffmpeg not found"; exit 10; }
command -v timeout >/dev/null || { echo "ERROR: timeout not found"; exit 11; }
[[ -e "$VID" ]] || { echo "ERROR: Video device not found: $VID"; exit 12; }
mkdir -p "$OUTDIR"

# ----- Timing / geometry -----
if [[ "$STANDARD" == "ntsc" ]]; then
  SAR="8/9"
  if [[ "$MODE" == "double" ]]; then FPS_FILTER="fps=60000/1001"; GOP=60; TAG="ntsc59";
  else                                FPS_FILTER="fps=30000/1001"; GOP=60; TAG="ntsc30"; fi
elif [[ "$STANDARD" == "pal" ]]; then
  SAR="8/9"  # many dongles still present 720x480; force DAR via setdar
  if [[ "$MODE" == "double" ]]; then FPS_FILTER="fps=50"; GOP=50; TAG="pal50";
  else                                FPS_FILTER="fps=25"; GOP=50; TAG="pal25"; fi
else
  echo "ERROR: Unknown --standard '$STANDARD' (use ntsc or pal)" >&2; exit 13
fi

DATESTAMP="$(date +%F_%H-%M-%S)"
[[ -z "$BASENAME" ]] && BASENAME="capture_${TAG}_${DATESTAMP}"
OUTFILE="${OUTDIR%/}/${BASENAME}.mkv"
LOGFILE="${OUTDIR%/}/${BASENAME}.log"

# ----- ffmpeg command (one string so we can embed) -----
FFMPEG_CMD=$(cat <<'EOF_CMD'
timeout --signal=INT "$DUR" ffmpeg \
  -f v4l2 -standard "$STANDARD" -thread_queue_size 4096 \
  -input_format "$INPUT_FORMAT" -video_size 720x480 \
  -i "$VID" \
  -f pulse -thread_queue_size 4096 -ar 48000 -ac 2 \
  -i "$PULSE_SRC" \
  -fflags +genpts -use_wallclock_as_timestamps 1 \
  -map 0:v:0 -map 1:a:0 \
  -filter:v "yadif=mode=$( [[ "$MODE" == "double" ]] && echo 1 || echo 0 ):parity=auto:deint=all,setsar=$SAR,setdar=4/3,${FPS_FILTER}" \
  -c:v h264_nvenc -pix_fmt yuv420p -rc constqp -cq 18 -preset p5 -profile:v high -g "$GOP" \
  -spatial-aq 1 -aq-strength 8 -temporal-aq 1 \
  -c:a aac -b:a 192k \
  -af "volume=0.90,alimiter=limit=0.95:level=disabled,aresample=async=1000" \
  "$OUTFILE"
EOF_CMD
)

# ----- ntfy helpers (foreground only; detached defines its own) -----
CURL="/usr/bin/curl"
notify_start_fg() {
  [[ -z "$NTFY_URL" ]] && return 0
  if [[ -n "$NTFY_TOKEN" ]]; then
    "$CURL" -sS -o /dev/null -w "%{http_code}\n" \
      -H "Authorization: Bearer $NTFY_TOKEN" \
      -H "Title: VHS capture started" -H "Priority: 3" \
      -d "Saving to: $OUTFILE" \
      "$NTFY_URL/$NTFY_TOPIC" || true
  else
    "$CURL" -sS -o /dev/null -w "%{http_code}\n" \
      -H "Title: VHS capture started" -H "Priority: 3" \
      -d "Saving to: $OUTFILE" \
      "$NTFY_URL/$NTFY_TOPIC" || true
  fi
}
notify_finish_fg() {
  [[ -z "$NTFY_URL" ]] && return 0
  local rc="$1"
  local msg="File: $OUTFILE%0ALog: $LOGFILE%0AExit: ${rc}"
  if [[ -n "$NTFY_TOKEN" ]]; then
    "$CURL" -sS -o /dev/null -w "%{http_code}\n" \
      -H "Authorization: Bearer $NTFY_TOKEN" \
      -H "Title: VHS capture complete" -H "Priority: 5" \
      -d "$msg" "$NTFY_URL/$NTFY_TOPIC" || true
  else
    "$CURL" -sS -o /dev/null -w "%{http_code}\n" \
      -H "Title: VHS capture complete" -H "Priority: 5" \
      -d "$msg" "$NTFY_URL/$NTFY_TOPIC" || true
  fi
}

# ===== Run =====
if [[ "$DETACHED" == "1" ]]; then
  UNIT="vhs-$(date +%s)"
  [[ "$DEBUG" == "1" ]] && echo "Launching detached as unit: $UNIT"

  # Build the child script with NO expansion (single-quoted heredoc)
  CHILD=$(cat <<'CHILD_EOF'
set -uo pipefail

# placate nounset for injected vars
: "${ENV_URL:=}"; : "${ENV_TOPIC:=}"; : "${ENV_TOKEN:=}"
: "${OUTFILE:=}"; : "${LOGFILE:=}"; : "${DUR:=}"
: "${VID:=}"; : "${PULSE_SRC:=}"; : "${STANDARD:=}"; : "${MODE:=}"
: "${INPUT_FORMAT:=}"; : "${SAR:=}"; : "${GOP:=}"; : "${FPS_FILTER:=}"

CURL='/usr/bin/curl'

CURL='/usr/bin/curl'

# Hard-coded Bearer for detached mode (no env needed)
AUTH_HEADER=(-H "Authorization: Bearer tk_rn8rgqfy2uvvq4xp5lm7d035fvgsi")

notify_start() {
  [[ -z "${ENV_URL}" ]] && return 0
  local tmp; tmp="$(mktemp)"
  local code
  code=$($CURL -sS -o "$tmp" -w "%{http_code}\n" \
        -H "Authorization: Bearer tk_rn8rgqfy2uvvq4xp5lm7d035fvgsi" \
        -H "Title: VHS capture started" -H "Priority: 3" \
        -d "Saving to: ${OUTFILE}" \
        "${ENV_URL}/${ENV_TOPIC}" || true)
  echo "ntfy start HTTP: $code"
  if [[ "$code" -ge 400 ]]; then echo "ntfy start body: $(cat "$tmp")"; fi
  rm -f "$tmp"
}

notify_finish() {
  [[ -z "${ENV_URL}" ]] && return 0
  local rc="$1"
  local msg="File: ${OUTFILE}%0ALog: ${LOGFILE}%0AExit: ${rc}"
  local tmp; tmp="$(mktemp)"
  local code
  code=$($CURL -sS -o "$tmp" -w "%{http_code}\n" \
        -H "Authorization: Bearer tk_rn8rgqfy2uvvq4xp5lm7d035fvgsi" \
        -H "Title: VHS capture complete" -H "Priority: 5" \
        -d "$msg" \
        "${ENV_URL}/${ENV_TOPIC}" || true)
  echo "ntfy end HTTP: $code"
  if [[ "$code" -ge 400 ]]; then echo "ntfy end body: $(cat "$tmp")"; fi
  rm -f "$tmp"
}

rc=0
trap 'rc=$?; echo "== Trap exit rc=$rc at $(date) ==" >> "${LOGFILE}"; notify_finish "$rc" >> "${LOGFILE}" 2>&1' EXIT

# Log everything
exec > >(tee -a "${LOGFILE}") 2>&1
echo "== Starting capture at $(date) =="
echo -n "ntfy start HTTP: "; notify_start

timeout --signal=INT "${DUR}" ffmpeg \
  -f v4l2 -standard "${STANDARD}" -thread_queue_size 4096 \
  -input_format "${INPUT_FORMAT}" -video_size 720x480 \
  -i "${VID}" \
  -f pulse -thread_queue_size 4096 -ar 48000 -ac 2 \
  -i "${PULSE_SRC}" \
  -fflags +genpts -use_wallclock_as_timestamps 1 \
  -map 0:v:0 -map 1:a:0 \
  -filter:v "yadif=mode=$( [[ "${MODE}" == "double" ]] && echo 1 || echo 0 ):parity=auto:deint=all,setsar=${SAR},setdar=4/3,${FPS_FILTER}" \
  -c:v h264_nvenc -pix_fmt yuv420p -rc constqp -cq 18 -preset p5 -profile:v high -g "${GOP}" \
  -spatial-aq 1 -aq-strength 8 -temporal-aq 1 \
  -c:a aac -b:a 192k \
  -af "volume=0.90,alimiter=limit=0.95:level=disabled,aresample=async=1000" \
  "${OUTFILE}"

rc=$?
case "$rc" in
  0|124|130) echo "== Capture finished (rc=$rc) at $(date) ==" ;;
  *)         echo "== Capture FAILED (rc=$rc) at $(date) ==" ;;
esac
exit "$rc"
CHILD_EOF
)

  # Export values for child and launch
  ENV_URL="$NTFY_URL" \
  ENV_TOPIC="$NTFY_TOPIC" \
  ENV_TOKEN="$NTFY_TOKEN" \
  OUTFILE="$OUTFILE" \
  LOGFILE="$LOGFILE" \
  DUR="$DUR" \
  VID="$VID" \
  PULSE_SRC="$PULSE_SRC" \
  STANDARD="$STANDARD" \
  MODE="$MODE" \
  INPUT_FORMAT="$INPUT_FORMAT" \
  SAR="$SAR" \
  GOP="$GOP" \
  FPS_FILTER="$FPS_FILTER" \
  systemd-run --user --unit="$UNIT" --collect \
    --setenv=ENV_URL="$NTFY_URL" \
    --setenv=ENV_TOPIC="$NTFY_TOPIC" \
    --setenv=OUTFILE="$OUTFILE" \
    --setenv=LOGFILE="$LOGFILE" \
    --setenv=DUR="$DUR" \
    --setenv=VID="$VID" \
    --setenv=PULSE_SRC="$PULSE_SRC" \
    --setenv=STANDARD="$STANDARD" \
    --setenv=MODE="$MODE" \
    --setenv=INPUT_FORMAT="$INPUT_FORMAT" \
    --setenv=SAR="$SAR" \
    --setenv=GOP="$GOP" \
    --setenv=FPS_FILTER="$FPS_FILTER" \
    bash -lc "$CHILD"

  echo "Detached as unit: $UNIT"
  echo "Status:   systemctl --user status $UNIT"
  echo "Logs:     journalctl --user -u $UNIT -e"
  echo "File/log: $OUTFILE  |  $LOGFILE"

else
  [[ "$DEBUG" == "1" ]] && set -x
  echo "== Starting capture at $(date) =="
  if [[ -n "$NTFY_URL" ]]; then
    echo -n "ntfy start HTTP: "; notify_start_fg
  fi
  set +e
  eval "$FFMPEG_CMD" 2> >(tee -a "$LOGFILE" >&2)
  rc=$?
  set -e
  case "$rc" in
    0|124|130) echo "== Capture finished (rc=$rc) at $(date) ==" ;;
    *)         echo "== Capture FAILED (rc=$rc) at $(date) ==" ;;
  esac
  if [[ -n "$NTFY_URL" ]]; then
    echo -n "ntfy end HTTP: "; notify_finish_fg "$rc"
  fi
  echo "File/log: $OUTFILE  |  $LOGFILE"
fi
# End