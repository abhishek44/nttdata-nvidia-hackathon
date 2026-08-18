from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from recallzero.config import Settings


@dataclass(slots=True)
class FreezeCheck:
    name: str
    ok: bool
    expected: str
    actual: str


@dataclass(slots=True)
class FreezeVerification:
    freeze_id: str
    ok: bool
    checks: list[FreezeCheck] = field(default_factory=list)

    @property
    def failures(self) -> list[FreezeCheck]:
        return [item for item in self.checks if not item.ok]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_freeze_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("Freeze manifest must be a YAML mapping")
    if not raw.get("freeze_id"):
        raise ValueError("Freeze manifest must define freeze_id")
    return raw


def find_project_root(start: Path) -> Path:
    start = start.resolve()
    current = start if start.is_dir() else start.parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "src" / "recallzero").exists():
            return candidate
    raise FileNotFoundError(f"Could not locate RecallZero project root from {start}")


def _scalar(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)


def _compare_mapping(
    checks: list[FreezeCheck],
    prefix: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> None:
    for key in sorted(expected):
        expected_value = expected[key]
        name = f"{prefix}.{key}"
        if key not in actual:
            checks.append(FreezeCheck(name=name, ok=False, expected=_scalar(expected_value), actual="<missing>"))
            continue
        actual_value = actual[key]
        if isinstance(expected_value, dict):
            if not isinstance(actual_value, dict):
                checks.append(
                    FreezeCheck(name=name, ok=False, expected=_scalar(expected_value), actual=_scalar(actual_value))
                )
                continue
            _compare_mapping(checks, name, expected_value, actual_value)
            continue
        checks.append(
            FreezeCheck(
                name=name,
                ok=actual_value == expected_value,
                expected=_scalar(expected_value),
                actual=_scalar(actual_value),
            )
        )


def verify_freeze(
    manifest_path: Path,
    settings: Settings,
    *,
    project_root: Path | None = None,
) -> FreezeVerification:
    """Verify source hashes and *live loaded* detector configuration against a freeze manifest.

    This intentionally does not trust duplicated numbers in the manifest alone. Risk values are
    loaded from ``Settings().risk_config()`` (which reads the active ``risk_file``), and clustering,
    trend, and model settings come from the live ``Settings`` object used to build the pipeline.
    """

    manifest_path = manifest_path.resolve()
    manifest = load_freeze_manifest(manifest_path)
    root = project_root.resolve() if project_root else find_project_root(manifest_path)
    checks: list[FreezeCheck] = []

    for section_name in ("detector_modules", "evaluation_modules"):
        section = manifest.get(section_name, {}) or {}
        if not isinstance(section, dict):
            raise ValueError(f"{section_name} must be a mapping")
        for module_name, spec in sorted(section.items()):
            if not isinstance(spec, dict) or not spec.get("path") or not spec.get("sha256"):
                raise ValueError(f"{section_name}.{module_name} must define path and sha256")
            path = root / str(spec["path"])
            actual_hash = sha256_file(path) if path.exists() else "<missing>"
            expected_hash = str(spec["sha256"])
            checks.append(
                FreezeCheck(
                    name=f"{section_name}.{module_name}",
                    ok=actual_hash == expected_hash,
                    expected=expected_hash,
                    actual=actual_hash,
                )
            )

    runtime = manifest.get("runtime", {}) or {}
    if not isinstance(runtime, dict):
        raise ValueError("runtime must be a mapping")

    expected_models = runtime.get("models", {}) or {}
    actual_models = {
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "use_nim": settings.use_nim,
    }
    _compare_mapping(checks, "runtime.models", expected_models, actual_models)

    expected_risk = runtime.get("risk", {}) or {}
    actual_risk = settings.risk_config().model_dump(mode="python")
    _compare_mapping(checks, "runtime.risk", expected_risk, actual_risk)

    expected_clustering = runtime.get("clustering", {}) or {}
    actual_clustering = settings.clustering.model_dump(mode="python")
    _compare_mapping(checks, "runtime.clustering", expected_clustering, actual_clustering)

    expected_trend = runtime.get("trend", {}) or {}
    actual_trend = settings.trend.model_dump(mode="python")
    _compare_mapping(checks, "runtime.trend", expected_trend, actual_trend)

    expected_execution = runtime.get("execution", {}) or {}
    actual_execution = {
        "llm_concurrency": settings.llm_concurrency,
        "signature_batch_size": settings.signature_batch_size,
        "transient_nim_fallback": settings.transient_nim_fallback,
    }
    _compare_mapping(checks, "runtime.execution", expected_execution, actual_execution)

    risk_file_spec = (manifest.get("runtime_config_files", {}) or {}).get("risk_file")
    if risk_file_spec:
        if not isinstance(risk_file_spec, dict) or not risk_file_spec.get("sha256"):
            raise ValueError("runtime_config_files.risk_file must define sha256")
        risk_path = settings.risk_file
        if not risk_path.is_absolute():
            risk_path = (Path.cwd() / risk_path).resolve()
        actual_hash = sha256_file(risk_path) if risk_path.exists() else "<missing>"
        expected_hash = str(risk_file_spec["sha256"])
        checks.append(
            FreezeCheck(
                name="runtime_config_files.risk_file.sha256",
                ok=actual_hash == expected_hash,
                expected=expected_hash,
                actual=actual_hash,
            )
        )

    ok = all(item.ok for item in checks)
    return FreezeVerification(freeze_id=str(manifest["freeze_id"]), ok=ok, checks=checks)
