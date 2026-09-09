# libAiterMK coding-chat design

## Goal

Provide one repository-owned path for using Pi as an agentic coding client on
top of a local OpenAI-compatible vLLM endpoint backed by libAiterMK. Both the
endpoint launcher and Pi launcher run inside the same prepared GPU environment.
Neither launcher creates, starts, selects, or otherwise manages a container.

The prepared container is responsible for providing the repository Python
environment and Node.js 22.19 or newer with npm. Pi is not vendored into the
container image: its pinned dependencies are installed under
`tools/libaitermk/pi/node_modules/` with `npm ci --omit=optional
--ignore-scripts`.

## User flow

From the vLLM repository root:

```bash
tools/libaitermk/start_endpoint.sh \
  --model /path/to/source-model \
  --compiled-checkpoint /path/to/compiled-checkpoint

tools/libaitermk/start_pi_chat.sh "$PWD" --stats
```

The endpoint and Pi are separate processes with separate responsibilities.
The endpoint and Pi context-window defaults are both 65,536 tokens.

## Component ownership

| Component | Responsibility |
| --- | --- |
| `tools/libaitermk/start_endpoint.sh` | Start the local vLLM/libAiterMK endpoint and retain logs and evidence. |
| `tools/libaitermk/start_pi_chat.sh` | Validate the endpoint and launch Pi for an explicit workspace. |
| `tools/libaitermk/pi/libaitermk.ts` | Register the OpenAI-compatible endpoint, policies, and metrics with Pi. |
| `tools/libaitermk/coding_chat.py` | Retained lightweight diagnostic client; not the primary interactive path. |
| `redline_vllm` | Select the libAiterMK worker, scheduler, runtime, and provider. |
| vLLM | Own HTTP serving, tokenization, prefill, scheduling, prefix caching, sampling, and responses. |
| Pi | Own the interactive agent loop, session UX, and coding-tool orchestration. |

## Path contract

The launchers contain no machine-specific absolute paths.

Required deployment inputs:

- Endpoint: `--model` with the complete source-model directory.
- Endpoint: `--compiled-checkpoint` with the complete compiled-checkpoint directory.
- Pi: the workspace passed as the first `start_pi_chat.sh` argument.

Repository-relative defaults:

- Python: `.venv/bin/python`
- Endpoint results: `results/libaitermk-coding-chat/endpoint/`
- Compiler caches: `results/libaitermk-coding-chat/cache/`
- Pi installation: `tools/libaitermk/pi/node_modules/`
- Pi state: `results/libaitermk-pi/agent/`
- Pi sessions: `results/libaitermk-pi/agent/sessions/`
- Pi metrics: `results/libaitermk-pi/metrics.jsonl`

The corresponding environment overrides are `VLLM_SERVER_PYTHON`,
`VLLM_RESULT_DIR`, `VLLM_CACHE_DIR`, `REDLINE_VLLM_ADAPTER_PATH`,
`PI_CODING_AGENT_DIR`, `PI_CODING_AGENT_SESSION_DIR`, and
`LIBAITERMK_PI_METRICS_FILE`.

## Endpoint contract

`start_endpoint.sh` owns the complete B1/K8 BF16 activation envelope:

- `VLLM_PLUGINS=redline_megakernel`
- `REDLINE_VLLM_ENABLE=1`
- `REDLINE_VLLM_MODE=direct`
- `REDLINE_VLLM_RUNTIME_API=c_v1`
- `VLLM_USE_V2_MODEL_RUNNER=0`
- `REDLINE_VLLM_ENABLE_QUANTUM_K8=1`
- BF16 KV cache with 64-token physical blocks
- one sequence, eager execution, synchronous scheduling
- chunked prefill and the hybrid KV manager disabled
- B1 prefix caching enabled
- the Redline worker and scheduler selected explicitly
- GPT-OSS Harmony tool parsing enabled

The endpoint defaults to `127.0.0.1:8000`, model name derived from the source
model directory, and a 65,536-token context. These are portable behavioral
defaults, not deployment-directory assumptions.

Each endpoint launch creates a unique repository-local result directory. It
never overwrites a prior ABI evidence file or server log.

## Pi contract

Pi is the primary agentic interface. `start_pi_chat.sh` requires an explicit,
existing workspace and defaults file writes and shell commands to interactive
approval. It verifies endpoint health, Node.js compatibility, the pinned Pi
installation, and the workspace before starting. It never starts or configures
the endpoint and never receives model or checkpoint filesystem paths.

The launcher discovers the API model ID from the endpoint's standard
`/v1/models` response. Pi needs that ID for the `model` field in OpenAI chat
requests, but the user does not need to pass it for this single-model endpoint.

Pi's built-in file tools are confined to the selected workspace, including
checks against parent traversal and symlink escapes. Approved shell commands
start in the workspace but are not filesystem-confined, so `ask` remains the
default shell policy.

The client uses the normal OpenAI-compatible chat API. The server remains a
standard vLLM endpoint; libAiterMK changes model execution, not the API shape.

## Prefix-cache behavior

The B1 direct-KV path allows vLLM to adopt immutable full prefix blocks. Later
agent steps can therefore reuse the unchanged system prompt, conversation
history, and tool definitions. Cache reuse is block-aligned and opportunistic;
new suffix tokens still require prefill.

Batch profiles and FP8 reuse diagnostics continue to reject prefix caching.

## Verification

Run from the repository environment:

```bash
.venv/bin/python -m pytest tests/tools/test_libaitermk_coding_chat.py -v
.venv/bin/python -m ruff check tests/tools/test_libaitermk_coding_chat.py
bash -n tools/libaitermk/start_endpoint.sh tools/libaitermk/start_pi_chat.sh
```

Device qualification requires a direct BF16 run with two sequential requests
sharing at least one complete 64-token prefix block. The second response must
report a nonzero cached-token count while the libAiterMK provider remains the
active decode path.
