#!/usr/bin/env python3
"""
SARIF → HTML fragment report for GitHub Actions step summary.

Features:
- De-duplicates results (SARIF fingerprints → fallback heuristics)
- Strips runner/workspace prefixes from paths
- Adds GitHub links to exact file + line
- Collapsible sections (tool → severity)
- Emoji severity markers
- Emits HTML fragment (summary-safe; no <style>/<head>)
"""

import argparse
import glob
import html
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


SEV_ORDER = ["error", "warning", "note", "none"]
SEV_EMOJI = {
    "error": "🔴",
    "warning": "🟡",
    "note": "🔵",
    "none": "⚫",
}
SEV_LABEL = {"error": "Errors", "warning": "Warnings", "note": "Notes", "none": "None"}


def _esc(s: str) -> str:
    return html.escape(s or "")


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


def _msg_text(res: Dict[str, Any]) -> str:
    return ((res.get("message") or {}).get("text") or "").strip()


def _best_fingerprint(res: Dict[str, Any]) -> str:
    if res.get("correlationGuid"):
        return f"cg:{res['correlationGuid']}"

    for k in ("fingerprints", "partialFingerprints"):
        d = res.get(k)
        if isinstance(d, dict) and d:
            items = "|".join(f"{x}={d[x]}" for x in sorted(d))
            return f"{k}:{items}"

    return ""


def _normalize_artifact_uri(uri: str) -> str:
    """
    Strip runner paths and return repo-relative path.

    Handles:
      - file:///home/runner/work/repo/repo/...
      - /home/runner/work/repo/repo/...
      - relative paths
    """
    if not uri:
        return ""

    # strip file://
    if uri.startswith("file://"):
        uri = urlparse(uri).path or ""

    uri = uri.replace("\\", "/")

    repo = (os.environ.get("GITHUB_REPOSITORY") or "").split("/")[-1]
    workspace = (os.environ.get("GITHUB_WORKSPACE") or "").replace("\\", "/").rstrip("/")

    # Strip workspace prefix if present
    if workspace and uri.startswith(workspace + "/"):
        uri = uri[len(workspace) + 1 :]

    # Strip everything before "/<repo>/"
    marker = f"/{repo}/"
    if marker in uri:
        uri = uri.split(marker, 1)[1]

    # Remove leading slashes
    while uri.startswith("/"):
        uri = uri[1:]

    return uri


def _extract_location(res: Dict[str, Any]) -> Tuple[str, Optional[int], str]:
    locs = res.get("locations") or []
    if not locs:
        return ("", None, "")

    pl = (locs[0].get("physicalLocation") or {})
    uri = (pl.get("artifactLocation") or {}).get("uri") or ""
    path = _normalize_artifact_uri(uri)

    region = pl.get("region") or {}
    sl = region.get("startLine")
    el = region.get("endLine")

    line = sl if isinstance(sl, int) else None

    if isinstance(sl, int):
        region_s = f"L{sl}" if not isinstance(el, int) or el == sl else f"L{sl}-L{el}"
    else:
        region_s = ""

    return (path, line, region_s)


def _github_line_url(path: str, line: Optional[int]) -> str:
    server = (os.environ.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    sha = os.environ.get("GITHUB_SHA") or ""
    if not (repo and sha and path):
        return ""
    url = f"{server}/{repo}/blob/{sha}/{path}"
    if line:
        url += f"#L{line}"
    return url


def sarif_to_rows(path: Path) -> List[Dict[str, str]]:
    sarif = _read_json(path)
    rows: List[Dict[str, str]] = []

    for run in sarif.get("runs") or []:
        tool = _tool_name(run)
        for res in run.get("results") or []:
            level = _norm_level(res.get("level"))
            rule = res.get("ruleId") or ""
            msg = _msg_text(res)

            path2, line, region = _extract_location(res)
            help_uri = _rule_help(run, rule)
            gh_url = _github_line_url(path2, line)

            fp = _best_fingerprint(res)
            dedupe = (
                f"{tool}|{rule}|{fp}"
                if fp
                else f"{tool}|{rule}|{path2}|{line or ''}|{level}"
            )

            rows.append(
                {
                    "tool": tool,
                    "level": level,
                    "rule": rule,
                    "message": msg,
                    "path": path2,
                    "line": str(line) if line else "",
                    "region": region,
                    "help": help_uri,
                    "gh_url": gh_url,
                    "dedupe_key": dedupe,
                }
            )

    return rows


def dedupe_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    out = []
    for r in rows:
        k = r["dedupe_key"]
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def html_fragment_report(rows: List[Dict[str, str]], title: str) -> str:
    counts = Counter(r["level"] for r in rows)
    grouped = defaultdict(lambda: defaultdict(list))
    for r in rows:
        grouped[r["tool"]][r["level"]].append(r)

    out: List[str] = []
    out.append(f"<h2>{_esc(title)}</h2>")
    out.append(
        f"<p>{SEV_EMOJI['error']} {counts.get('error',0)} "
        f"{SEV_EMOJI['warning']} {counts.get('warning',0)} "
        f"{SEV_EMOJI['note']} {counts.get('note',0)} "
        f"Total: {len(rows)}</p>"
    )

    for tool in sorted(grouped):
        tc = Counter(r["level"] for v in grouped[tool].values() for r in v)
        out.append("<details open>")
        out.append(
            f"<summary>🧰 <b>{_esc(tool)}</b> — "
            f"{SEV_EMOJI['error']} {tc.get('error',0)} "
            f"{SEV_EMOJI['warning']} {tc.get('warning',0)} "
            f"{SEV_EMOJI['note']} {tc.get('note',0)}</summary>"
        )

        for sev in SEV_ORDER:
            items = grouped[tool].get(sev, [])
            if not items:
                continue

            out.append("<details>")
            out.append(f"<summary>{SEV_EMOJI[sev]} {SEV_LABEL[sev]} ({len(items)})</summary>")
            out.append("<table>")
            out.append("<tr><th>Rule</th><th>Location</th><th>Link</th><th>Message</th></tr>")

            for r in items:
                loc = _esc(r["path"] + (f":{r['line']}" if r["line"] else ""))
                link = f"<a href=\"{_esc(r['gh_url'])}\">🔗</a>" if r["gh_url"] else ""
                out.append(
                    "<tr>"
                    f"<td><code>{_esc(r['rule'])}</code></td>"
                    f"<td>{loc}<br><small>{_esc(r['region'])}</small></td>"
                    f"<td>{link}</td>"
                    f"<td>{_esc(r['message'])}</td>"
                    "</tr>"
                )

            out.append("</table></details>")
        out.append("</details>")

    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="Security Report")
    ap.add_argument("--out", default="out/security-report.html")
    ap.add_argument("--glob", default="sarif/**/*.sarif")
    args = ap.parse_args()

    rows: List[Dict[str, str]] = []
    for p in glob.glob(args.glob, recursive=True):
        if Path(p).is_file():
            rows.extend(sarif_to_rows(Path(p)))

    rows = dedupe_rows(rows)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_fragment_report(rows, args.title), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
