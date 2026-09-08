# libAiterMK coding chat

`tools/libaitermk/coding_chat.py` is a workspace-scoped coding client for a
running OpenAI-compatible vLLM endpoint. It gives a tool-capable model bounded
file operations and policy-controlled shell execution without changing the
standard `vllm chat` command.

The repository also includes an optional Pi coding-agent integration under
`tools/libaitermk/pi/`. Pi provides a richer interactive terminal UI, saved
sessions, context compaction, cancellation, and built-in coding tools. The
original Python client remains the smaller diagnostic and benchmark client.

## Server requirements

GPT-OSS tool calls require the server flags:

```bash
--enable-auto-tool-choice \
--tool-call-parser openai
```

For libAiterMK, start the server with the Redline worker, scheduler, provider,
KV-cache profile, and compiled-checkpoint environment already configured. The
repository helper can start an existing configured container:

```bash
LIBAITERMK_CONTAINER=ssharma4-libaitermk \
VLLM_CHAT_URL=http://127.0.0.1:8005/v1 \
tools/libaitermk/start_endpoint.sh
```

The helper does not create or configure a Docker container. It starts the
container if necessary and launches `vllm serve` inside it. By default, server
logs and Redline evidence are written below
`vllm_aitermk/results/libaitermk-coding-chat/endpoint/` through the repository
mount at `/workspace/vllm_aitermk`. Override `VLLM_RESULT_DIR` only when the
container uses a different repository mount.

The endpoint defaults to a 65,536-token context window. Override it at server
startup with `VLLM_MAX_MODEL_LEN`; the GPT-OSS model and Redline runtime support
up to 131,072 positions. This is a startup-time setting, so changing it requires
restarting the endpoint.

Packaged deployments load the installed `redline_vllm` adapter. For local
adapter development, set `REDLINE_VLLM_ADAPTER_PATH` to a container-visible
directory containing the `redline_vllm` package; the helper prepends it to the
container's existing `PYTHONPATH`.

The compiled checkpoint defaults to `/compiled-checkpoint` inside the
container. Override it with `REDLINE_COMPILED_MODEL_PATH` when the deployment
mount uses a different container-visible path.

## Start coding chat

Use the vLLM fork as the workspace:

```bash
LIBAITERMK_CONTAINER=ssharma4-libaitermk \
VLLM_CHAT_URL=http://127.0.0.1:8005/v1 \
tools/libaitermk/start_coding_chat.sh \
  /home/ssharma4/vllm_aitermk \
  --model-name gpt-oss-120b \
  --write-policy ask \
  --shell-policy ask \
  --stats
```

Or call the Python client when the endpoint is already running:

```bash
.venv/bin/python tools/libaitermk/coding_chat.py \
  --url http://127.0.0.1:8005/v1 \
  --model-name gpt-oss-120b \
  --workspace /home/ssharma4/vllm_aitermk \
  --write-policy ask \
  --shell-policy ask
```

The familiar vLLM chat options are `--url`, `--model-name`, `--api-key`,
`--system-prompt`, `-q`/`--quick`, and `--stats`.

With `--stats`, every model step reports TTFT, end-to-end output TPS, and TPOT.
TPOT excludes TTFT and is calculated over tokens after the first generated
token, so it is the appropriate decode-latency metric. The displayed `TPS`
continues to include TTFT.

## Pi interactive coding chat

Pi is pinned to `@earendil-works/pi-coding-agent` 0.84.4. It requires Node.js
22.19 or newer. Install the pinned dependency once:

```bash
cd tools/libaitermk/pi
npm ci --omit=optional --ignore-scripts
```

The current Docker image does not include Node.js by default. Install Node.js
22.19 or newer in the image or container before running `npm ci`. `node_modules`
is local installation state and is not committed.

With optional clipboard support omitted, the installed Pi dependency closure
is approximately 135 MB. The complete portable Node.js 22.23.2 distribution
used in the current local container is approximately 204 MB. A system Node.js
installation may have a different footprint. Pi is MIT licensed; the local
launcher and extension use the repository's Apache-2.0 license.

Run Pi inside the configured container, where the endpoint is available on
port 8000 and the repository is mounted at `/workspace/vllm_aitermk`:

```bash
docker exec -it ssharma4-libaitermk \
  /workspace/vllm_aitermk/tools/libaitermk/start_pi_chat.sh \
  /workspace/vllm_aitermk/results/coding-chat-workspace \
  --url http://127.0.0.1:8000/v1 \
  --model-name gpt-oss-120b \
  --max-model-len 65536 \
  --write-policy ask \
  --shell-policy ask \
  --max-tokens 4096 \
  --stats
```

The current `ssharma4-libaitermk` container has Node.js 22.23.2 installed at
`/opt/libaitermk-node`, with `node`, `npm`, and `npx` available through
`/usr/local/bin`. This is a change to that container, not to its source image;
a newly created container must install Node.js again. On the first interactive
launch, Pi asks whether to trust the workspace. Pi may also download `rg` and
`fd` into the repository-local agent directory.

The wrapper accepts the familiar vLLM-style options `--url`, `--model-name`,
`--api-key`, `--max-model-len`, `--max-tokens`, `--system-prompt`,
`-q`/`--quick`, and `--stats`. `--max-model-len` tells Pi the endpoint context
window and defaults to 65,536; it does not change an already-running server.
Other options are passed directly to Pi. For example, use `--no-session` for
an ephemeral conversation or `--continue` to continue the most recent one.

The libAiterMK Pi extension registers the endpoint as the
`libaitermk-vllm` provider and enables Pi's built-in `read`, `bash`, `edit`,
`write`, `grep`, `find`, and `ls` tools. File-tool paths are confined to the
workspace, including checks for symlink escapes. Writes and model-requested
shell commands default to interactive approval. Set either policy to `allow`
only in an appropriately isolated workspace:

```bash
--write-policy allow --shell-policy allow
```

Approved shell commands are not filesystem-confined. The workspace boundary
is enforced for Pi's file tools; retain `--shell-policy ask` unless the
container or workspace provides an additional sandbox.

With `--stats`, the footer updates while the response streams and the completed
model step displays exact endpoint token usage, TTFT, TPOT, decode throughput,
and end-to-end throughput. `/libaitermk-metrics` restores the most recent
completed metrics display. Metrics are appended as JSON lines to:

```text
vllm_aitermk/results/libaitermk-pi/metrics.jsonl
```

Pi sessions and configuration also default below
`vllm_aitermk/results/libaitermk-pi/`, so the integration does not create
agent state directly in the user's home directory. Override
`PI_CODING_AGENT_DIR`, `PI_CODING_AGENT_SESSION_DIR`, or
`LIBAITERMK_PI_METRICS_FILE` when a different location is required.

Pi's live stream counter is a count of received stream delta events, not an
exact tokenizer count. The final prompt/output counts and derived metrics use
the endpoint's streamed usage record and are exact. Tool-enabled GPT-OSS chat
can still use one-token Redline quantums because of the Harmony multi-stop
behavior; client-side TPS must not be treated as proof that K8 executed.

The endpoint has a vLLM-owned KV cache for each active request, so decode does
not recompute the entire prompt for every generated token. B=1 direct-KV
serving also enables vLLM prefix caching, allowing later chat turns and agent
steps to reuse unchanged full prompt blocks across requests. Cache reuse is
block-aligned and opportunistic; new suffix tokens still require prefill.

Prefix caching remains disabled for Redline B2/B3/B4 serving and FP8 reuse
diagnostics. Same-step KV block-copy transactions also remain outside the
bounded-quantum contract and fail closed before native entry.

## Security boundary

File tools reject absolute paths, parent traversal, and symlink escapes. Writes
are atomic, and focused edits require an exact match count. Both writes and
shell commands default to interactive approval.

The shell starts in the workspace but is not filesystem-confined. Keep
`--shell-policy ask` unless the environment itself provides an appropriate
sandbox.

Available tools are `list_files`, `read_file`, `search_files`, `write_file`,
`replace_text`, `make_directory`, and `run_command`. Deletion is intentionally
not exposed in the first version.

## Current local checkpoints

The current local endpoint loads the base model from:

```text
/models/openai/gpt-oss-120b
```

The Redline ABI-4 checkpoint is expected at `/compiled-checkpoint` inside the
container. The qualified artifact is retained on Fleet in run
`job-bcedc612` at:

```text
team-admin-1/compiled/gpt-oss-120b-p2-gfx950-tp1-abi4-b9e3-v3
```

For local Docker use, mount a complete copy at `/compiled-checkpoint`; a broken
or empty compatibility mount will make the engine fail during startup. These
paths belong to the deployment. The coding client only connects to the endpoint
and does not load either checkpoint directly.
