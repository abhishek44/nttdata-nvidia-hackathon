from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, TypeVar

from pydantic import BaseModel, TypeAdapter

from recallzero.models import AnalysisRun, BacktestResult, Complaint, FailureSignature, Recall, Vehicle
from recallzero.utils import dumps_json, slugify

T = TypeVar("T", bound=BaseModel)


class FileRepository:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        for name in ("raw", "normalized", "cache", "runs"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)

    def vehicle_key(self, vehicle: Vehicle) -> str:
        return vehicle.slug

    def _write_json(self, path: Path, value: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(dumps_json(value), encoding="utf-8")
        tmp.replace(path)
        return path

    def _read_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def raw_path(self, vehicle: Vehicle, kind: str) -> Path:
        return self.data_dir / "raw" / f"{self.vehicle_key(vehicle)}_{slugify(kind)}.json"

    def save_raw(self, vehicle: Vehicle, kind: str, payload: Any) -> Path:
        return self._write_json(self.raw_path(vehicle, kind), payload)

    def load_raw(self, vehicle: Vehicle, kind: str) -> Any | None:
        path = self.raw_path(vehicle, kind)
        return self._read_json(path) if path.exists() else None

    def complaints_path(self, vehicle: Vehicle) -> Path:
        return self.data_dir / "normalized" / f"{self.vehicle_key(vehicle)}_complaints.json"

    def recalls_path(self, vehicle: Vehicle) -> Path:
        return self.data_dir / "normalized" / f"{self.vehicle_key(vehicle)}_recalls.json"

    def signatures_path(self, vehicle: Vehicle) -> Path:
        return self.data_dir / "cache" / f"{self.vehicle_key(vehicle)}_signatures.json"

    def save_complaints(self, vehicle: Vehicle, complaints: Iterable[Complaint]) -> Path:
        return self._write_json(self.complaints_path(vehicle), [item.model_dump(mode="json") for item in complaints])

    def load_complaints(self, vehicle: Vehicle) -> list[Complaint] | None:
        path = self.complaints_path(vehicle)
        if not path.exists():
            return None
        return TypeAdapter(list[Complaint]).validate_python(self._read_json(path))

    def save_recalls(self, vehicle: Vehicle, recalls: Iterable[Recall]) -> Path:
        return self._write_json(self.recalls_path(vehicle), [item.model_dump(mode="json") for item in recalls])

    def load_recalls(self, vehicle: Vehicle) -> list[Recall] | None:
        path = self.recalls_path(vehicle)
        if not path.exists():
            return None
        return TypeAdapter(list[Recall]).validate_python(self._read_json(path))

    def save_signatures(self, vehicle: Vehicle, signatures: Iterable[FailureSignature]) -> Path:
        return self._write_json(self.signatures_path(vehicle), [item.model_dump(mode="json") for item in signatures])

    def load_signatures(self, vehicle: Vehicle) -> dict[str, FailureSignature]:
        path = self.signatures_path(vehicle)
        if not path.exists():
            return {}
        items = TypeAdapter(list[FailureSignature]).validate_python(self._read_json(path))
        return {item.complaint_id: item for item in items}

    def save_analysis_run(self, run: AnalysisRun) -> Path:
        return self._write_json(self.data_dir / "runs" / f"{run.run_id}.json", run.model_dump(mode="json"))

    def load_analysis_run(self, run_id: str) -> AnalysisRun | None:
        path = self.data_dir / "runs" / f"{slugify(run_id)}.json"
        if not path.exists():
            path = self.data_dir / "runs" / f"{run_id}.json"
        return AnalysisRun.model_validate(self._read_json(path)) if path.exists() else None

    def save_backtest(self, result: BacktestResult) -> Path:
        return self._write_json(self.data_dir / "runs" / f"{result.backtest_id}.json", result.model_dump(mode="json"))

    def load_backtest(self, backtest_id: str) -> BacktestResult | None:
        path = self.data_dir / "runs" / f"{backtest_id}.json"
        return BacktestResult.model_validate(self._read_json(path)) if path.exists() else None

    def find_complaint(self, odi_number: str) -> Complaint | None:
        for path in (self.data_dir / "normalized").glob("*_complaints.json"):
            try:
                items = TypeAdapter(list[Complaint]).validate_python(self._read_json(path))
            except Exception:
                continue
            for item in items:
                if item.odi_number == str(odi_number):
                    return item
        return None
