#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 WORKSPACE --model PATH --compiled-checkpoint PATH [coding-chat options]" >&2
  exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "${script_dir}/../.." && pwd)
workspace=$1
shift

if [[ ! -d "${workspace}" ]]; then
  echo "Workspace does not exist or is not a directory: ${workspace}" >&2
  exit 2
fi

endpoint=${VLLM_CHAT_URL:-http://127.0.0.1:8000/v1}
python_bin=${VLLM_CHAT_PYTHON:-${repo_root}/.venv/bin/python}
endpoint_args=()
chat_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model|--compiled-checkpoint)
      endpoint_args+=("$1" "${2:?"$1 requires a path"}")
      shift 2
      ;;
    --model=*|--compiled-checkpoint=*)
      endpoint_args+=("$1")
      shift
      ;;
    *)
      chat_args+=("$1")
      shift
      ;;
  esac
done

"${script_dir}/start_endpoint.sh" "${endpoint_args[@]}"

if [[ ! -x "${python_bin}" ]]; then
  echo "Python environment does not exist: ${python_bin}" >&2
  echo "Set VLLM_CHAT_PYTHON to the intended vLLM Python executable." >&2
  exit 1
fi

exec "${python_bin}" "${script_dir}/coding_chat.py" \
  --url "${endpoint}" \
  --workspace "${workspace}" \
  "${chat_args[@]}"
