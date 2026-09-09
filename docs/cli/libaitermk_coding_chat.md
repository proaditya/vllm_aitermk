# libAiterMK coding chat

This integration runs a standard vLLM OpenAI-compatible server with the
libAiterMK Redline provider for decode and Pi as the coding-agent client.

## Prerequisites

Use a prepared ROCm container with:

- this vLLM checkout and its `.venv` environment;
- the `libaitermk` package installed;
- Node.js 22.19 or newer with npm; and
- container-visible source-model and compiled-checkpoint directories.

Install the pinned Pi dependencies once:

```bash
cd tools/libaitermk/pi
npm ci --omit=optional --ignore-scripts
cd ../../..
```

## Start the endpoint

From the repository root inside the GPU container:

```bash
tools/libaitermk/start_endpoint.sh \
  --model /path/to/gpt-oss-120b \
  --compiled-checkpoint /path/to/megakernel-checkpoint
```

The launcher defaults to port 8000, a 65,536-token context window, BF16 B1/K8
Redline decode, AITER prefill, prefix caching, and GPT-OSS tool-call parsing.
Use `--max-model-len` to change the context window at server startup. Run
`tools/libaitermk/start_endpoint.sh --help` for all server options.

Runtime logs, compiler caches, Pi sessions, and metrics are written below the
repository's ignored `.cache/libaitermk/` directory. `VLLM_RESULT_DIR`,
`VLLM_CACHE_DIR`, `PI_CODING_AGENT_DIR`, and
`LIBAITERMK_PI_METRICS_FILE` can override those locations.

Check the endpoint with:

```bash
curl -fsS http://127.0.0.1:8000/health
```

The launcher prints the run directory. Stop that server with
`kill "$(cat <run-directory>/server.pid)"`.

## Start Pi

The normal interactive command is:

```bash
tools/libaitermk/start_pi_chat.sh "$PWD" --stats
```

The first positional argument is the workspace exposed to Pi's file tools.
The launcher checks endpoint health, discovers the served model through
`/v1/models`, and connects to `http://127.0.0.1:8000/v1` by default. It does
not load model or checkpoint files.

Writes and shell commands require approval by default. For an isolated,
unattended container session, enable both explicitly:

```bash
tools/libaitermk/start_pi_chat.sh "$PWD" \
  --dangerously-skip-permissions \
  --stats
```

Optional read-only web tools are enabled separately:

```bash
tools/libaitermk/start_pi_chat.sh "$PWD" \
  --dangerously-skip-permissions \
  --web-access \
  --stats
```

`--web-access` adds `web_search` and `fetch_content`. URL fetching needs no API
key. Search can use `EXA_API_KEY` or `BRAVE_API_KEY`, with Exa's zero-config
fallback available when no key is set. The web tools do not modify remote
sites, but unrestricted shell access can perform arbitrary network operations.

Useful client options include `--url`, `--api-key`, `--max-model-len`,
`--max-tokens`, `--system-prompt`, `--continue`, `--no-session`, and `--stats`.
Changing permission or extension options requires restarting Pi, not the
vLLM endpoint.

## Execution behavior

vLLM owns HTTP serving, tokenization, prefill, scheduling, prefix caching,
stop handling, and response publication. Redline handles eligible decode calls.
The B1/K8 provider may return 1, 2, 4, or 8 tokens per native call.

When a request has multiple token-stop IDs, Redline disables its single-ID EOS
comparison and returns the bounded token slice. Stock vLLM checks those tokens
in order, publishes through the first stop token, and drops the remaining
suffix. String stops, repetition detection, resumable requests, and output or
page boundaries can reduce an individual call to K1.

`--stats` loads the pinned live speed-meter extension and records authoritative
endpoint-usage metrics in `.cache/libaitermk/pi/metrics.jsonl`. Short responses
produce noisy TPOT; use a sufficiently long output when measuring decode speed.
