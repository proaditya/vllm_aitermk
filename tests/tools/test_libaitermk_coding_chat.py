# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_endpoint_launcher_requires_explicit_model_paths() -> None:
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "tools/libaitermk/start_endpoint.sh")],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 2
    assert "--model must name" in result.stderr


def test_endpoint_launcher_requires_compiled_checkpoint(tmp_path: Path) -> None:
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


def test_endpoint_launcher_has_portable_defaults() -> None:
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
    assert "${repo_root}/.cache/libaitermk/endpoint" in source
    assert "${repo_root}/.cache/libaitermk/compiler" in source
    assert "VLLM_ROCM_USE_AITER=1" in source
    assert "REDLINE_VLLM_ENABLE_QUANTUM_K8=1" in source
    assert "REDLINE_VLLM_ENABLE_QUANTUM_K1=1" not in source


def test_pi_launcher_only_connects_to_existing_endpoint() -> None:
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
    assert "${repo_root}/.cache/libaitermk/pi" in source


def test_pi_launcher_optional_extensions_are_pinned() -> None:
    source = (REPO_ROOT / "tools/libaitermk/start_pi_chat.sh").read_text()
    assert 'if [[ "${stats}" == "1" ]]' in source
    assert 'extension_args+=(--extension "${speed_extension}")' in source
    assert "--web-access)" in source
    assert 'extension_args+=(--extension "${web_extension}")' in source

    package = json.loads(
        (REPO_ROOT / "tools/libaitermk/pi/package.json").read_text()
    )
    assert package["dependencies"]["@earendil-works/pi-coding-agent"] == "0.84.4"
    assert package["dependencies"]["pi-agent-web-access"] == "1.1.2"
    assert package["dependencies"]["pi-token-speed"].endswith(
        "/e1e139e8740fa5a166ce3a3834411c658fecee34.tar.gz"
    )


def test_pi_launcher_supports_explicit_unattended_permissions() -> None:
    source = (REPO_ROOT / "tools/libaitermk/start_pi_chat.sh").read_text()
    assert "--dangerously-skip-permissions)" in source
    assert "write_policy=allow" in source
    assert "shell_policy=allow" in source
    assert 'echo "Pi permissions:' in source
