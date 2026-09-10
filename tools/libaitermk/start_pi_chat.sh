#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 WORKSPACE [--dangerously-skip-permissions] [--web-access] [Pi options]" >&2
  exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "${script_dir}/../.." && pwd)
pi_dir=${script_dir}/pi
pi_entry=${pi_dir}/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js
extension=${pi_dir}/libaitermk.ts
speed_extension=${pi_dir}/node_modules/pi-token-speed/index.ts
web_extension=${pi_dir}/node_modules/pi-agent-web-access/index.ts
workspace=$1
shift

if [[ ! -d "${workspace}" ]]; then
  echo "Workspace does not exist or is not a directory: ${workspace}" >&2
  exit 2
fi

endpoint=${VLLM_CHAT_URL:-http://127.0.0.1:8000/v1}
api_key=${VLLM_CHAT_API_KEY:-EMPTY}
max_tokens=${VLLM_CHAT_MAX_TOKENS:-4096}
context_window=${VLLM_CHAT_CONTEXT_WINDOW:-65536}
write_policy=ask
shell_policy=ask
stats=${LIBAITERMK_PI_STATS:-0}
web_access=0
pi_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)
      endpoint=$2
      shift 2
      ;;
    --url=*)
      endpoint=${1#*=}
      shift
      ;;
    --api-key)
      api_key=$2
      shift 2
      ;;
    --api-key=*)
      api_key=${1#*=}
      shift
      ;;
    --max-tokens)
      max_tokens=$2
      shift 2
      ;;
    --max-tokens=*)
      max_tokens=${1#*=}
      shift
      ;;
    --max-model-len)
      context_window=$2
      shift 2
      ;;
    --max-model-len=*)
      context_window=${1#*=}
      shift
      ;;
    --write-policy)
      write_policy=$2
      shift 2
      ;;
    --write-policy=*)
      write_policy=${1#*=}
      shift
      ;;
    --shell-policy)
      shell_policy=$2
      shift 2
      ;;
    --shell-policy=*)
      shell_policy=${1#*=}
      shift
      ;;
    --dangerously-skip-permissions)
      write_policy=allow
      shell_policy=allow
      shift
      ;;
    --web-access)
      web_access=1
      shift
      ;;
    --stats)
      stats=1
      shift
      ;;
    -q|--quick)
      pi_args+=("$2")
      shift 2
      ;;
    *)
      pi_args+=("$1")
      shift
      ;;
  esac
done

health_url=${endpoint%/}
health_url=${health_url%/v1}/health
if ! curl -fsS --max-time 2 "${health_url}" >/dev/null; then
  echo "Endpoint is not healthy at ${endpoint}" >&2
  echo "Start the vLLM endpoint before launching Pi." >&2
  exit 1
fi

node_bin=${LIBAITERMK_PI_NODE:-node}
if ! command -v "${node_bin}" >/dev/null 2>&1; then
  echo "Node.js 22.19 or newer is required: ${node_bin}" >&2
  exit 1
fi
if ! "${node_bin}" -e '
  const [major, minor] = process.versions.node.split(".").map(Number);
  process.exit(major > 22 || (major === 22 && minor >= 19) ? 0 : 1);
'; then
  echo "Node.js 22.19 or newer is required: $("${node_bin}" --version)" >&2
  exit 1
fi
if [[ ! -f "${pi_entry}" ]]; then
  echo "Pi is not installed under ${pi_dir}" >&2
  echo "Run: cd ${pi_dir} && npm ci" >&2
  exit 1
fi
if [[ "${stats}" == "1" && ! -f "${speed_extension}" ]]; then
  echo "The decode-speed meter is not installed under ${pi_dir}" >&2
  echo "Run: cd ${pi_dir} && npm ci" >&2
  exit 1
fi
if [[ "${web_access}" == "1" && ! -f "${web_extension}" ]]; then
  echo "The web-access extension is not installed under ${pi_dir}" >&2
  echo "Run: cd ${pi_dir} && npm ci" >&2
  exit 1
fi

models_url=${endpoint%/}/models
if ! model=$(
  curl -fsS --max-time 10 \
    -H "Authorization: Bearer ${api_key}" \
    "${models_url}" \
    | "${node_bin}" -e '
      let input = "";
      process.stdin.setEncoding("utf8");
      process.stdin.on("data", chunk => { input += chunk; });
      process.stdin.on("end", () => {
        const models = JSON.parse(input).data;
        if (!Array.isArray(models) || models.length === 0 || !models[0].id) {
          console.error("The endpoint returned no served model ID.");
          process.exit(1);
        }
        process.stdout.write(String(models[0].id));
      });
    '
); then
  echo "Unable to discover the served model from ${models_url}" >&2
  exit 1
fi

agent_dir=${PI_CODING_AGENT_DIR:-${repo_root}/.cache/libaitermk/pi}
session_dir=${PI_CODING_AGENT_SESSION_DIR:-${agent_dir}/sessions}
metrics_file=${LIBAITERMK_PI_METRICS_FILE:-${agent_dir}/metrics.jsonl}
mkdir -p "${agent_dir}" "${session_dir}" "$(dirname -- "${metrics_file}")"

export PI_CODING_AGENT_DIR=${agent_dir}
export PI_CODING_AGENT_SESSION_DIR=${session_dir}
export VLLM_CHAT_URL=${endpoint}
export VLLM_CHAT_MODEL=${model}
export VLLM_CHAT_API_KEY=${api_key}
export VLLM_CHAT_MAX_TOKENS=${max_tokens}
export VLLM_CHAT_CONTEXT_WINDOW=${context_window}
export LIBAITERMK_PI_WRITE_POLICY=${write_policy}
export LIBAITERMK_PI_SHELL_POLICY=${shell_policy}
export LIBAITERMK_PI_STATS=${stats}
export LIBAITERMK_PI_METRICS_FILE=${metrics_file}

extension_args=(--extension "${extension}")
if [[ "${stats}" == "1" ]]; then
  extension_args+=(--extension "${speed_extension}")
fi
if [[ "${web_access}" == "1" ]]; then
  extension_args+=(--extension "${web_extension}")
fi

echo "Pi permissions: write=${write_policy}, shell=${shell_policy}, web_access=${web_access}" >&2

cd -- "${workspace}"
exec "${node_bin}" "${pi_entry}" \
  --no-extensions \
  "${extension_args[@]}" \
  --provider libaitermk-vllm \
  --model "${model}" \
  --thinking off \
  --session-dir "${session_dir}" \
  "${pi_args[@]}"
