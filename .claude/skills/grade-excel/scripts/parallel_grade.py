#!/usr/bin/env python3
"""
Parallel grading primitives for the grade-excel skill.

This script only handles workbook/file I/O:
1. Export workbook payload once
2. Split payload into shard files
3. Merge partial grading results
4. Apply grading to workbook
5. Verify workbook cells

The LLM grading step is orchestrated by the skill itself.

Usage:
    python scripts/parallel_grade.py export --input results.xlsx
    python scripts/parallel_grade.py split --input results.xlsx --agents 4
    python scripts/parallel_grade.py merge --input results.xlsx
    python scripts/parallel_grade.py apply --input results.xlsx --grading grading.json
    python scripts/parallel_grade.py verify --input results.xlsx
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).parent.resolve()
SKILL_DIR = SCRIPT_DIR.parent
GRADE_EXCEL_PY = SKILL_DIR / "scripts" / "grade_excel.py"
TEMP_DIR = SKILL_DIR / "temp"


@dataclass
class AgentConfig:
    id: int
    sheets: list[str]
    payload_file: Path
    output_file: Path


def run_command(cmd: list[str], timeout: int = 300) -> str:
    """Run grade_excel.py command and return stdout."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nError: {result.stderr}")
    return result.stdout


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict[str, Any], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def export_workbook(input_path: str | Path) -> Path:
    """Export workbook payload to temp JSON file."""
    input_path = Path(input_path)
    payload_path = TEMP_DIR / f"{input_path.stem}_payload.json"

    if payload_path.exists():
        print(f"[Export] Using existing payload: {payload_path}")
        return payload_path

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[Export] Exporting workbook...")
    t0 = time.time()

    stdout = run_command([
        sys.executable, str(GRADE_EXCEL_PY),
        "export", "--input", str(input_path),
    ])

    payload = json.loads(stdout)
    save_json(payload, payload_path)

    elapsed = time.time() - t0
    print(f"[Export] Done in {elapsed:.1f}s → {payload_path}")
    print(f"  Workbook: {payload['workbook_path']}")
    print(f"  Total sheets: {len(payload['sheets'])}")
    print(f"  Total questions: {sum(len(s['questions']) for s in payload['sheets'])}")

    return payload_path


def split_sheets(payload_path: Path, num_agents: int) -> list[AgentConfig]:
    """Split payload into agent-specific payload files."""
    payload = load_json(payload_path)
    all_sheets = payload["sheets"]
    total = len(all_sheets)

    # Calculate distribution
    base = total // num_agents
    remainder = total % num_agents

    agents: list[AgentConfig] = []
    start = 0

    for agent_id in range(1, num_agents + 1):
        count = base + (1 if agent_id <= remainder else 0)
        if count == 0:
            break

        sheet_names = [s["sheet_name"] for s in all_sheets[start:start + count]]
        agent_sheets = all_sheets[start:start + count]

        # Create agent payload
        agent_payload = {
            "workbook_path": payload["workbook_path"],
            "headers": payload["headers"],
            "rubric": payload["rubric"],
            "sheets": agent_sheets,
            "metadata": {
                "agent_id": agent_id,
                "total_agents": num_agents,
                "sheet_count": len(agent_sheets),
            }
        }

        payload_file = TEMP_DIR / f"agent_{agent_id}_payload.json"
        output_file = TEMP_DIR / f"partial_grading_{agent_id}.json"

        save_json(agent_payload, payload_file)

        agents.append(AgentConfig(
            id=agent_id,
            sheets=sheet_names,
            payload_file=payload_file,
            output_file=output_file,
        ))

        start += count

    # Save agent manifest
    manifest = {
        "total_agents": len(agents),
        "payload_path": str(payload_path),
        "agents": [
            {
                "id": a.id,
                "sheets": a.sheets,
                "payload_file": str(a.payload_file),
                "output_file": str(a.output_file),
            }
            for a in agents
        ]
    }
    manifest_path = TEMP_DIR / "agent_manifest.json"
    save_json(manifest, manifest_path)

    return agents



def merge_partial_results(manifest_path: Path, output_path: Path) -> Path:
    """Merge all partial grading JSONs into one grading file."""
    manifest = load_json(manifest_path)

    all_sheets: list[dict[str, Any]] = []
    merged_agent_ids: list[int] = []

    print(f"\n[Merge] Reading {manifest['total_agents']} partial results...")

    for agent_info in manifest["agents"]:
        output_file = Path(agent_info["output_file"])
        if not output_file.exists():
            print(f"  WARNING: Agent {agent_info['id']} output not found: {output_file}")
            continue

        try:
            data = load_json(output_file)
            sheets = data.get("sheets", [])
            all_sheets.extend(sheets)
            merged_agent_ids.append(agent_info["id"])
            print(f"  Agent {agent_info['id']}: {len(sheets)} sheets OK")
        except Exception as e:
            print(f"  ERROR: Agent {agent_info['id']} failed to load: {e}")

    if not all_sheets:
        raise RuntimeError("No partial grading results found!")

    merged = {"sheets": all_sheets}
    save_json(merged, output_path)

    print(f"\n[Merge] Combined {len(all_sheets)} sheets from {len(merged_agent_ids)} agents")
    print(f"        → {output_path}")

    return output_path


def apply_grading(input_path: str, grading_path: str) -> Path:
    """Apply grading JSON to workbook using grade_excel.py."""
    print(f"\n[Apply] Applying grading to workbook...")

    grading_data = load_json(Path(grading_path))

    # Validate grading data
    if "sheets" not in grading_data:
        raise ValueError("Invalid grading JSON: missing 'sheets' key")

    for sheet in grading_data["sheets"]:
        if "sheet_name" not in sheet or "rows" not in sheet:
            raise ValueError(f"Invalid sheet data: {sheet}")

    # Use grade_excel.py to apply
    run_command([
        sys.executable, str(GRADE_EXCEL_PY),
        "apply",
        "--input", input_path,
        "--grading", grading_path,
    ])

    print(f"[Apply] Done → {input_path}")
    return Path(input_path)


def verify_workbook(input_path: str, sheet_name: str | None = None) -> dict[str, Any]:
    """Verify workbook cells."""
    cmd = [
        sys.executable, str(GRADE_EXCEL_PY),
        "verify", "--input", input_path,
    ]
    if sheet_name:
        cmd.extend(["--sheet", sheet_name])

    stdout = run_command(cmd)
    return json.loads(stdout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parallel grading primitives for Excel workbooks."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Export command
    export_parser = subparsers.add_parser("export", help="Export workbook payload")
    export_parser.add_argument("--input", required=True, help="Path to .xlsx workbook")
    export_parser.add_argument("--force", action="store_true", help="Force re-export")

    # Split command
    split_parser = subparsers.add_parser("split", help="Split payload for parallel agents")
    split_parser.add_argument("--input", required=True, help="Path to .xlsx workbook")
    split_parser.add_argument("--agents", type=int, default=4, help="Number of parallel agents")

    # Merge command
    merge_parser = subparsers.add_parser("merge", help="Merge partial grading results")
    merge_parser.add_argument("--input", required=True, help="Path to .xlsx workbook")
    merge_parser.add_argument("--output", help="Output grading JSON path")

    # Apply command
    apply_parser = subparsers.add_parser("apply", help="Apply grading to workbook")
    apply_parser.add_argument("--input", required=True, help="Path to .xlsx workbook")
    apply_parser.add_argument("--grading", required=True, help="Path to grading JSON")

    # Verify command
    verify_parser = subparsers.add_parser("verify", help="Verify workbook cells")
    verify_parser.add_argument("--input", required=True, help="Path to .xlsx workbook")
    verify_parser.add_argument("--sheet", help="Optional specific sheet to verify")

    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args()

    input_path = Path(args.input)
    payload_path = input_path.parent / f"{input_path.stem}_payload.json"
    manifest_path = input_path.parent / "agent_manifest.json"

    if args.command == "export":
        payload_file = export_workbook(args.input)
        print(str(payload_file))
        return 0

    if args.command == "split":
        if not payload_path.exists():
            payload_path = export_workbook(args.input)

        agents = split_sheets(payload_path, args.agents)
        print(json.dumps({
            "total_agents": len(agents),
            "manifest_path": str(manifest_path),
            "agents": [
                {
                    "id": agent.id,
                    "sheets": agent.sheets,
                    "payload_file": str(agent.payload_file),
                    "output_file": str(agent.output_file),
                }
                for agent in agents
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "merge":
        if not manifest_path.exists():
            print("ERROR: Run 'split' first to create agent manifest")
            return 1

        output_path = Path(args.output) if args.output else TEMP_DIR / f"grading.{input_path.name}.json"
        merged_path = merge_partial_results(manifest_path, output_path)
        print(str(merged_path))
        return 0

    if args.command == "apply":
        output_path = apply_grading(args.input, args.grading)
        print(str(output_path))
        return 0

    if args.command == "verify":
        result = verify_workbook(args.input, getattr(args, 'sheet', None))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())