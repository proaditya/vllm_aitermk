#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
pi_dir=${script_dir}/pi
demo_dir=${pi_dir}/node_modules/pi-token-speed
tsx=${pi_dir}/node_modules/.bin/tsx
workspace=$PWD
port=${PI_SPEED_DEMO_PORT:-8790}
write_policy=${LIBAITERMK_PI_WRITE_POLICY:-ask}
shell_policy=${LIBAITERMK_PI_SHELL_POLICY:-ask}
web_access=${LIBAITERMK_PI_WEB_ACCESS:-0}
persist=${PI_SPEED_DEMO_PERSIST:-0}

usage() {
  cat <<EOF
Usage: $0 [WORKSPACE] [options]

Options:
  --dangerously-skip-permissions  Allow Pi file writes and shell commands
  --web-access                    Enable Pi's read-only web tools
  --persist                       Save the Pi session instead of starting ephemeral
  --port PORT                     Browser bridge port (default: 8790)
  -h, --help                      Show this help
EOF
}

if [[ $# -gt 0 && $1 != -* ]]; then
  workspace=$1
  shift
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dangerously-skip-permissions)
      write_policy=allow
      shell_policy=allow
      shift
      ;;
    --web-access)
      web_access=1
      shift
      ;;
    --persist)
      persist=1
      shift
      ;;
    --port)
      port=${2:?"--port requires a value"}
      shift 2
      ;;
    --port=*)
      port=${1#*=}
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "${workspace}" ]]; then
  echo "Workspace does not exist or is not a directory: ${workspace}" >&2
  exit 2
fi
if [[ ! -x "${tsx}" || ! -f "${demo_dir}/demo/server.ts" ]]; then
  echo "Pi browser dependencies are not installed under ${pi_dir}" >&2
  echo "Run: cd ${pi_dir} && npm ci --ignore-scripts" >&2
  exit 1
fi

endpoint=${VLLM_CHAT_URL:-http://127.0.0.1:8000/v1}
health_url=${endpoint%/}
health_url=${health_url%/v1}/health
if ! curl -fsS --max-time 2 "${health_url}" >/dev/null; then
  echo "Endpoint is not healthy at ${endpoint}" >&2
  echo "Start it with tools/libaitermk/start_endpoint.sh first." >&2
  exit 1
fi

export PI_SPEED_DEMO_MODE=pi
export PI_SPEED_DEMO_HOST=${PI_SPEED_DEMO_HOST:-0.0.0.0}
export PI_SPEED_DEMO_PORT=${port}
export PI_SPEED_DEMO_CWD=${workspace}
export PI_SPEED_DEMO_PERSIST=${persist}
export PI_BIN=${script_dir}/start_pi_chat.sh
export LIBAITERMK_PI_WRITE_POLICY=${write_policy}
export LIBAITERMK_PI_SHELL_POLICY=${shell_policy}
export LIBAITERMK_PI_WEB_ACCESS=${web_access}
export LIBAITERMK_PI_STATS=1

echo "Starting Pi browser bridge for workspace: ${workspace}" >&2
echo "The bridge runs in this terminal; press Ctrl+C to stop it." >&2

cd -- "${demo_dir}"
exec "${tsx}" demo/server.ts
