#!/usr/bin/env python3
import argparse, json, html, os, glob
from pathlib import Path
from typing import Any, Dict, List, Tuple

def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def _norm_level(level: str) -> str:
    if not level:
        return "warning"
    level = level.lower()
    if level == "info":
        return "note"
    return level

def _pick_location(result: Dict[str, Any]) -> Tuple[str, str]:
    locs = result.get("locations") or []
    if not locs:
        return ("", "")
    pl = (locs[0].get("physicalLocation") or {})
    arti = (pl.get("artifactLocation") or {}).get("uri") or ""
    region = pl.get("region") or {}
    sl, el = region.get("startLine"), region.get("endLine")
    sc, ec = region.get("startColumn"), region.get("endColumn")

    loc = arti
    region_s = ""
    if sl is not None:
        loc = f"{arti}:{sl}" if arti else f"{sl}"
        region_s = f"L{sl}" + (f"-L{el}" if el and el != sl else "")
        if sc is not None:
            region_s += f" C{sc}" + (f"-C{ec}" if ec and ec != sc else "")
    return (loc, region_s)

def _tool_name(run: Dict[str, Any]) -> str:
    tool = (run.get("tool") or {}).get("driver") or {}
    return tool.get("name") or "tool"

def _rule_help(run: Dict[str, Any], rule_id: str) -> str:
    rules = ((run.get("tool") or {}).get("driver") or {}).get("rules") or []
    for r in rules:
        if (r.get("id") or "") == rule_id:
            return r.get("helpUri") or ""
    return ""

def sarif_to_rows(path: Path) -> List[Dict[str, str]]:
    sarif = _read_json(path)
    rows: List[Dict[str, str]] = []
    for run in sarif.get("runs") or []:
        tool = _tool_name(run)
        for res in run.get("results") or []:
            level = _norm_level(res.get("level") or "warning")
            rule_id = res.get("ruleId") or ""
            msg = ((res.get("message") or {}).get("text") or "").strip()
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

def html_report(rows: List[Dict[str, str]], title: str) -> str:
    counts = {"error": 0, "warning": 0, "note": 0, "none": 0}
    for r in rows:
        counts[r["level"]] = counts.get(r["level"], 0) + 1

    def esc(s: str) -> str:
        return html.escape(s or "")

    def sev_key(lvl: str) -> int:
        return {"error": 0, "warning": 1, "note": 2, "none": 3}.get(lvl, 9)

    rows_sorted = sorted(
        rows,
        key=lambda r: (r["tool"], sev_key(r["level"]), r["rule"], r["location"], r["message"])
    )

    out = []
    out.append("<!doctype html>")
    out.append("<meta charset='utf-8'>")
    out.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    out.append(f"<title>{esc(title)}</title>")
    out.append("""
<style>
body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; }
h1 { margin: 0 0 8px 0; font-size: 20px; }
.summary { margin: 12px 0 18px 0; }
.badge { display:inline-block; padding:2px 10px; border-radius: 999px; border:1px solid #ddd; margin-right: 8px; font-size: 12px; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #e5e5e5; padding: 8px; font-size: 12px; vertical-align: top; }
th { text-align: left; background: #fafafa; position: sticky; top: 0; }
small { color: #666; }
</style>
""")
    out.append(f"<h1>{esc(title)}</h1>")
    out.append(
        "<div class='summary'>"
        f"<span class='badge'>Errors: <b>{counts.get('error',0)}</b></span>"
        f"<span class='badge'>Warnings: <b>{counts.get('warning',0)}</b></span>"
        f"<span class='badge'>Notes: <b>{counts.get('note',0)}</b></span>"
        f"<span class='badge'>Total: <b>{len(rows)}</b></span>"
        "</div>"
    )
    out.append("<table>")
    out.append("<tr><th>Tool</th><th>Level</th><th>Rule</th><th>Location</th><th>Message</th><th>Help</th></tr>")
    for r in rows_sorted:
        help_cell = f"<a href='{esc(r['help'])}'>{esc(r['help'])}</a>" if r["help"] else ""
        out.append(
            "<tr>"
            f"<td>{esc(r['tool'])}</td>"
            f"<td>{esc(r['level'])}</td>"
            f"<td>{esc(r['rule'])}</td>"
            f"<td>{esc(r['location'])}<br><small>{esc(r['region'])}</small></td>"
            f"<td>{esc(r['message'])}</td>"
            f"<td>{help_cell}</td>"
            "</tr>"
        )
    out.append("</table>")
    out.append("<p><small>Generated from SARIF artifacts.</small></p>")
    return "\n".join(out)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="Security Report")
    ap.add_argument("--out", default="out/security-report.html")
    ap.add_argument("--glob", default="sarif/**/*.sarif")
    args = ap.parse_args()

    paths = [Path(p) for p in glob.glob(args.glob, recursive=True)]
    rows: List[Dict[str, str]] = []
    for p in paths:
        if p.is_file():
            rows.extend(sarif_to_rows(p))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_report(rows, args.title), encoding="utf-8")

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        counts = {"error": 0, "warning": 0, "note": 0, "none": 0}
        for r in rows:
            counts[r["level"]] = counts.get(r["level"], 0) + 1
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write("## SARIF report\n")
            f.write(f"- SARIF files: {len(paths)}\n")
            f.write(f"- Errors: {counts.get('error',0)}\n")
            f.write(f"- Warnings: {counts.get('warning',0)}\n")
            f.write(f"- Notes: {counts.get('note',0)}\n")
            f.write(f"- Total: {len(rows)}\n")
            f.write(f"- HTML: `{out_path}`\n")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
