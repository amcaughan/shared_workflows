#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ACTION_ORDER = ("create", "update", "replace", "delete")
ACTION_LABELS = {
    "create": "Create",
    "update": "Update",
    "replace": "Replace",
    "delete": "Destroy",
}
ACTION_EMOJI = {
    "create": "🟢",
    "update": "🟡",
    "replace": "🟠",
    "delete": "🔴",
}


@dataclass(frozen=True)
class StackPlanInput:
    safe_name: str
    stack_path: str
    plan_json_path: Path


@dataclass(frozen=True)
class ResourceChange:
    address: str
    resource_type: str
    action: str
    actions: tuple[str, ...]
    mode: str


def normalize_actions(actions: list[str]) -> str | None:
    if actions == ["create"]:
        return "create"
    if actions == ["update"]:
        return "update"
    if actions == ["delete"]:
        return "delete"
    if actions in (["delete", "create"], ["create", "delete"]):
        return "replace"
    return None


def load_plan_index(path: Path) -> list[StackPlanInput]:
    entries: list[StackPlanInput] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        safe_name, stack_path, plan_json_path = raw_line.split("\t", 2)
        entries.append(
            StackPlanInput(
                safe_name=safe_name,
                stack_path=stack_path,
                plan_json_path=Path(plan_json_path),
            )
        )
    return entries


def summarize_stack(entry: StackPlanInput) -> dict[str, Any]:
    plan = json.loads(entry.plan_json_path.read_text(encoding="utf-8"))
    resources: list[ResourceChange] = []

    for resource_change in plan.get("resource_changes") or []:
        action = normalize_actions((resource_change.get("change") or {}).get("actions") or [])
        if action is None:
            continue
        resources.append(
            ResourceChange(
                address=resource_change.get("address") or "(unknown)",
                resource_type=resource_change.get("type") or "(unknown)",
                action=action,
                actions=tuple((resource_change.get("change") or {}).get("actions") or []),
                mode=resource_change.get("mode") or "managed",
            )
        )

    resources = sorted(
        resources,
        key=lambda change: (
            ACTION_ORDER.index(change.action),
            change.address,
        ),
    )

    counts = Counter(change.action for change in resources)
    return {
        "safe_name": entry.safe_name,
        "stack_path": entry.stack_path,
        "counts": {action: counts.get(action, 0) for action in ACTION_ORDER},
        "total_changes": sum(counts.values()),
        "resources": [
            {
                "address": change.address,
                "resource_type": change.resource_type,
                "action": change.action,
                "actions": list(change.actions),
                "mode": change.mode,
            }
            for change in resources
        ],
    }


def build_summary(title: str, entries: list[StackPlanInput]) -> dict[str, Any]:
    stacks = [summarize_stack(entry) for entry in entries]
    totals = Counter()
    for stack in stacks:
        totals.update(stack["counts"])

    return {
        "title": title,
        "totals": {action: totals.get(action, 0) for action in ACTION_ORDER},
        "total_changes": sum(totals.values()),
        "stacks": stacks,
        "notes": [
            "Sanitized plan summary only.",
            "Raw planfiles and before/after values are not uploaded.",
        ],
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"## {summary['title']}",
        "",
        "_Sanitized plan summary only. Raw planfiles and before/after values are not uploaded._",
        "",
        "| Action | Count |",
        "| --- | ---: |",
    ]

    for action in ACTION_ORDER:
        lines.append(f"| {ACTION_LABELS[action]} | {summary['totals'][action]} |")

    for stack in summary["stacks"]:
        counts = stack["counts"]
        lines.extend(
            [
                "",
                (
                    f"<details open><summary><code>{stack['stack_path']}</code> — "
                    f"{ACTION_EMOJI['create']} {counts['create']} "
                    f"{ACTION_EMOJI['update']} {counts['update']} "
                    f"{ACTION_EMOJI['replace']} {counts['replace']} "
                    f"{ACTION_EMOJI['delete']} {counts['delete']}</summary>"
                ),
                "",
            ]
        )

        if stack["total_changes"] == 0:
            lines.extend(["No resource changes.", "", "</details>"])
            continue

        for action in ACTION_ORDER:
            action_resources = [resource for resource in stack["resources"] if resource["action"] == action]
            if not action_resources:
                continue
            lines.extend([f"### {ACTION_LABELS[action]}", ""])
            for resource in action_resources:
                lines.append(f"- `{resource['address']}` (`{resource['resource_type']}`)")
            lines.append("")

        lines.append("</details>")

    return "\n".join(lines).strip() + "\n"


def render_html(summary: dict[str, Any]) -> str:
    def esc(value: str) -> str:
        return html.escape(value)

    parts = [
        f"<h2>{esc(summary['title'])}</h2>",
        (
            "<p><em>Sanitized plan summary only. Raw planfiles and before/after values "
            "are not uploaded.</em></p>"
        ),
        "<table>",
        "<tr><th>Action</th><th>Count</th></tr>",
    ]

    for action in ACTION_ORDER:
        parts.append(
            "<tr>"
            f"<td>{ACTION_EMOJI[action]} {esc(ACTION_LABELS[action])}</td>"
            f"<td>{summary['totals'][action]}</td>"
            "</tr>"
        )
    parts.append("</table>")

    for stack in summary["stacks"]:
        counts = stack["counts"]
        parts.extend(
            [
                "<details open>",
                (
                    f"<summary><code>{esc(stack['stack_path'])}</code> — "
                    f"{ACTION_EMOJI['create']} {counts['create']} "
                    f"{ACTION_EMOJI['update']} {counts['update']} "
                    f"{ACTION_EMOJI['replace']} {counts['replace']} "
                    f"{ACTION_EMOJI['delete']} {counts['delete']}</summary>"
                ),
            ]
        )

        if stack["total_changes"] == 0:
            parts.extend(["<p>No resource changes.</p>", "</details>"])
            continue

        for action in ACTION_ORDER:
            action_resources = [resource for resource in stack["resources"] if resource["action"] == action]
            if not action_resources:
                continue
            parts.extend(
                [
                    f"<h3>{ACTION_EMOJI[action]} {esc(ACTION_LABELS[action])}</h3>",
                    "<table>",
                    "<tr><th>Resource</th><th>Type</th></tr>",
                ]
            )
            for resource in action_resources:
                parts.append(
                    "<tr>"
                    f"<td><code>{esc(resource['address'])}</code></td>"
                    f"<td><code>{esc(resource['resource_type'])}</code></td>"
                    "</tr>"
                )
            parts.extend(["</table>"])
        parts.append("</details>")

    return "\n".join(parts)


def write_outputs(summary: dict[str, Any], out_json: Path, out_markdown: Path, out_html: Path) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_markdown.parent.mkdir(parents=True, exist_ok=True)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_markdown.write_text(render_markdown(summary), encoding="utf-8")
    out_html.write_text(render_html(summary), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--plan-index-tsv", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-markdown", required=True)
    parser.add_argument("--out-html", required=True)
    args = parser.parse_args()

    entries = load_plan_index(Path(args.plan_index_tsv))
    if not entries:
        raise SystemExit("No Terragrunt plan entries found")

    summary = build_summary(args.title, entries)
    write_outputs(
        summary=summary,
        out_json=Path(args.out_json),
        out_markdown=Path(args.out_markdown),
        out_html=Path(args.out_html),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
