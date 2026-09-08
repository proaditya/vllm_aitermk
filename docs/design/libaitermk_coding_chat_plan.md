# libAiterMK Coding Chat Integration Plan

## Goal

Add a self-contained coding-chat utility under `tools/libaitermk/`. The utility
must use the running OpenAI-compatible vLLM endpoint, preserve the familiar
`vllm chat` argument names, and provide workspace-scoped file inspection, file
creation, focused edits, and guarded command execution. It must not modify the
existing vLLM chat implementation.

The model does not receive direct filesystem access. It produces structured
tool calls, and the CLI validates and executes those calls on the user's
behalf. File tools remain confined to the workspace selected by the user.

## Current Prototype Inventory

The working prototype currently lives outside the vLLM repository under
`/home/ssharma4/libaitermk-vllm-local-gpu5/`:

| File | Purpose | Commit disposition |
| --- | --- | --- |
| `coding-chat/chat_agent.py` | OpenAI-compatible agent loop and workspace tools | Move and simplify as `tools/libaitermk/coding_chat.py` |
| `coding-chat/tests/test_chat_agent.py` | Unit tests for file boundaries, writes, edits, searches, and shell policy | Port into the vLLM test tree |
| `coding-chat/README.md` | Prototype usage and security notes | Replace with vLLM documentation and a skill |
| `start_coding_chat.sh` | Starts the local endpoint and then the prototype client | Replace with a parameterized repository script if still needed |
| `start_endpoint.sh` | Starts the local GPU-5 Redline endpoint | Replace with a parameterized repository script; do not commit machine-specific defaults |

The prototype has already completed a live create/read/edit/read cycle through
GPT-OSS tool calls, and its eight workspace-tool unit tests pass.

## Proposed User Interface

Keep the utility independent from vLLM's existing `openai.py`. It should live
inside the vLLM fork but run as a separate script whose argument names mirror
`vllm chat`.

Example:

```bash
.venv/bin/python tools/libaitermk/coding_chat.py \
  --url http://127.0.0.1:8005/v1 \
  --model-name gpt-oss-120b \
  --workspace /home/ssharma4/vllm_aitermk \
  --write-policy ask \
  --shell-policy ask \
  --stats
```

One-shot mode should continue to use the existing vLLM spelling:

```bash
.venv/bin/python tools/libaitermk/coding_chat.py \
  --url http://127.0.0.1:8005/v1 \
  --model-name gpt-oss-120b \
  --workspace /home/ssharma4/vllm_aitermk \
  --write-policy allow \
  --shell-policy deny \
  --quick "Inspect the repository and update the requested file."
```

### CLI compatibility

Reuse the existing `vllm chat` flags and semantics wherever there is an
equivalent prototype option:

| vLLM chat flag | Prototype flag | Planned action |
| --- | --- | --- |
| `--url` | `--url` | Keep the vLLM name and default |
| `--model-name` | `--model` | Rename the prototype option to `--model-name` |
| `--api-key` | Not implemented | Reuse vLLM's argument and environment fallback |
| `--system-prompt` | Built-in fixed prompt | Append the user-provided prompt to the coding-agent system instructions |
| `-q`, `--quick` | `--prompt` | Use the vLLM name and remove `--prompt` |
| `--stats` | `--stats` | Preserve the vLLM name and report model-call timing consistently |

Keep the following prototype names because they have no existing equivalent in
`vllm chat`:

- `--workspace`
- `--write-policy {ask,allow,deny}`
- `--shell-policy {ask,allow,deny}`
- `--max-steps`
- `--max-tokens`
- `--timeout`
- `--history-char-budget`
- `--max-tool-output-chars`
- `--prompt-file`

`--quick` and `--prompt-file` must remain mutually exclusive. The existing
interactive prompt remains the default when neither is supplied.

## Proposed Repository Files

Keep the integration self-contained so it is easy to maintain and does not
change upstream vLLM CLI behavior:

| Repository path | Change |
| --- | --- |
| `tools/libaitermk/coding_chat.py` | Add the complete standalone agent loop, OpenAI tool schemas, workspace validation, approval policies, and tool execution |
| `tools/libaitermk/start_endpoint.sh` | Add an optional parameterized Redline/vLLM launcher without user-, GPU-, port-, or checkpoint-specific defaults |
| `tools/libaitermk/start_coding_chat.sh` | Add an optional convenience wrapper around the endpoint and Python utility |
| `tests/tools/test_libaitermk_coding_chat.py` | Add focused unit tests for observable tool and policy behavior |
| `docs/cli/libaitermk_coding_chat.md` | Document server requirements, security boundaries, and examples |
| `.agents/skills/libaitermk-coding-chat/SKILL.md` | Add the repository skill for using and diagnosing the coding chat with libAiterMK |
| `.agents/skills/libaitermk-coding-chat/agents/openai.yaml` | Add concise skill UI metadata |
| `.claude/skills/libaitermk-coding-chat` | Add a compatibility symlink to the canonical `.agents` skill directory |
| `results/.gitignore` | Keep the repository-owned results directory while ignoring generated artifacts |

All vLLM-side logs, evidence, generated examples, and test outputs must stay
under `vllm_aitermk/results/libaitermk-coding-chat/`. Redline-owned build and
runtime results must stay under `libAiterMK/results/`; neither workflow
may create new project directories directly under `/home/ssharma4`.

The implementation should use the same OpenAI Python client pattern as
`vllm chat`, but it should not import private helpers or add branches to
`vllm/entrypoints/cli/openai.py`. Endpoint discovery, `--api-key`,
`--model-name`, defaults, and error behavior should still align with
`vllm chat`.

## Tool Surface

Start with the smallest useful tool set:

- `list_files`: list a bounded portion of the workspace tree.
- `read_file`: read a bounded UTF-8 line range.
- `search_files`: search bounded UTF-8 files by literal text or regular expression.
- `write_file`: create a file or explicitly overwrite an existing file.
- `replace_text`: make a focused edit only when the expected match count is exact.
- `make_directory`: create a workspace directory.
- `run_command`: run a command from the workspace under the configured shell policy.

Do not add a deletion tool in the first committed version. A later deletion or
move operation needs an explicit recoverability and approval design.

## Safety Requirements

1. Require an explicit existing `--workspace` before enabling tools.
2. Reject absolute paths and paths that resolve outside the workspace.
3. Reject symlink-based workspace escapes.
4. Use atomic file replacement for writes and edits.
5. Require an exact replacement count before modifying an existing file.
6. Keep `ask` as the default for writes and shell commands.
7. State clearly that `--shell-policy allow` is not filesystem-confined like
   the file tools.
8. Cap file size, tool output, search results, command duration, and agent steps.
9. Preserve complete assistant/tool message groups when trimming history.
10. Dispatch only tool names present in an explicit allowlist.

## Comments and Documentation Standard

Follow the repository's `AGENTS.md` guidance: prefer clear code and avoid
comments that merely repeat an operation. Add concise comments or Google-style
docstrings where they explain a non-obvious contract, especially:

- the boundary between model-generated tool calls and host-side execution;
- workspace containment and symlink handling;
- atomic-write behavior;
- approval-policy behavior;
- OpenAI tool-call message ordering;
- why context trimming must preserve complete tool-call groups;
- the distinction between file-tool confinement and unrestricted shell access.

Public functions and classes that carry these contracts should have brief
Google-style docstrings with `Args:`, `Returns:`, and `Raises:` sections when
applicable. Avoid line-by-line narration.

## GPT-OSS and Server Requirements

The client sends OpenAI-compatible chat requests containing tool definitions
and `tool_choice="auto"`. A GPT-OSS endpoint must be started with Harmony tool
parsing enabled:

```bash
vllm serve /models/openai/gpt-oss-120b \
  --served-model-name gpt-oss-120b \
  --enable-auto-tool-choice \
  --tool-call-parser openai
```

For the current libAiterMK environment, the remaining Redline worker,
scheduler, KV-cache, and provider arguments are supplied by the deployment
launcher. They must not be hard-coded into the generic vLLM chat client.

Tool-enabled GPT-OSS requests can carry multiple Harmony stop tokens. They may
therefore use one-token decode quantums under the current Redline bounded-mode
policy. The coding utility is a functional integration; it must not claim the
strict K8 performance configuration used by the dedicated benchmark client.

## Current Model and Compiled Checkpoint

The current local endpoint uses two distinct artifacts:

### Base GPT-OSS model weights

```text
Host:      /models/openai/gpt-oss-120b
Container: /models/openai/gpt-oss-120b
```

The `/models` directory is mounted read-only, and vLLM loads this Hugging
Face-format checkpoint as the model passed to `vllm serve`.

### Redline compiled megakernel checkpoint

```text
Host:      /models/openai/gpt-oss-120b-redline-persistent-decoder-gfx950-b1k8-bf16-kv-abi4-v1
Container: /compiled-checkpoint
Environment: REDLINE_COMPILED_MODEL_PATH=/compiled-checkpoint
```

The compiled checkpoint mount is read-only. The qualified ABI-4 checkpoint is
retained on Fleet in run `job-bcedc612` at:

```text
team-admin-1/compiled/gpt-oss-120b-p2-gfx950-tp1-abi4-b9e3-v3
```

The local Docker bind source must be a complete copy of that checkpoint. The
legacy `/home/ssharma4/gsm8k-local-abi4-v1/checkpoint` compatibility symlink is
not itself a checkpoint and must not be used when its target is absent. The
qualified artifact metadata reports:

```text
ABI version:            4
Tensor catalog SHA-256: e0659750ff285a5f9bc20fd73afef696f7e733cedf2ffb24443ed01a81ccbff6
Weight pack plan:       gpt_oss_gfx950_v1_checkpoint_abi4_pack_plan_v1
Pack plan SHA-256:      7a97e621cb7f2bc8aff699b5e42476e53577b082054244deed4ad29cdd109311
Provider ID:            redline.gpt-oss-120b.gfx950.b1k8.bf16-kv.v1
```

The client does not select either checkpoint directly. It connects to the
already-running endpoint; the server process selects and loads these artifacts.

## Skill Plan and Fork History

Add a concise `libaitermk-coding-chat` skill. It should activate when a user
asks to start, use, or diagnose the workspace-enabled GPT-OSS coding chat. The
skill should cover:

- checking endpoint health before launching the client;
- selecting the intended workspace;
- choosing `ask`, `allow`, or `deny` policies;
- confirming that GPT-OSS Harmony tool parsing is enabled;
- distinguishing ordinary `vllm chat` from workspace-enabled coding mode;
- preserving the rule that the client executes tools and the model only
  proposes them;
- diagnosing connection, tool-parser, permission, and path-containment errors.

Do not put the full implementation or generic coding advice in `SKILL.md`.
Keep the skill focused on the non-obvious operational contract.

Commit-history inspection found no skill introduced specifically by this fork:

- The current qualified branch has one fork-only commit,
  `6ced9b81d6862057b46846c706f9a72afdf3c93b`, which changes only
  `vllm/v1/worker/gpu_worker.py` and its test.
- The integration branch commit
  `36eb263ba552575694cdc9594a17fafe0c087613` has the same two-file scope.
- `.claude/skills/ci-fails-buildkite/SKILL.md` came from upstream commit
  `ca7e1f2c4`.
- The `kernel-microbenchmark` and `debug-ima` skills visible on `origin/main`
  came from upstream commits `5e379a361` and `d125b540b`.
- All three skill commits are ancestors of upstream vLLM `main`; none are
  fork-only additions.

The new `libaitermk-coding-chat` skill will therefore be the first skill added
specifically for this fork. Use the current upstream convention of a canonical
`.agents/skills/` directory with a `.claude/skills/` compatibility symlink.

## Test Plan

### Unit tests

- Accept paths inside the workspace.
- Reject absolute paths, `..` escapes, and symlink escapes.
- Create a new file and require explicit overwrite for an existing file.
- Apply an exact focused edit and reject an unexpected match count.
- Bound file reads, listings, searches, and returned output.
- Enforce `ask`, `allow`, and `deny` policies.
- Restrict dispatch to the declared tool allowlist.
- Preserve complete tool-call groups during history trimming.
- Verify vLLM-compatible argument names and mutual exclusions.
- Verify that ordinary `vllm chat` behavior is unchanged without
  `--workspace`.

### Endpoint tests

With a tool-enabled GPT-OSS server:

1. Create a small file through `write_file`.
2. Read it back through `read_file`.
3. Modify one exact block through `replace_text`.
4. Read it again and verify the change.
5. Run a harmless validation command under shell approval.
6. Confirm a denied write or shell operation is reported to the model without
   executing it.
7. Confirm ordinary non-tool `vllm chat --quick` still succeeds.

### Repository checks

Run through the repository environment rather than system Python:

```bash
.venv/bin/python -m pytest tests/tools/test_libaitermk_coding_chat.py -v
pre-commit run ruff-check --files \
  tools/libaitermk/coding_chat.py \
  tests/tools/test_libaitermk_coding_chat.py
```

Validate the new skill with the skill-creator validator before committing:

```bash
.venv/bin/python "${CODEX_HOME}/skills/.system/skill-creator/scripts/quick_validate.py" \
  .agents/skills/libaitermk-coding-chat
```

## Implementation Order

1. Port the prototype into `tools/libaitermk/coding_chat.py` and simplify it
   around the OpenAI client and vLLM-compatible argument names.
2. Parameterize and move the optional local launchers under `tools/libaitermk/`.
3. Add focused unit tests and run them through the repository `.venv`.
4. Add user documentation under `docs/cli/`.
5. Add and validate the `libaitermk-coding-chat` skill.
6. Run the live GPT-OSS create/read/edit/read test against the Redline endpoint.
7. Review the final diff for machine-specific paths, redundant comments, and
   accidental changes outside the planned files before committing.

## Acceptance Criteria

- Existing `vllm chat` code and behavior remain unchanged.
- The standalone utility uses the same familiar argument names as `vllm chat`.
- Coding mode uses vLLM's existing flag names wherever equivalents exist.
- New coding-specific flags retain their current prototype names.
- The model can inspect, create, and make focused edits inside the workspace.
- File tools cannot escape the workspace.
- Writes and shell commands default to interactive approval.
- The code contains concise documentation for security and protocol contracts.
- The repository skill is valid and contains no machine-specific secrets or
  artifact paths.
- Unit tests, CLI regression tests, skill validation, and a live GPT-OSS tool
  cycle all pass.
