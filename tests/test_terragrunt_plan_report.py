from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


def load_plan_report_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "actions"
        / "terragrunt-plan-report"
        / "terragrunt_plan_report.py"
    )
    spec = importlib.util.spec_from_file_location("terragrunt_plan_report", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


terragrunt_plan_report = load_plan_report_module()


class TerragruntPlanReportTests(unittest.TestCase):
    def test_build_summary_counts_changes_and_ignores_reads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            plan_one = temp_path / "core.json"
            plan_two = temp_path / "workflow.json"
            index_path = temp_path / "index.tsv"

            plan_one.write_text(
                json.dumps(
                    {
                        "resource_changes": [
                            {
                                "address": "aws_s3_bucket.ingest",
                                "mode": "managed",
                                "type": "aws_s3_bucket",
                                "name": "ingest",
                                "change": {"actions": ["create"]},
                            },
                            {
                                "address": "aws_ecs_task_definition.dbt",
                                "mode": "managed",
                                "type": "aws_ecs_task_definition",
                                "name": "dbt",
                                "change": {"actions": ["update"]},
                            },
                            {
                                "address": "data.aws_caller_identity.current",
                                "mode": "data",
                                "type": "aws_caller_identity",
                                "name": "current",
                                "change": {"actions": ["read"]},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            plan_two.write_text(
                json.dumps(
                    {
                        "resource_changes": [
                            {
                                "address": "aws_iam_role_policy_attachment.old",
                                "mode": "managed",
                                "type": "aws_iam_role_policy_attachment",
                                "name": "old",
                                "change": {"actions": ["delete"]},
                            },
                            {
                                "address": "aws_lambda_function.emitter",
                                "mode": "managed",
                                "type": "aws_lambda_function",
                                "name": "emitter",
                                "change": {"actions": ["create", "delete"]},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            index_path.write_text(
                "\n".join(
                    [
                        f"core\tlive/dev/core\t{plan_one}",
                        f"workflow\tlive/dev/sample-stream-events-01\t{plan_two}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = terragrunt_plan_report.build_summary(
                "Terragrunt plan report",
                terragrunt_plan_report.load_plan_index(index_path),
            )

        self.assertEqual(summary["totals"]["create"], 1)
        self.assertEqual(summary["totals"]["update"], 1)
        self.assertEqual(summary["totals"]["replace"], 1)
        self.assertEqual(summary["totals"]["delete"], 1)
        self.assertEqual(summary["total_changes"], 4)

        first_stack = summary["stacks"][0]
        self.assertEqual(first_stack["stack_path"], "live/dev/core")
        self.assertEqual(first_stack["counts"]["create"], 1)
        self.assertEqual(first_stack["counts"]["update"], 1)
        self.assertEqual(len(first_stack["resources"]), 2)

    def test_markdown_and_html_include_sanitized_notice(self):
        summary = {
            "title": "Terragrunt plan report",
            "totals": {"create": 1, "update": 0, "replace": 0, "delete": 0},
            "total_changes": 1,
            "stacks": [
                {
                    "safe_name": "core",
                    "stack_path": "live/dev/core",
                    "counts": {"create": 1, "update": 0, "replace": 0, "delete": 0},
                    "total_changes": 1,
                    "resources": [
                        {
                            "address": "aws_s3_bucket.ingest",
                            "resource_type": "aws_s3_bucket",
                            "action": "create",
                            "actions": ["create"],
                            "mode": "managed",
                        }
                    ],
                }
            ],
            "notes": [],
        }

        markdown = terragrunt_plan_report.render_markdown(summary)
        html = terragrunt_plan_report.render_html(summary)

        self.assertIn("Sanitized plan summary only", markdown)
        self.assertIn("aws_s3_bucket.ingest", markdown)
        self.assertIn("Sanitized plan summary only", html)
        self.assertIn("aws_s3_bucket.ingest", html)


if __name__ == "__main__":
    unittest.main()
