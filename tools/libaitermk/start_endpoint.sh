#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "${script_dir}/../.." && pwd)

model_path=
compiled_model_path=
python_bin=${VLLM_SERVER_PYTHON:-${repo_root}/.venv/bin/python}
server_port=${VLLM_SERVER_PORT:-8000}
endpoint=${VLLM_CHAT_URL:-http://127.0.0.1:${server_port}/v1}
model_name=
result_base=${VLLM_RESULT_DIR:-${repo_root}/.cache/libaitermk/endpoint}
cache_base=${VLLM_CACHE_DIR:-${repo_root}/.cache/libaitermk/compiler}
max_model_len=65536
adapter_path=${REDLINE_VLLM_ADAPTER_PATH:-}
health_url=${endpoint%/}
health_url=${health_url%/v1}/health

usage() {
  cat <<EOF
Usage: $0 --model PATH --compiled-checkpoint PATH [options]

Options:
  --model PATH                 Source model directory
  --compiled-checkpoint PATH   libAiterMK compiled checkpoint directory
  --served-model-name NAME     API model name (default: source directory name)
  --max-model-len TOKENS       Context window (default: 65536)
  -h, --help                   Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)
      model_path=${2:?"--model requires a path"}
      shift 2
      ;;
    --model=*)
      model_path=${1#*=}
      shift
      ;;
    --compiled-checkpoint)
      compiled_model_path=${2:?"--compiled-checkpoint requires a path"}
      shift 2
      ;;
    --compiled-checkpoint=*)
      compiled_model_path=${1#*=}
      shift
      ;;
    --served-model-name)
      model_name=${2:?"--served-model-name requires a name"}
      shift 2
      ;;
    --served-model-name=*)
      model_name=${1#*=}
      shift
      ;;
    --max-model-len)
      max_model_len=${2:?"--max-model-len requires a value"}
      shift 2
      ;;
    --max-model-len=*)
      max_model_len=${1#*=}
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

if [[ -z "${model_path}" ]]; then
  echo "--model must name the source-model directory." >&2
  usage >&2
  exit 2
fi
if [[ -z "${compiled_model_path}" ]]; then
  echo "--compiled-checkpoint must name the compiled-checkpoint directory." >&2
  usage >&2
  exit 2
fi
if [[ -z "${model_name}" ]]; then
  model_name=$(basename -- "${model_path%/}")
fi
if [[ ! -x "${python_bin}" ]]; then
  echo "vLLM Python environment does not exist: ${python_bin}" >&2
  echo "Set VLLM_SERVER_PYTHON when the repository venv is elsewhere." >&2
  exit 1
fi
if [[ ! -f "${model_path}/config.json" ]]; then
  echo "Source model is missing or incomplete: ${model_path}" >&2
  exit 1
fi
if [[ ! -f "${compiled_model_path}/config.json" ]]; then
  echo "Compiled checkpoint is missing or incomplete: ${compiled_model_path}" >&2
  exit 1
fi

if curl -fsS --max-time 2 "${health_url}" >/dev/null 2>&1; then
  echo "Endpoint is already healthy at ${endpoint}"
  exit 0
fi

torchinductor_cache=${TORCHINDUCTOR_CACHE_DIR:-${cache_base}/torchinductor}
triton_cache=${TRITON_CACHE_DIR:-${cache_base}/triton}
mkdir -p "${result_base}" "${torchinductor_cache}" "${triton_cache}"
result_dir=$(mktemp -d "${result_base%/}/run.XXXXXXXX")
server_log=${result_dir}/server.log

python_path=${PYTHONPATH:-}
if [[ -n "${adapter_path}" ]]; then
  python_path=${adapter_path}${python_path:+:${python_path}}
fi

cd -- "${repo_root}"
nohup env \
  VLLM_ROCM_USE_AITER=1 \
  VLLM_PLUGINS=redline_megakernel \
  REDLINE_VLLM_ENABLE=1 \
  REDLINE_VLLM_MODE=direct \
  REDLINE_VLLM_RUNTIME_API=c_v1 \
  VLLM_USE_V2_MODEL_RUNNER=0 \
  REDLINE_VLLM_ENABLE_QUANTUM_K8=1 \
  REDLINE_COMPILED_MODEL_PATH="${compiled_model_path}" \
  REDLINE_VLLM_KV_ABI_EVIDENCE_PATH="${result_dir}/kv-abi.json" \
  REDLINE_VLLM_COUNTER_EVIDENCE_PATH="${result_dir}/native-counters.json" \
  TORCHINDUCTOR_CACHE_DIR="${torchinductor_cache}" \
  TRITON_CACHE_DIR="${triton_cache}" \
  PYTHONPATH="${python_path}" \
  "${python_bin}" -m vllm.entrypoints.cli.main serve \
  "${model_path}" \
  --served-model-name "${model_name}" \
  --host 0.0.0.0 \
  --port "${server_port}" \
  --dtype bfloat16 \
  --kv-cache-dtype auto \
  --block-size 64 \
  --max-model-len "${max_model_len}" \
  --max-num-seqs 1 \
  --tensor-parallel-size 1 \
  --pipeline-parallel-size 1 \
  --data-parallel-size 1 \
  --gpu-memory-utilization 0.90 \
  --enforce-eager \
  --generation-config vllm \
  --override-generation-config '{"temperature":0}' \
  --enable-auto-tool-choice \
  --tool-call-parser openai \
  --no-enable-chunked-prefill \
  --enable-prefix-caching \
  --disable-hybrid-kv-cache-manager \
  --no-async-scheduling \
  --attention-backend ROCM_AITER_UNIFIED_ATTN \
  --worker-cls redline_vllm.worker.RedlineGPUWorker \
  --scheduler-cls redline_vllm.scheduler.RedlineQuantumScheduler \
  >"${server_log}" 2>&1 &
server_pid=$!
printf '%s\n' "${server_pid}" >"${result_dir}/server.pid"

for _ in $(seq 1 180); do
  if curl -fsS --max-time 2 "${health_url}" >/dev/null 2>&1; then
    echo "Endpoint is healthy at ${endpoint}"
    echo "Server artifacts: ${result_dir}"
    exit 0
  fi
  if ! kill -0 "${server_pid}" >/dev/null 2>&1; then
    echo "Endpoint process exited during startup." >&2
    tail -200 "${server_log}" >&2 || true
    exit 1
  fi
  sleep 5
done

echo "Endpoint did not become ready within 15 minutes." >&2
tail -200 "${server_log}" >&2 || true
exit 1
