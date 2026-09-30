#!/usr/bin/env bash
# Install a released noaap into the venv the systemd unit runs, and restart it (DESIGN §9, slice 93).
#
# **Why a script and not a `noaap` subcommand.** The thing being replaced is a venv, and a subcommand
# would ship *inside* it: running it there means a process overwriting the files it is executing from.
# It also has to be able to install a tag whose own code predates this procedure, which a subcommand
# in that tag cannot do. So this runs from a checkout, needs only git, uv and systemctl, and can be
# read before it touches anything.
#
# It builds from the **tag**, not from the working tree: the tag is extracted with `git archive` into a
# temporary directory and built there, so a dirty checkout cannot leak into a release.
#
#   scripts/release-install.sh                 # the tag at HEAD
#   scripts/release-install.sh v1.22.0         # a named tag
#   scripts/release-install.sh --no-restart    # install, leave the running service alone
#
# Undo: `scripts/release-install.sh <the previous tag>`, or point the unit back at a checkout with
# `noaap service install --from-checkout`.

set -euo pipefail

VENV="${NOAAP_RELEASE_VENV:-$HOME/.local/noaap-release}"
EXTRAS="${NOAAP_RELEASE_EXTRAS:-timing,timing-check}"
PYTHON="${NOAAP_RELEASE_PYTHON:-3.14}"
UNIT="noaap.service"
restart=yes
allow_dirty=no
allow_untagged=no
tag=""

die() { printf 'release-install: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --no-restart) restart=no ;;
    --allow-dirty) allow_dirty=yes ;;
    --allow-untagged) allow_untagged=yes ;;
    --venv) shift; [ $# -gt 0 ] || die "--venv needs a path"; VENV="$1" ;;
    --extras) shift; [ $# -gt 0 ] || die "--extras needs a list, or \"\" for none"; EXTRAS="$1" ;;
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
    -*) die "unknown option $1" ;;
    *) [ -z "$tag" ] || die "one tag at a time (got $tag and $1)"; tag="$1" ;;
  esac
  shift
done

repo="$(cd "$(dirname "$0")/.." && pwd)"
[ -d "$repo/.git" ] || die "$repo is not a checkout"
command -v git >/dev/null || die "git is not installed"
command -v uv >/dev/null || die "uv is not installed — see https://docs.astral.sh/uv/"

if [ "$allow_dirty" = no ] && [ -n "$(git -C "$repo" status --porcelain)" ]; then
  die "the checkout has uncommitted changes — commit them, or pass --allow-dirty (the tag is what gets built either way)"
fi

if [ -z "$tag" ]; then
  tag="$(git -C "$repo" describe --tags --exact-match HEAD 2>/dev/null || true)"
  if [ -z "$tag" ]; then
    [ "$allow_untagged" = yes ] || die "HEAD is not a tagged commit — name a tag, or pass --allow-untagged to release $(git -C "$repo" rev-parse --short HEAD)"
    tag="$(git -C "$repo" rev-parse HEAD)"
  fi
elif ! git -C "$repo" rev-parse --verify --quiet "$tag^{commit}" >/dev/null; then
  die "there is no tag or commit named $tag in $repo"
fi

# **never /tmp.** uv hardlinks a venv's files from its cache when both are on one filesystem, and
# copies them otherwise: a release venv on tmpfs writes ~7 GB of real blocks (measured: it filled the
# quota), where one under $HOME costs tens of megabytes.
case "$VENV" in
  "$HOME"/*) ;;
  *) die "the release venv must live under $HOME (got $VENV): elsewhere uv copies the whole of torch" ;;
esac
case "$VENV" in
  /tmp/*|/var/tmp/*|/dev/shm/*) die "the release venv must not be in a temporary filesystem: $VENV" ;;
esac

work="$(mktemp -d "${TMPDIR:-/tmp}/noaap-release.XXXXXX")"
trap 'rm -rf "$work"' EXIT

say "building $tag (from the tag, not from the working tree)"
git -C "$repo" archive --format=tar "$tag" | tar -x -C "$work"
uv build --quiet --wheel --out-dir "$work/dist" "$work"
wheel="$(ls "$work"/dist/*.whl)"
[ -f "$wheel" ] || die "no wheel was built"
say "built $(basename "$wheel")"

if [ ! -x "$VENV/bin/python" ]; then
  say "creating the release venv at $VENV (python $PYTHON)"
  uv venv --quiet --python "$PYTHON" "$VENV"
fi

spec="noaap @ $wheel"
[ -z "$EXTRAS" ] || spec="noaap[$EXTRAS] @ $wheel"
say "installing $spec"
uv pip install --quiet --python "$VENV/bin/python" "$spec"
# `noaap --version` exists from 1.23.0 on; anything older still has its metadata to read
installed="$("$VENV/bin/noaap" --version 2>/dev/null || true)"
if [ -z "$installed" ]; then
  installed="$("$VENV/bin/python" -c 'from importlib.metadata import version; print("noaap", version("noaap"))' 2>/dev/null || true)"
fi
say "the release venv holds ${installed:-$(basename "$wheel")}"

# the unit has to name this venv, or the release is installed and nothing runs it
unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$UNIT"
if [ ! -f "$unit" ]; then
  say "note: $UNIT is not installed — 'noaap service install' writes it, and it will name this venv"
elif ! grep -q "^ExecStart=$VENV/bin/noaap" "$unit"; then
  say "note: $unit still starts $(sed -n 's/^ExecStart=\([^ ]*\).*/\1/p' "$unit") — run 'noaap service install' once to point it here"
fi

if [ "$restart" = yes ] && [ -f "$unit" ]; then
  say "restarting $UNIT"
  systemctl --user restart "$UNIT" || die "systemctl --user restart $UNIT failed"
  port="$(sed -n 's/.*ListenStream=.*:\([0-9]\{1,\}\).*/\1/p' "${unit%.service}.socket" 2>/dev/null | head -1)"
  port="${port:-8765}"
  for _ in $(seq 40); do
    said="$(curl -fsS --max-time 2 "http://127.0.0.1:$port/api/state" 2>/dev/null || true)"
    if [ -n "$said" ]; then
      python3 - "$said" <<'PY'
import json, sys
state = json.loads(sys.argv[1])
s = state.get("settings", {})
print(f"the service on this port reports {s.get('version', '?')} — {s.get('running_from', '?')}")
PY
      exit 0
    fi
    sleep 0.25
  done
  die "the service did not answer on port $port after the restart — see 'systemctl --user status $UNIT'"
fi

if [ "$restart" = no ]; then
  say "done — the running service was left alone; it picks this up on its next start"
else
  say "done — there is no $UNIT to restart"
fi
