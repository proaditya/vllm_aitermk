# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.libaitermk.coding_chat import (
    CodingChat,
    CompletionResult,
    Policies,
    ToolError,
    WorkspaceTools,
    parse_args,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tools(tmp_path: Path) -> WorkspaceTools:
    return WorkspaceTools(
        tmp_path,
        Policies(write="allow", shell="deny"),
        max_output_chars=10000,
    )


def test_create_read_and_replace_file(tools: WorkspaceTools, tmp_path: Path) -> None:
    assert "created src/example.py" in tools.write_file("src/example.py", "value = 1\n")
    assert "value = 1" in tools.read_file("src/example.py")
    assert "replaced 1" in tools.replace_text(
        "src/example.py", "value = 1", "value = 2"
    )
    assert (tmp_path / "src/example.py").read_text() == "value = 2\n"


def test_existing_file_requires_explicit_overwrite(
    tools: WorkspaceTools, tmp_path: Path
) -> None:
    tools.write_file("example.txt", "first")
    with pytest.raises(ToolError, match="already exists"):
        tools.write_file("example.txt", "second")
    tools.write_file("example.txt", "second", overwrite=True)
    assert (tmp_path / "example.txt").read_text() == "second"


def test_parent_and_absolute_path_escapes_are_rejected(
    tools: WorkspaceTools,
) -> None:
    with pytest.raises(ToolError, match="escapes"):
        tools.write_file("../outside.txt", "forbidden")
    with pytest.raises(ToolError, match="absolute paths"):
        tools.read_file("/etc/passwd")


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    (tmp_path / "outside-link").symlink_to(outside, target_is_directory=True)
    tools = WorkspaceTools(
        tmp_path,
        Policies(write="allow", shell="deny"),
    )
    with pytest.raises(ToolError, match="escapes"):
        tools.read_file("outside-link/secret.txt")


def test_search_and_listing_are_bounded(
    tools: WorkspaceTools,
) -> None:
    tools.write_file("src/a.py", "alpha\nbeta\n")
    tools.write_file("src/b.txt", "alpha\n")
    results = tools.search_files("alpha", path="src", glob="*.py")
    assert "src/a.py:1:alpha" in results
    assert "src/b.txt" not in results
    assert "DIR  src/" in tools.list_files(".")


def test_replace_requires_exact_occurrence_count(
    tools: WorkspaceTools, tmp_path: Path
) -> None:
    tools.write_file("values.txt", "x\nx\n")
    with pytest.raises(ToolError, match="expected 1 exact occurrence"):
        tools.replace_text("values.txt", "x", "y")
    assert (tmp_path / "values.txt").read_text() == "x\nx\n"


def test_shell_policy_denies_execution(tools: WorkspaceTools) -> None:
    with pytest.raises(ToolError, match="denied"):
        tools.run_command("pwd")


def test_shell_command_starts_in_workspace(tmp_path: Path) -> None:
    tools = WorkspaceTools(
        tmp_path,
        Policies(write="allow", shell="allow"),
    )
    result = tools.run_command("pwd")
    assert "exit_code=0" in result
    assert str(tmp_path) in result


def test_unknown_tool_is_rejected(tools: WorkspaceTools) -> None:
    result = json.loads(tools.execute("_resolve", {"raw_path": "."}))
    assert result == {"ok": False, "error": "unknown tool: _resolve"}


def test_cli_uses_vllm_chat_argument_names(tmp_path: Path) -> None:
    args = parse_args(
        [
            "--workspace",
            str(tmp_path),
            "--model-name",
            "gpt-oss-120b",
            "--quick",
            "inspect the project",
            "--stats",
        ]
    )
    assert args.model_name == "gpt-oss-120b"
    assert args.quick == "inspect the project"
    assert args.stats is True


def test_endpoint_launcher_requires_deployment_model_paths() -> None:
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "tools/libaitermk/start_endpoint.sh")],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 2
    assert "--model must name" in result.stderr


def test_endpoint_launcher_accepts_model_paths_as_cli_arguments(
    tmp_path: Path,
) -> None:
    source_model = tmp_path / "source-model"
    source_model.mkdir()
    (source_model / "config.json").write_text("{}")
    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "tools/libaitermk/start_endpoint.sh"),
            "--model",
            str(source_model),
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 2
    assert "--compiled-checkpoint must name" in result.stderr


def test_endpoint_launcher_has_no_container_or_machine_path_assumptions() -> None:
    source = (REPO_ROOT / "tools/libaitermk/start_endpoint.sh").read_text()
    for forbidden in (
        "docker ",
        "LIBAITERMK_CONTAINER",
        "/opt/venv",
        "/workspace/",
        "/home/",
        "/models/",
        "/compiled-checkpoint",
        "/tmp/",
        "VLLM_MODEL_PATH",
        "model_path=${REDLINE_COMPILED_MODEL_PATH",
    ):
        assert forbidden not in source
    assert "${repo_root}/.venv/bin/python" in source
    assert "${repo_root}/results/libaitermk-coding-chat/endpoint" in source
    assert "${repo_root}/results/libaitermk-coding-chat/cache" in source
    assert "REDLINE_VLLM_ENABLE_QUANTUM_K8=1" in source
    assert "REDLINE_VLLM_ENABLE_QUANTUM_K1=1" not in source


def test_pi_launcher_only_connects_to_an_existing_endpoint() -> None:
    source = (REPO_ROOT / "tools/libaitermk/start_pi_chat.sh").read_text()
    for forbidden in (
        "LIBAITERMK_START_ENDPOINT",
        "start_endpoint.sh",
        "compiled-checkpoint",
        "model_path",
        "--model-name",
    ):
        assert forbidden not in source
    assert "models_url=${endpoint%/}/models" in source
    assert "export VLLM_CHAT_MODEL=${model}" in source


def test_stats_include_decode_tpot(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    chat = CodingChat(
        client=None,
        tools=WorkspaceTools(tmp_path, Policies(write="deny", shell="deny")),
        system_prompt=None,
        max_steps=1,
        history_char_budget=4000,
        show_stats=True,
    )
    chat._print_stats(
        CompletionResult(
            message={"role": "assistant", "content": "done"},
            usage={"prompt_tokens": 569, "completion_tokens": 862},
            elapsed_seconds=2.602,
            ttft_seconds=0.93828,
        ),
        step=1,
    )
    stderr = capsys.readouterr().err
    assert "TPS:  331.28 tokens/s" in stderr
    assert "TPOT: 1.932 ms/token" in stderr


def test_history_trimming_preserves_complete_tool_turns(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path, Policies(write="deny", shell="deny"))
    chat = CodingChat(
        client=None,
        tools=tools,
        system_prompt=None,
        max_steps=1,
        history_char_budget=4000,
        show_stats=False,
    )
    for index in range(10):
        chat.messages.extend(
            [
                {"role": "user", "content": f"request-{index}" * 100},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call-{index}",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"x"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": f"call-{index}",
                    "name": "read_file",
                    "content": "result" * 100,
                },
            ]
        )
    chat._trim_history()
    retained_ids = {
        call["id"]
        for message in chat.messages
        for call in message.get("tool_calls", [])
    }
    for message in chat.messages:
        if message.get("role") == "tool":
            assert message["tool_call_id"] in retained_ids
