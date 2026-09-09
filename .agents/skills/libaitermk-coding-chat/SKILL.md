---
name: libaitermk-coding-chat
description: Start, use, or diagnose the workspace-scoped GPT-OSS coding chat served by vLLM with the libAiterMK Redline integration.
---

# libAiterMK Coding Chat

Use `tools/libaitermk/coding_chat.py` for model-assisted file inspection and
edits. It is a client of an existing OpenAI-compatible endpoint; it does not
load model weights or execute Redline itself.

Before launching, check the endpoint health URL derived from `--url`. GPT-OSS
tool use requires `--enable-auto-tool-choice --tool-call-parser openai` on the
server. Use `tools/libaitermk/start_endpoint.sh` only with an existing container
that already has the Redline runtime, provider, and checkpoint configured.

The endpoint launcher enables vLLM's AITER kernels for stock GPT-OSS prefill
while Redline remains the eligible decode provider. Confirm the startup log
selects `AITER_MXFP4_BF16`; falling back to `TRITON` materially increases TTFT
after coding-tool results.

Select the narrowest useful `--workspace`. File tools are confined to it, but
shell commands are not. Keep `--write-policy ask --shell-policy ask` unless the
user explicitly requests unattended operation in a suitably isolated
environment. For such an unattended run, use the launcher CLI switch
`--dangerously-skip-permissions`; it sets both policies to `allow`. Add
`--web-access` when the agent needs the optional `web_search` and
`fetch_content` tools. Restart Pi after changing either option because
extensions and permission policies are selected at process startup.

Use the same familiar flags as `vllm chat`: `--url`, `--model-name`,
`--api-key`, `--system-prompt`, `--quick`, and `--stats`. See
`docs/cli/libaitermk_coding_chat.md` for commands and troubleshooting context.
`--stats` also loads the pinned `pi-token-speed` extension for an estimated live
decode graph; use the final libAiterMK endpoint-usage metrics for exact results.

When diagnosing failures, distinguish among endpoint connection errors,
missing GPT-OSS tool-parser flags, denied operations, and rejected paths. The
model proposes tool calls; the local client validates and executes them.
