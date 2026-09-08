#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
set -euo pipefail

container=${LIBAITERMK_CONTAINER:-libaitermk-vllm}
endpoint=${VLLM_CHAT_URL:-http://127.0.0.1:8000/v1}
model_path=${VLLM_MODEL_PATH:-/models/openai/gpt-oss-120b}
model_name=${VLLM_MODEL_NAME:-gpt-oss-120b}
result_base=${VLLM_RESULT_DIR:-/workspace/vllm_aitermk/results/libaitermk-coding-chat/endpoint}
server_port=${VLLM_SERVER_PORT:-8000}
max_model_len=${VLLM_MAX_MODEL_LEN:-65536}
adapter_path=${REDLINE_VLLM_ADAPTER_PATH:-}
compiled_model_path=${REDLINE_COMPILED_MODEL_PATH:-/compiled-checkpoint}
health_url=${endpoint%/}
health_url=${health_url%/v1}/health

if curl -fsS --max-time 2 "${health_url}" >/dev/null 2>&1; then
  echo "Endpoint is already healthy at ${endpoint}"
  exit 0
fi

if ! docker container inspect "${container}" >/dev/null 2>&1; then
  echo "Container does not exist: ${container}" >&2
  echo "Set LIBAITERMK_CONTAINER to an existing configured container." >&2
  exit 1
fi

if [[ "$(docker container inspect -f '{{.State.Running}}' "${container}")" != true ]]; then
  docker start "${container}" >/dev/null
fi

if ! docker exec "${container}" test -f "${compiled_model_path}/config.json"; then
  echo "Compiled Redline checkpoint is missing or incomplete: ${compiled_model_path}" >&2
  echo "Mount a complete ABI-compatible checkpoint into the container." >&2
  exit 1
fi

launch_id=$(date -u +%Y%m%dT%H%M%SZ)
result_dir=${result_base}/${launch_id}
docker exec "${container}" mkdir -p \
  "${result_dir}" \
  /tmp/torchinductor-libaitermk \
  /tmp/triton-libaitermk

docker exec -d \
  -e VLLM_RESULT_DIR="${result_dir}" \
  -e REDLINE_VLLM_KV_ABI_EVIDENCE_PATH="${result_dir}/kv-abi.json" \
  -e REDLINE_VLLM_COUNTER_EVIDENCE_PATH="${result_dir}/native-counters.json" \
  -e TORCHINDUCTOR_CACHE_DIR=/tmp/torchinductor-libaitermk \
  -e TRITON_CACHE_DIR=/tmp/triton-libaitermk \
  -e REDLINE_VLLM_ADAPTER_PATH="${adapter_path}" \
  -e REDLINE_COMPILED_MODEL_PATH="${compiled_model_path}" \
  "${container}" \
  /bin/bash -lc '
    if [[ -n "${REDLINE_VLLM_ADAPTER_PATH}" ]]; then
      export PYTHONPATH="${REDLINE_VLLM_ADAPTER_PATH}:${PYTHONPATH:-}"
    fi
    echo $$ > "${VLLM_RESULT_DIR}/server.pid"
    exec "$@" > "${VLLM_RESULT_DIR}/server.log" 2>&1
  ' bash \
  /opt/venv/bin/python -m vllm.entrypoints.cli.main serve \
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
  --scheduler-cls redline_vllm.scheduler.RedlineQuantumScheduler

for _ in $(seq 1 180); do
  if curl -fsS --max-time 2 "${health_url}" >/dev/null 2>&1; then
    echo "Endpoint is healthy at ${endpoint}"
    echo "Server artifacts: ${result_dir}"
    exit 0
  fi
  if ! docker container inspect -f '{{.State.Running}}' "${container}" \
    | grep -qx true; then
    echo "Container stopped while the endpoint was starting." >&2
    exit 1
  fi
  sleep 5
done

echo "Endpoint did not become ready within 15 minutes." >&2
docker exec "${container}" tail -200 "${result_dir}/server.log" >&2 || true
exit 1
