#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

MARKER_PREFIX = "<!-- pr-comment:"
MARKER_SUFFIX = " -->"

def eprint(*a: Any) -> None:
    print(*a, file=sys.stderr)

def api_request(method: str, url: str, token: str, data: Optional[dict] = None) -> Tuple[int, dict, Dict[str, str]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "pr-comment-action",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, method=method, headers=headers, data=body)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return resp.status, json.loads(raw), dict(resp.headers.items())
    except urllib.error.HTTPError as ex:
        raw = ex.read().decode("utf-8") if ex.fp else ""
        msg = raw or str(ex)
        eprint(f"GitHub API error: {ex.code} {method} {url}\n{msg}")
        return ex.code, (json.loads(raw) if raw.startswith("{") else {"error": msg}), dict(ex.headers.items() if ex.headers else {})
    except Exception as ex:
        eprint(f"Request failed: {method} {url}: {ex}")
        raise

def parse_repo() -> Tuple[str, str, str]:
    server = (os.environ.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    if "/" not in repo:
        raise SystemExit("GITHUB_REPOSITORY not set or invalid")
    owner, name = repo.split("/", 1)
    api_base = server.replace("https://github.com", "https://api.github.com")
    # For GHES, server URL differs; API is usually <server>/api/v3
    if "github.com" not in server:
        api_base = server.rstrip("/") + "/api/v3"
    return api_base, owner, name

def load_event() -> dict:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def derive_pr_number(event: dict) -> Optional[int]:
    pr = event.get("pull_request")
    if isinstance(pr, dict) and isinstance(pr.get("number"), int):
        return pr["number"]
    # Some events nest it under "issue" for issue_comment; not what we want here.
    return None

def marker_for(name: str) -> str:
    return f"{MARKER_PREFIX}{name}{MARKER_SUFFIX}"

def build_body(name: str, content: str) -> str:
    # Put marker at top so we can find it reliably.
    return f"{marker_for(name)}\n{content}\n"

def read_body(body: str, body_file: str) -> str:
    if body_file:
        with open(body_file, "r", encoding="utf-8") as f:
            return f.read()
    return body

def find_existing_comment(api_base: str, owner: str, repo: str, pr_number: int, token: str, marker: str) -> Optional[dict]:
    # PR comments are issue comments on the PR issue
    per_page = 100
    page = 1
    while True:
        url = f"{api_base}/repos/{owner}/{repo}/issues/{pr_number}/comments?per_page={per_page}&page={page}"
        status, payload, headers = api_request("GET", url, token)
        if status != 200:
            raise SystemExit(f"Failed to list comments (status {status})")
        if not isinstance(payload, list):
            raise SystemExit("Unexpected comments payload")
        for c in payload:
            body = c.get("body") or ""
            if marker in body:
                return c
        if len(payload) < per_page:
            return None
        page += 1

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comment-name", required=True)
    ap.add_argument("--body", default="")
    ap.add_argument("--body-file", default="")
    ap.add_argument("--pr-number", default="")
    ap.add_argument("--mode", default="upsert", choices=["upsert", "update", "create"])
    ap.add_argument("--token", required=True)
    args = ap.parse_args()

    content = read_body(args.body, args.body_file).strip()
    if not content:
        raise SystemExit("Empty comment body (provide body or body_file)")

    event = load_event()
    pr_number: Optional[int] = None
    if args.pr_number.strip():
        pr_number = int(args.pr_number.strip())
    else:
        pr_number = derive_pr_number(event)

    if not pr_number:
        raise SystemExit("Could not determine PR number. Provide inputs.pr_number or run on pull_request event.")

    api_base, owner, repo = parse_repo()
    marker = marker_for(args.comment_name)
    final_body = build_body(args.comment_name, content)

    existing = find_existing_comment(api_base, owner, repo, pr_number, args.token, marker)

    if args.mode == "create":
        url = f"{api_base}/repos/{owner}/{repo}/issues/{pr_number}/comments"
        status, payload, _ = api_request("POST", url, args.token, {"body": final_body})
        if status not in (201,):
            raise SystemExit(f"Failed to create comment (status {status})")
        eprint(f"Created comment id={payload.get('id')}")
        return 0

    if existing:
        if args.mode in ("upsert", "update"):
            comment_id = existing.get("id")
            url = f"{api_base}/repos/{owner}/{repo}/issues/comments/{comment_id}"
            status, payload, _ = api_request("PATCH", url, args.token, {"body": final_body})
            if status not in (200,):
                raise SystemExit(f"Failed to update comment (status {status})")
            eprint(f"Updated comment id={payload.get('id')}")
            return 0
    else:
        if args.mode == "update":
            eprint("No existing named comment found; mode=update so nothing to do.")
            return 0
        # upsert path
        url = f"{api_base}/repos/{owner}/{repo}/issues/{pr_number}/comments"
        status, payload, _ = api_request("POST", url, args.token, {"body": final_body})
        if status not in (201,):
            raise SystemExit(f"Failed to create comment (status {status})")
        eprint(f"Created comment id={payload.get('id')}")
        return 0

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
