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

Select the narrowest useful `--workspace`. File tools are confined to it, but
shell commands are not. Keep `--write-policy ask --shell-policy ask` unless the
user explicitly requests unattended operation in a suitably isolated
environment.

Use the same familiar flags as `vllm chat`: `--url`, `--model-name`,
`--api-key`, `--system-prompt`, `--quick`, and `--stats`. See
`docs/cli/libaitermk_coding_chat.md` for commands and troubleshooting context.

When diagnosing failures, distinguish among endpoint connection errors,
missing GPT-OSS tool-parser flags, denied operations, and rejected paths. The
model proposes tool calls; the local client validates and executes them.
