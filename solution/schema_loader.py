"""Schema loader and validator for workflow definitions and disposition catalogues."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from solution.normalizers import normalise_header


@dataclass
class DispositionInfo:
    workflow_id: str
    code: str
    label: str
    is_terminal: bool
    is_success: bool


@dataclass
class WorkflowSchema:
    workflow_id: str
    description: str
    fields: list[dict[str, Any]]
    reference_field: str
    header_to_field: dict[str, str] = field(default_factory=dict)
    field_by_name: dict[str, dict[str, Any]] = field(default_factory=dict)

    def match_header(self, header: str) -> str | None:
        """Map a raw CSV column header to a schema field name, or None if no match."""
        norm = normalise_header(header)
        return self.header_to_field.get(norm)


class SchemaRegistry:
    def __init__(self, schema_dir: str | Path):
        self.schema_dir = Path(schema_dir)
        self.workflows: dict[str, WorkflowSchema] = {}
        self.dispositions: dict[tuple[str, str], DispositionInfo] = {}
        self.disposition_codes_by_workflow: dict[str, set[str]] = {}
        self.load()

    def load(self) -> None:
        # 1. Load workflow schemas (*.json)
        for json_path in self.schema_dir.glob("*.json"):
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            wf_id = data["workflow_id"]
            fields = data.get("fields", [])
            header_map: dict[str, str] = {}
            field_by_name: dict[str, dict[str, Any]] = {}
            ref_field = ""

            for f_spec in fields:
                fname = f_spec["name"]
                field_by_name[fname] = f_spec
                if f_spec.get("reference"):
                    ref_field = fname
                
                # Match normalized field name
                header_map[normalise_header(fname)] = fname
                # Match normalized aliases
                for alias in f_spec.get("aliases", []):
                    header_map[normalise_header(alias)] = fname

            self.workflows[wf_id] = WorkflowSchema(
                workflow_id=wf_id,
                description=data.get("description", ""),
                fields=fields,
                reference_field=ref_field,
                header_to_field=header_map,
                field_by_name=field_by_name,
            )

        # 2. Load dispositions.csv
        disp_path = self.schema_dir / "dispositions.csv"
        if disp_path.exists():
            with open(disp_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    wf = row["workflow_id"].strip()
                    code = row["code"].strip()
                    is_term = row["is_terminal"].strip().lower() == "true"
                    is_succ = row["is_success"].strip().lower() == "true"
                    info = DispositionInfo(
                        workflow_id=wf,
                        code=code,
                        label=row.get("label", "").strip(),
                        is_terminal=is_term,
                        is_success=is_succ,
                    )
                    self.dispositions[(wf, code)] = info
                    if wf not in self.disposition_codes_by_workflow:
                        self.disposition_codes_by_workflow[wf] = set()
                    self.disposition_codes_by_workflow[wf].add(code)

    def get_workflow(self, workflow_id: str) -> WorkflowSchema | None:
        return self.workflows.get(workflow_id)

    def is_valid_disposition(self, workflow_id: str, code: str) -> bool:
        codes = self.disposition_codes_by_workflow.get(workflow_id)
        if not codes:
            return False
        return code in codes

    def get_disposition(self, workflow_id: str, code: str) -> DispositionInfo | None:
        return self.dispositions.get((workflow_id, code))

