# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
"""Workspace-scoped coding chat for an OpenAI-compatible vLLM endpoint."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import regex as re

DEFAULT_SYSTEM_PROMPT = """You are a coding agent working in one explicitly
scoped workspace. Use the supplied tools to inspect files before changing them.
All file-tool paths must be relative to the workspace. Prefer replace_text for
focused edits and write_file for new files or complete rewrites. Run relevant
tests after edits when a safe command is available. Never claim a file was
read, changed, or tested unless its tool call succeeded. Ask before destructive
changes. In the final response, list changed files and verification performed.
"""


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "List files and directories under a workspace-relative path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path; use . for the root.",
                    },
                    "max_depth": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 8,
                        "default": 3,
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 1,
                    },
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Search UTF-8 files for a literal string or regular expression."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                    "glob": {"type": "string", "default": "*"},
                    "regex": {"type": "boolean", "default": False},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 50,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or rewrite a UTF-8 file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_text",
            "description": "Replace an exact text block in an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "expected_replacements": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 1,
                    },
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_directory",
            "description": "Create a directory inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command from the workspace after policy approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 300,
                        "default": 60,
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
]


class ToolError(RuntimeError):
    """An expected validation or execution failure from a local tool."""


@dataclass
class Policies:
    """Approval policies for operations that can mutate local state."""

    write: str
    shell: str

    def approve(self, category: str, detail: str) -> bool:
        """Return whether an operation is permitted by policy or user approval.

        Args:
            category: Either ``write`` or ``shell``.
            detail: Human-readable operation details shown for confirmation.

        Returns:
            True when the operation may proceed.
        """
        policy = self.write if category == "write" else self.shell
        if policy == "allow":
            return True
        if policy == "deny":
            return False
        print(f"\nApproval required for {category}:\n{detail}", file=sys.stderr)
        try:
            answer = input("Allow? [y/N] ").strip().lower()
        except EOFError:
            return False
        return answer in {"y", "yes"}


class WorkspaceTools:
    """Execute bounded file tools within one resolved workspace root."""

    ALLOWED_TOOLS = {
        "list_files",
        "read_file",
        "search_files",
        "write_file",
        "replace_text",
        "make_directory",
        "run_command",
    }

    def __init__(
        self,
        root: Path,
        policies: Policies,
        max_output_chars: int = 16000,
    ) -> None:
        """Initialize workspace tools.

        Args:
            root: Existing directory that bounds all file operations.
            policies: Write and shell approval policies.
            max_output_chars: Maximum text returned by one tool invocation.
        """
        self.root = root.resolve(strict=True)
        self.policies = policies
        self.max_output_chars = max_output_chars

    def _resolve(self, raw_path: str, *, must_exist: bool = False) -> Path:
        # Resolving before the containment check also rejects symlink escapes.
        path = Path(raw_path)
        if path.is_absolute():
            raise ToolError(
                "absolute paths are not allowed; use a workspace-relative path"
            )
        candidate = (self.root / path).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ToolError("path escapes the configured workspace") from exc
        if must_exist and not candidate.exists():
            raise ToolError(f"path does not exist: {raw_path}")
        return candidate

    def _relative(self, path: Path) -> str:
        relative = path.relative_to(self.root)
        return "." if not relative.parts else relative.as_posix()

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_output_chars:
            return text
        omitted = len(text) - self.max_output_chars
        return text[: self.max_output_chars] + (
            f"\n...[truncated {omitted} characters]"
        )

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """Write text atomically while retaining an existing file's mode."""
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        try:
            temporary.chmod(mode)
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def list_files(self, path: str, max_depth: int = 3) -> str:
        """List a bounded workspace subtree."""
        target = self._resolve(path, must_exist=True)
        if not target.is_dir():
            return f"FILE {self._relative(target)} ({target.stat().st_size} bytes)"
        max_depth = max(0, min(int(max_depth), 8))
        rows: list[str] = []
        for current_root, dir_names, file_names in os.walk(target):
            current = Path(current_root)
            depth = len(current.relative_to(target).parts)
            dir_names[:] = sorted(
                name
                for name in dir_names
                if name not in {".git", ".venv", "__pycache__"}
            )
            if depth >= max_depth:
                dir_names[:] = []
            for name in dir_names:
                rows.append(f"DIR  {self._relative(current / name)}/")
            for name in sorted(file_names):
                item = current / name
                try:
                    size = item.stat().st_size
                except OSError:
                    continue
                rows.append(f"FILE {self._relative(item)} ({size} bytes)")
            if len(rows) >= 500:
                rows.append("...[listing capped at 500 entries]")
                break
        return self._truncate("\n".join(rows) if rows else "(empty directory)")

    def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        """Read a bounded range from a UTF-8 workspace file."""
        target = self._resolve(path, must_exist=True)
        if not target.is_file():
            raise ToolError(f"not a regular file: {path}")
        if target.stat().st_size > 2_000_000:
            raise ToolError(
                "file is larger than 2 MB; use search_files or a targeted method"
            )
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ToolError("file is not valid UTF-8 text") from exc
        start = max(int(start_line), 1)
        stop = min(
            int(end_line) if end_line is not None else start + 249,
            len(lines),
        )
        if stop < start:
            raise ToolError("end_line must be greater than or equal to start_line")
        body = "\n".join(
            f"{number:6d}  {lines[number - 1]}" for number in range(start, stop + 1)
        )
        header = f"{self._relative(target)} lines {start}-{stop} of {len(lines)}"
        return self._truncate(header + "\n" + body)

    def search_files(
        self,
        query: str,
        path: str = ".",
        glob: str = "*",
        regex: bool = False,
        max_results: int = 50,
    ) -> str:
        """Search bounded UTF-8 files beneath a workspace path."""
        target = self._resolve(path, must_exist=True)
        limit = max(1, min(int(max_results), 200))
        try:
            pattern = re.compile(query) if regex else None
        except re.error as exc:
            raise ToolError(f"invalid regular expression: {exc}") from exc
        candidates = [target] if target.is_file() else target.rglob("*")
        results: list[str] = []
        for candidate in candidates:
            if not candidate.is_file() or ".git" in candidate.parts:
                continue
            relative = self._relative(candidate)
            if not fnmatch.fnmatch(candidate.name, glob) and not fnmatch.fnmatch(
                relative, glob
            ):
                continue
            try:
                if candidate.stat().st_size > 2_000_000:
                    continue
                lines = candidate.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(lines, 1):
                matched = (
                    bool(pattern.search(line)) if pattern is not None else query in line
                )
                if matched:
                    results.append(f"{relative}:{line_number}:{line}")
                    if len(results) >= limit:
                        return self._truncate(
                            "\n".join(results) + "\n...[result limit reached]"
                        )
        return self._truncate("\n".join(results) if results else "(no matches)")

    def write_file(
        self,
        path: str,
        content: str,
        overwrite: bool = False,
    ) -> str:
        """Create or explicitly overwrite a UTF-8 workspace file."""
        target = self._resolve(path)
        if target.exists() and not target.is_file():
            raise ToolError(f"target is not a regular file: {path}")
        if target.exists() and not overwrite:
            raise ToolError(
                "target already exists; set overwrite=true or use replace_text"
            )
        action = "overwrite" if target.exists() else "create"
        detail = f"{action} {self._relative(target)} ({len(content)} characters)"
        if not self.policies.approve("write", detail):
            raise ToolError("user denied the write operation")
        self._atomic_write(target, content)
        return f"{action}d {self._relative(target)} ({len(content)} characters)"

    def replace_text(
        self,
        path: str,
        old_text: str,
        new_text: str,
        expected_replacements: int = 1,
    ) -> str:
        """Replace an exact block only when its occurrence count matches."""
        target = self._resolve(path, must_exist=True)
        if not target.is_file():
            raise ToolError(f"not a regular file: {path}")
        source = target.read_text(encoding="utf-8")
        count = source.count(old_text)
        expected = int(expected_replacements)
        if count != expected:
            raise ToolError(
                f"expected {expected} exact occurrence(s), found {count}; "
                "file was not changed"
            )
        detail = (
            f"edit {self._relative(target)}: replace {count} occurrence(s), "
            f"{len(old_text)} old characters -> {len(new_text)} new characters"
        )
        if not self.policies.approve("write", detail):
            raise ToolError("user denied the edit operation")
        self._atomic_write(target, source.replace(old_text, new_text))
        return f"edited {self._relative(target)}; replaced {count} occurrence(s)"

    def make_directory(self, path: str) -> str:
        """Create a workspace directory after write approval."""
        target = self._resolve(path)
        if target.exists():
            if target.is_dir():
                return f"directory already exists: {self._relative(target)}"
            raise ToolError(f"path exists and is not a directory: {path}")
        detail = f"create directory {self._relative(target)}/"
        if not self.policies.approve("write", detail):
            raise ToolError("user denied directory creation")
        target.mkdir(parents=True)
        return f"created directory {self._relative(target)}/"

    def run_command(self, command: str, timeout_seconds: int = 60) -> str:
        """Run an approved shell command with the workspace as its initial cwd."""
        timeout = max(1, min(int(timeout_seconds), 300))
        detail = f"cwd={self.root}\n$ {command}"
        if not self.policies.approve("shell", detail):
            raise ToolError("user denied shell execution")
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                ["/bin/bash", "-lc", command],
                cwd=self.root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or ""
            raise ToolError(
                self._truncate(f"command timed out after {timeout}s\n{output}")
            ) from exc
        elapsed = time.perf_counter() - started
        output = completed.stdout or ""
        result = (
            f"exit_code={completed.returncode} elapsed_seconds={elapsed:.3f}\n{output}"
        )
        return self._truncate(result.rstrip())

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """Validate and invoke an allowlisted tool, returning a JSON result."""
        if name not in self.ALLOWED_TOOLS:
            return json.dumps({"ok": False, "error": f"unknown tool: {name}"})
        method = getattr(self, name)
        try:
            result = method(**arguments)
            return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
        except (ToolError, OSError, TypeError, ValueError) as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


@dataclass
class CompletionResult:
    """One assembled streaming chat-completion result."""

    message: dict[str, Any]
    usage: dict[str, Any]
    elapsed_seconds: float
    ttft_seconds: float | None


class VllmClient:
    """Small OpenAI SDK adapter for streaming vLLM chat completions."""

    def __init__(
        self,
        client: Any,
        model_name: str,
        timeout: int,
        max_tokens: int,
    ) -> None:
        self.client = client
        self.model_name = model_name
        self.timeout = timeout
        self.max_tokens = max_tokens

    def complete(self, messages: list[dict[str, Any]]) -> CompletionResult:
        """Stream one model step and assemble content and tool calls."""
        started = time.perf_counter()
        first_token: float | None = None
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        stream = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            parallel_tool_calls=False,
            temperature=0,
            reasoning_effort="low",
            max_tokens=self.max_tokens,
            stream=True,
            stream_options={"include_usage": True},
            timeout=self.timeout,
        )
        for chunk in stream:
            if chunk.usage is not None:
                usage = chunk.usage.model_dump()
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                if first_token is None:
                    first_token = time.perf_counter()
                content_parts.append(delta.content)
            for tool_delta in delta.tool_calls or []:
                if first_token is None:
                    first_token = time.perf_counter()
                index = tool_delta.index
                call = tool_calls.setdefault(
                    index,
                    {
                        "id": tool_delta.id or f"call_{index}",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if tool_delta.id:
                    call["id"] = tool_delta.id
                if tool_delta.function is not None:
                    if tool_delta.function.name:
                        call["function"]["name"] += tool_delta.function.name
                    if tool_delta.function.arguments:
                        call["function"]["arguments"] += tool_delta.function.arguments
        finished = time.perf_counter()
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts) or None,
        }
        if tool_calls:
            message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
        return CompletionResult(
            message=message,
            usage=usage,
            elapsed_seconds=finished - started,
            ttft_seconds=(first_token - started) if first_token else None,
        )


class CodingChat:
    """Maintain conversation state and execute model-requested tools."""

    def __init__(
        self,
        client: VllmClient,
        tools: WorkspaceTools,
        *,
        system_prompt: str | None,
        max_steps: int,
        history_char_budget: int,
        show_stats: bool,
    ) -> None:
        prompt = DEFAULT_SYSTEM_PROMPT
        if system_prompt:
            prompt += f"\nAdditional system instructions:\n{system_prompt}"
        prompt += (
            f"\nThe workspace root is {tools.root}. Pass only relative paths "
            "to file tools."
        )
        self.client = client
        self.tools = tools
        self.max_steps = max_steps
        self.history_char_budget = history_char_budget
        self.show_stats = show_stats
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": prompt}]

    @staticmethod
    def _message_size(message: dict[str, Any]) -> int:
        return len(json.dumps(message, ensure_ascii=False))

    def _trim_history(self) -> None:
        # Tool results must never survive without their initiating assistant call.
        total = sum(self._message_size(item) for item in self.messages)
        if total <= self.history_char_budget:
            return
        system = self.messages[0]
        turns: list[list[dict[str, Any]]] = []
        for message in self.messages[1:]:
            if message.get("role") == "user" or not turns:
                turns.append([])
            turns[-1].append(message)
        kept_turns: list[list[dict[str, Any]]] = []
        used = self._message_size(system)
        for turn in reversed(turns):
            size = sum(self._message_size(message) for message in turn)
            if kept_turns and used + size > self.history_char_budget:
                break
            kept_turns.append(turn)
            used += size
        kept_turns.reverse()
        kept = [message for turn in kept_turns for message in turn]
        self.messages = [
            system,
            {
                "role": "system",
                "content": (
                    "Older turns were dropped to fit the model context. "
                    "Re-read files when needed."
                ),
            },
            *kept,
        ]

    def clear(self) -> None:
        """Discard conversation history while preserving the system prompt."""
        self.messages = self.messages[:1]

    def _print_stats(self, result: CompletionResult, step: int) -> None:
        completion_tokens = result.usage.get("completion_tokens", 0)
        print(
            f"[model step {step}: {result.elapsed_seconds:.3f}s, "
            f"prompt={result.usage.get('prompt_tokens', '?')}, "
            f"completion={completion_tokens}]",
            file=sys.stderr,
        )
        if result.ttft_seconds is not None:
            print(f"TTFT: {result.ttft_seconds * 1000:.2f} ms", file=sys.stderr)
        if completion_tokens and result.elapsed_seconds > 0:
            rate = completion_tokens / result.elapsed_seconds
            print(f"TPS:  {rate:.2f} tokens/s", file=sys.stderr)
        if result.ttft_seconds is not None and completion_tokens > 1:
            decode_seconds = result.elapsed_seconds - result.ttft_seconds
            if decode_seconds > 0:
                tpot_ms = decode_seconds * 1000 / (completion_tokens - 1)
                print(f"TPOT: {tpot_ms:.3f} ms/token", file=sys.stderr)

    def run_turn(self, user_text: str) -> str:
        """Run model/tool steps until the model produces a final response."""
        self.messages.append({"role": "user", "content": user_text})
        for step in range(1, self.max_steps + 1):
            self._trim_history()
            result = self.client.complete(self.messages)
            self.messages.append(result.message)
            if self.show_stats:
                self._print_stats(result, step)
            tool_calls = result.message.get("tool_calls") or []
            if not tool_calls:
                content = result.message.get("content") or ""
                print(content)
                return content
            for call in tool_calls:
                function = call.get("function") or {}
                name = function.get("name", "")
                raw_arguments = function.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be a JSON object")
                except (json.JSONDecodeError, ValueError) as exc:
                    tool_result = json.dumps(
                        {"ok": False, "error": f"invalid arguments: {exc}"}
                    )
                else:
                    rendered = json.dumps(arguments, ensure_ascii=False)
                    print(f"[tool] {name}({rendered})", file=sys.stderr)
                    tool_result = self.tools.execute(name, arguments)
                    parsed = json.loads(tool_result)
                    status = "ok" if parsed.get("ok") else "error"
                    detail = parsed.get("result") or parsed.get("error")
                    print(f"[tool {status}] {detail}", file=sys.stderr)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "name": name,
                        "content": tool_result,
                    }
                )
        raise RuntimeError(
            f"agent exceeded the maximum of {self.max_steps} model/tool steps"
        )


def build_parser() -> argparse.ArgumentParser:
    """Build an argument parser aligned with the existing ``vllm chat`` CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="http://localhost:8000/v1",
        help="URL of the running OpenAI-compatible RESTful API server.",
    )
    parser.add_argument(
        "--model-name",
        default=None,
        help="Model name, defaulting to the first model returned by the server.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key, overriding OPENAI_API_KEY when provided.",
    )
    parser.add_argument(
        "--system-prompt",
        default=None,
        help="Additional system instructions for the coding agent.",
    )
    parser.add_argument(
        "-q",
        "--quick",
        metavar="MESSAGE",
        help="Send one request and exit.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print TTFT, end-to-end TPS, and TPOT after each model step.",
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument(
        "--write-policy",
        choices=("ask", "allow", "deny"),
        default="ask",
    )
    parser.add_argument(
        "--shell-policy",
        choices=("ask", "allow", "deny"),
        default="ask",
    )
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--history-char-budget", type=int, default=18000)
    parser.add_argument("--max-tool-output-chars", type=int, default=16000)
    parser.add_argument("--prompt-file", type=Path)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments and validate mutually exclusive input modes."""
    args = build_parser().parse_args(argv)
    if args.quick is not None and args.prompt_file is not None:
        raise SystemExit("--quick and --prompt-file cannot be used together")
    return args


def create_client(args: argparse.Namespace) -> VllmClient:
    """Create the OpenAI client and resolve the served model name."""
    from openai import OpenAI

    api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "EMPTY")
    client = OpenAI(api_key=api_key, base_url=args.url, timeout=args.timeout)
    model_name = args.model_name
    if model_name is None:
        models = client.models.list()
        if not models.data:
            raise RuntimeError("the endpoint returned no served models")
        model_name = models.data[0].id
    print(f"Using model: {model_name}")
    return VllmClient(client, model_name, args.timeout, args.max_tokens)


def read_multiline() -> str:
    """Read an interactive multiline prompt terminated by ``.end``."""
    print("Enter your message. Finish with a line containing only .end")
    lines: list[str] = []
    while True:
        try:
            line = input("| ")
        except EOFError:
            break
        if line == ".end":
            break
        lines.append(line)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run one-shot or interactive coding chat."""
    args = parse_args(argv)
    workspace = args.workspace.expanduser()
    if not workspace.exists() or not workspace.is_dir():
        print(
            f"Workspace does not exist or is not a directory: {workspace}",
            file=sys.stderr,
        )
        return 2
    tools = WorkspaceTools(
        workspace,
        Policies(write=args.write_policy, shell=args.shell_policy),
        max_output_chars=args.max_tool_output_chars,
    )
    chat = CodingChat(
        create_client(args),
        tools,
        system_prompt=args.system_prompt,
        max_steps=max(1, args.max_steps),
        history_char_budget=max(4000, args.history_char_budget),
        show_stats=args.stats,
    )
    if args.quick is not None or args.prompt_file is not None:
        prompt = (
            args.quick
            if args.quick is not None
            else args.prompt_file.read_text(encoding="utf-8")
        )
        chat.run_turn(prompt)
        return 0

    print("Please enter a message for the coding chat model:")
    print(f"Workspace: {tools.root}")
    print("Commands: /help, /paste, /clear, /status, /exit")
    while True:
        try:
            user_text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not user_text:
            continue
        if user_text in {"/exit", "/quit"}:
            return 0
        if user_text == "/clear":
            chat.clear()
            print("Conversation cleared.")
            continue
        if user_text == "/status":
            print(f"Endpoint: {args.url}")
            print(f"Workspace: {tools.root}")
            print(f"Messages retained: {len(chat.messages)}")
            continue
        if user_text == "/help":
            print("/paste  enter a multiline prompt terminated by .end")
            print("/clear  clear conversation history")
            print("/status show endpoint and workspace")
            print("/exit   quit")
            continue
        if user_text == "/paste":
            user_text = read_multiline()
            if not user_text:
                continue
        try:
            chat.run_turn(user_text)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
