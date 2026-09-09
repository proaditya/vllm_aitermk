# libAiterMK coding chat

Pi is the primary agentic-chat interface. The repository integration lives
under `tools/libaitermk/pi/` and provides an interactive terminal UI, saved
sessions, context compaction, cancellation, and built-in coding tools.

`tools/libaitermk/coding_chat.py` remains a smaller diagnostic client. It is
not the normal interactive entry point.

## Container prerequisites

The prepared GPU container must provide:

- the repository's Python environment at `.venv/`;
- Node.js 22.19 or newer, including `npm`; and
- access to the source-model and compiled-checkpoint directories.

Node.js must be installed inside the same container that runs the endpoint and
Pi. The current setup is validated with Node.js 22.23.2 and npm 10.9.8. Confirm
the runtime before installing Pi:

```bash
node --version
npm --version
```

## Server requirements

GPT-OSS tool calls require the server flags:

```bash
--enable-auto-tool-choice \
--tool-call-parser openai
```

Run the launcher from inside the already configured GPU container. It starts
the endpoint in the current container and never invokes Docker or selects a
container by name. The two external model directories are required inputs:

```bash
cd /path/to/vllm-checkout
tools/libaitermk/start_endpoint.sh \
  --model /path/to/source-model \
  --compiled-checkpoint /path/to/compiled-checkpoint
```

The Python executable defaults to `.venv/bin/python` relative to the repository
root. Server logs and Redline evidence default to
`results/libaitermk-coding-chat/endpoint/`, also relative to the repository
root. TorchInductor and Triton caches default below
`results/libaitermk-coding-chat/cache/`. Override `VLLM_SERVER_PYTHON`,
`VLLM_RESULT_DIR`, or `VLLM_CACHE_DIR` only when needed.

The endpoint defaults to a 65,536-token context window. Override it at server
startup with `--max-model-len`; the GPT-OSS model and Redline runtime support up
to 131,072 positions. This is a startup-time setting, so changing it requires
restarting the endpoint.

The launcher selects the default B1/K8 BF16 provider. K8 is the maximum native
decode quantum: eligible calls may emit 1, 2, 4, or 8 tokens. For Harmony
requests with multiple effective stop-token IDs, the native controller returns
a bounded slice with EOS comparison disabled and vLLM scans that slice in
order, publishing through the first stop token and discarding the tail.

The launcher also enables vLLM's AITER kernels for stock GPT-OSS prefill. This
does not replace the Redline decode provider: vLLM still owns prefill and
Redline still owns eligible decode. On the validated MI355X setup, a controlled
two-step Pi tool turn reduced aggregate warmed server prefill time from 617 ms
with the Triton MXFP4 MoE backend to 202 ms with `AITER_MXFP4_BF16` for the
same 346 newly computed prompt tokens. The first request for a new AITER
attention shape can still include one-time JIT latency; subsequent tool steps
in the validation run reached 49-59 ms TTFT. Prefix-cache hits remain important
because every new tool result or user turn still has to be ingested before
decoding can begin.

Packaged deployments load the installed `redline_vllm` adapter. For local
adapter development, set `REDLINE_VLLM_ADAPTER_PATH` to the directory containing
the `redline_vllm` package; the helper prepends it to the existing `PYTHONPATH`.

## Pi interactive coding chat

Pi is pinned to `@earendil-works/pi-coding-agent` 0.84.4. The live decode-speed
extension is installed from a pinned `decode-speed-meter` GitHub revision. Both
require Node.js 22.19 or newer. Install the pinned dependencies once:

```bash
cd tools/libaitermk/pi
npm ci --omit=optional --ignore-scripts
```

`node_modules` is local installation state and is not committed. A replacement
container therefore needs Node.js installed and this `npm ci` step repeated.

With optional clipboard support omitted, the installed Pi dependency closure
is approximately 135 MB. The complete portable Node.js 22.23.2 distribution
used in the current local container is approximately 204 MB. A system Node.js
installation may have a different footprint. Pi is MIT licensed; the local
launcher and extension use the repository's Apache-2.0 license.

From the repository root inside the same container, start Pi against the local
endpoint and choose the workspace it may inspect and modify:

```bash
tools/libaitermk/start_pi_chat.sh "$PWD" --stats
```

On the first interactive launch, Pi asks whether to trust the workspace. Pi
may also download `rg` and `fd` into the repository-local agent directory.

The Pi launcher accepts the familiar client options `--url`, `--api-key`,
`--max-model-len`, `--max-tokens`, `--system-prompt`, `-q`/`--quick`, and
`--stats`. `--max-model-len` tells Pi the endpoint context window and defaults
to 65,536; it does not change an already-running server. Other options are
passed directly to Pi. For example, use `--no-session` for an ephemeral
conversation or `--continue` to continue the most recent one.

Pi queries the endpoint's standard `/v1/models` API and uses the first served
model ID. That ID is required in OpenAI chat requests, but it does not need to
be supplied manually for this single-model endpoint.

The libAiterMK Pi extension registers the endpoint as the
`libaitermk-vllm` provider and enables Pi's built-in `read`, `bash`, `edit`,
`write`, `grep`, `find`, and `ls` tools. File-tool paths are confined to the
workspace, including checks for symlink escapes. Writes and model-requested
shell commands default to interactive approval. Set either policy to `allow`
only in an appropriately isolated workspace:

```bash
--write-policy allow --shell-policy allow
```

For unattended operation, one CLI switch enables both policies:

```bash
tools/libaitermk/start_pi_chat.sh "$PWD" \
  --dangerously-skip-permissions \
  --stats
```

The policy is fixed when Pi starts. Exit and relaunch Pi when changing it.
The launcher prints the active write, shell, and web-access policies before
the interactive UI starts.

Web access is optional. Enable the pinned `pi-agent-web-access` extension with:

```bash
tools/libaitermk/start_pi_chat.sh "$PWD" --web-access
```

This adds `web_search` and `fetch_content`. Direct URL fetching requires no
search API key. Search uses `EXA_API_KEY` or `BRAVE_API_KEY` when supplied and
can otherwise use Exa's zero-configuration MCP fallback. Combine web access
with unattended operation only in a controlled container:

```bash
tools/libaitermk/start_pi_chat.sh "$PWD" \
  --dangerously-skip-permissions \
  --web-access \
  --stats
```

Approved shell commands are not filesystem-confined. The workspace boundary
is enforced for Pi's file tools; retain `--shell-policy ask` unless the
container or workspace provides an additional sandbox.

With `--stats`, Pi also loads the pinned `pi-token-speed` extension. It displays
a live terminal decode-speed graph with estimated Now/Mean/Peak rates. Use
`/tps` inside Pi to configure or toggle that display. npm downloads the pinned
GitHub source archive; its source is not copied into this repository.

The libAiterMK footer continues to update while the response streams, and the
completed model step displays exact endpoint token usage, TTFT, TPOT, decode
throughput, and end-to-end throughput. `/libaitermk-metrics` restores the most
recent completed metrics display. Metrics are appended as JSON lines to:

```text
results/libaitermk-pi/metrics.jsonl
```

Pi sessions and configuration also default below
`vllm_aitermk/results/libaitermk-pi/`, so the integration does not create
agent state directly in the user's home directory. Override
`PI_CODING_AGENT_DIR`, `PI_CODING_AGENT_SESSION_DIR`, or
`LIBAITERMK_PI_METRICS_FILE` when a different location is required.

The decode-speed graph estimates tokens from Pi transport deltas, which are not
tokenizer events and do not include provider timestamps. It is useful for live
shape and responsiveness, but the final libAiterMK prompt/output counts and
derived metrics use the endpoint's streamed usage record and remain the
authoritative measurements. Tool-enabled GPT-OSS chat can still use one-token
Redline quantums near output or page boundaries. Client-side TPS and SSE chunk
counts must not be treated as proof of the effective native quantum.

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

## Deployment inputs

The endpoint launcher's `--model` and `--compiled-checkpoint` arguments must
point to complete, container-visible directories. The launcher deliberately
has no machine-local fallback for either path. It passes the compiled
checkpoint into the provider's internal runtime environment.

The Pi launcher receives neither path. It only needs the endpoint URL and
discovers the served model ID through `/v1/models` before sending chat
requests.
