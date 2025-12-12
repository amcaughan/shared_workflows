#!/usr/bin/env python3
import argparse
import glob
import html
import json
import os
from collections import defaultdict, Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional


SEV_ORDER = ["error", "warning", "note", "none"]
SEV_EMOJI = {
    "error": "🔴",
    "warning": "🟡",
    "note": "🔵",
    "none": "⚫",
}
SEV_LABEL = {
    "error": "Errors",
    "warning": "Warnings",
    "note": "Notes",
    "none": "None",
}


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _norm_level(level: Optional[str]) -> str:
    if not level:
        return "warning"
    level = level.lower()
    if level == "info":
        return "note"
    if level not in ("none", "note", "warning", "error"):
        return "warning"
    return level


def _tool_name(run: Dict[str, Any]) -> str:
    tool = (run.get("tool") or {}).get("driver") or {}
    return tool.get("name") or "tool"


def _rule_help(run: Dict[str, Any], rule_id: str) -> str:
    rules = ((run.get("tool") or {}).get("driver") or {}).get("rules") or []
    for r in rules:
        if (r.get("id") or "") == rule_id:
            return r.get("helpUri") or ""
    return ""


def _pick_location(result: Dict[str, Any]) -> Tuple[str, str]:
    locs = result.get("locations") or []
    if not locs:
        return ("", "")
    pl = (locs[0].get("physicalLocation") or {})
    arti = (pl.get("artifactLocation") or {}).get("uri") or ""
    region = pl.get("region") or {}
    sl = region.get("startLine")
    el = region.get("endLine")

    loc = arti
    region_s = ""
    if sl is not None:
        loc = f"{arti}:{sl}" if arti else f"{sl}"
        if el and el != sl:
            region_s = f"L{sl}-L{el}"
        else:
            region_s = f"L{sl}"
    return (loc, region_s)


def _msg_text(res: Dict[str, Any]) -> str:
    msg = (res.get("message") or {}).get("text") or ""
    return msg.strip()


def sarif_to_rows(path: Path) -> List[Dict[str, str]]:
    sarif = _read_json(path)
    rows: List[Dict[str, str]] = []

    for run in sarif.get("runs") or []:
        tool = _tool_name(run)
        for res in run.get("results") or []:
            level = _norm_level(res.get("level"))
            rule_id = res.get("ruleId") or ""
            msg = _msg_text(res)
            loc, region = _pick_location(res)
            help_uri = _rule_help(run, rule_id)

            rows.append({
                "tool": tool,
                "level": level,
                "rule": rule_id,
                "message": msg,
                "location": loc,
                "region": region,
                "help": help_uri,
                "source": str(path),
            })

    return rows


def _esc(s: str) -> str:
    return html.escape(s or "")


def _sev_key(level: str) -> int:
    try:
        return SEV_ORDER.index(level)
    except ValueError:
        return 999


def _row_sort_key(r: Dict[str, str]) -> Tuple[Any, ...]:
    return (
        r.get("tool", ""),
        _sev_key(r.get("level", "")),
        r.get("rule", ""),
        r.get("location", ""),
        r.get("message", ""),
    )


def _counts(rows: List[Dict[str, str]]) -> Counter:
    c = Counter()
    for r in rows:
        c[_norm_level(r.get("level"))] += 1
    return c


def _top_rules(rows: List[Dict[str, str]], n: int = 10) -> List[Tuple[str, int]]:
    c = Counter()
    for r in rows:
        rule = r.get("rule") or "(no rule)"
        c[rule] += 1
    return c.most_common(n)


def html_fragment_report(rows: List[Dict[str, str]], title: str) -> str:
    rows_sorted = sorted(rows, key=_row_sort_key)
    counts = _counts(rows_sorted)

    # group tool -> severity -> list[rows]
    grouped: Dict[str, Dict[str, List[Dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for r in rows_sorted:
        grouped[r["tool"]][_norm_level(r.get("level"))].append(r)

    # overall header
    out: List[str] = []
    out.append(f"<h2>{_esc(title)}</h2>")

    out.append("<p>")
    out.append(
        f"{SEV_EMOJI['error']} <b>{counts.get('error',0)}</b> "
        f"{SEV_EMOJI['warning']} <b>{counts.get('warning',0)}</b> "
        f"{SEV_EMOJI['note']} <b>{counts.get('note',0)}</b> "
        f"{SEV_EMOJI['none']} <b>{counts.get('none',0)}</b> "
        f" • Total: <b>{len(rows_sorted)}</b>"
    )
    out.append("</p>")

    # quick “top rules” section (collapsed)
    top = _top_rules(rows_sorted, n=10)
    if top:
        out.append("<details>")
        out.append("<summary>Top rules (by count)</summary>")
        out.append("<ul>")
        for rule, n in top:
            out.append(f"<li><code>{_esc(rule)}</code> — {_esc(str(n))}</li>")
        out.append("</ul>")
        out.append("</details>")

    # per-tool collapsibles
    for tool in sorted(grouped.keys()):
        tool_rows = []
        for sev in SEV_ORDER:
            tool_rows.extend(grouped[tool].get(sev, []))
        tool_counts = _counts(tool_rows)

        out.append("<details open>")
        out.append(
            "<summary>"
            f"<b>{_esc(tool)}</b> — "
            f"{SEV_EMOJI['error']} {_esc(str(tool_counts.get('error',0)))} "
            f"{SEV_EMOJI['warning']} {_esc(str(tool_counts.get('warning',0)))} "
            f"{SEV_EMOJI['note']} {_esc(str(tool_counts.get('note',0)))} "
            f"(Total {_esc(str(len(tool_rows)))})"
            "</summary>"
        )

        # severity sections within tool
        for sev in SEV_ORDER:
            items = grouped[tool].get(sev, [])
            if not items:
                continue

            out.append("<details>")
            out.append(f"<summary>{SEV_EMOJI[sev]} {SEV_LABEL[sev]} ({len(items)})</summary>")
            out.append("<table>")
            out.append("<tr><th>Rule</th><th>Location</th><th>Message</th><th>Help</th></tr>")

            for r in items:
                rule = _esc(r.get("rule", ""))
                loc = _esc(r.get("location", ""))
                region = _esc(r.get("region", ""))
                msg = _esc(r.get("message", ""))
                help_uri = r.get("help", "") or ""
                help_cell = f"<a href=\"{_esc(help_uri)}\">link</a>" if help_uri else ""

                loc_cell = loc
                if region:
                    loc_cell = f"{loc}<br><small>{region}</small>" if loc else f"<small>{region}</small>"

                out.append(
                    "<tr>"
                    f"<td><code>{rule}</code></td>"
                    f"<td>{loc_cell}</td>"
                    f"<td>{msg}</td>"
                    f"<td>{help_cell}</td>"
                    "</tr>"
                )

            out.append("</table>")
            out.append("</details>")

        out.append("</details>")  # tool

    return "\n".join(out)


def write_step_summary(rows: List[Dict[str, str]], sarif_files: List[str]) -> None:
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not step_summary:
        return

    counts = _counts(rows)
    with open(step_summary, "a", encoding="utf-8") as f:
        f.write("## SARIF report\n\n")
        f.write(f"- SARIF files: {len(sarif_files)}\n")
        f.write(f"- Errors: {counts.get('error',0)}\n")
        f.write(f"- Warnings: {counts.get('warning',0)}\n")
        f.write(f"- Notes: {counts.get('note',0)}\n")
        f.write(f"- Total: {len(rows)}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="Security Report")
    ap.add_argument("--out", default="out/security-report.html")
    ap.add_argument("--glob", default="sarif/**/*.sarif")
    args = ap.parse_args()

    sarif_paths = [p for p in glob.glob(args.glob, recursive=True)]
    rows: List[Dict[str, str]] = []
    for p in sarif_paths:
        path = Path(p)
        if path.is_file():
            rows.extend(sarif_to_rows(path))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_fragment_report(rows, args.title), encoding="utf-8")

    write_step_summary(rows, sarif_paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())