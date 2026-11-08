#!/usr/bin/env python3
"""Publish Markdown articles with front matter to Qiita."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_BASE_URL = "https://qiita.com/api/v2"
ARTICLE_DIRECTORY = Path("qiita/articles")


class ArticleError(ValueError):
    """Raised when an article's front matter is invalid."""


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        return [parse_scalar(part) for part in value[1:-1].split(",") if part.strip()]
    return value


def parse_front_matter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ArticleError(f"{path}: front matter must start with '---'")

    end = text.find("\n---", 4)
    if end == -1:
        raise ArticleError(f"{path}: closing front matter delimiter is missing")

    front_matter_text = text[4:end]
    body = text[end + 4 :]
    if body.startswith("\n"):
        body = body[1:]

    metadata: dict[str, Any] = {}
    current_list_key: str | None = None
    for line_number, line in enumerate(front_matter_text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("-") and current_list_key:
            metadata.setdefault(current_list_key, []).append(parse_scalar(stripped[1:].strip()))
            continue
        if ":" not in line:
            raise ArticleError(f"{path}:{line_number}: expected 'key: value'")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise ArticleError(f"{path}:{line_number}: metadata key is empty")
        if raw_value.strip():
            metadata[key] = parse_scalar(raw_value)
            current_list_key = None
        else:
            metadata[key] = []
            current_list_key = key

    return metadata, body


def validate_article(metadata: dict[str, Any], body: str, path: Path) -> dict[str, Any]:
    title = metadata.get("title")
    tags = metadata.get("tags")
    if not isinstance(title, str) or not title.strip():
        raise ArticleError(f"{path}: 'title' is required")
    if not isinstance(tags, list) or not tags or not all(isinstance(tag, str) and tag.strip() for tag in tags):
        raise ArticleError(f"{path}: 'tags' must contain at least one tag")
    if len(tags) > 5:
        raise ArticleError(f"{path}: Qiita supports at most 5 tags")
    if not body.strip():
        raise ArticleError(f"{path}: article body is empty")

    for key in ("published", "private", "coediting", "tweet", "gist", "slide"):
        if key in metadata and not isinstance(metadata[key], bool):
            raise ArticleError(f"{path}: '{key}' must be true or false")
    organization_url_name = metadata.get("organization_url_name")
    if organization_url_name is not None and not isinstance(organization_url_name, str):
        raise ArticleError(f"{path}: 'organization_url_name' must be a string")
    qiita_id = metadata.get("qiita_id")
    if qiita_id is not None and (not isinstance(qiita_id, str) or not qiita_id.strip()):
        raise ArticleError(f"{path}: 'qiita_id' must be a non-empty string")

    return {
        "title": title.strip(),
        "tags": [{"name": tag.strip()} for tag in tags],
        "raw_body": body.rstrip() + "\n",
        "published": metadata.get("published", False),
        "private": metadata.get("private", False),
        "coediting": metadata.get("coediting", False),
        "tweet": metadata.get("tweet", False),
        "gist": metadata.get("gist", False),
        "slide": metadata.get("slide", False),
        "organization_url_name": organization_url_name,
        "qiita_id": qiita_id,
    }


def request_qiita(method: str, url: str, token: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "github-actions-qiita-publisher",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Qiita API returned HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Could not connect to Qiita API: {error.reason}") from error

    try:
        return json.loads(response_body)
    except json.JSONDecodeError as error:
        raise RuntimeError("Qiita API returned invalid JSON") from error


def article_payload(article: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: article[key]
        for key in ("title", "tags", "private", "coediting", "tweet", "gist", "slide", "raw_body")
    }
    if article.get("organization_url_name"):
        payload["organization_url_name"] = article["organization_url_name"]
    return payload


def payload_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "articles": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read state file {path}: {error}") from error
    if not isinstance(state, dict) or not isinstance(state.get("articles", {}), dict):
        raise RuntimeError(f"Invalid state file: {path}")
    state.setdefault("version", 1)
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--state", type=Path, default=Path(".qiita/state.json"))
    parser.add_argument("--dry-run", action="store_true", help="Validate and show actions without calling Qiita")
    args = parser.parse_args()

    root = args.root.resolve()
    article_directory = root / ARTICLE_DIRECTORY
    state_path = args.state if args.state.is_absolute() else root / args.state
    if not article_directory.exists():
        print(f"No article directory found: {article_directory}")
        return 0

    token = os.environ.get("QIITA_TOKEN")
    state = load_state(state_path)
    registered_articles = state["articles"]
    changed = False
    article_paths = sorted(article_directory.glob("*.md"))
    if not article_paths:
        print(f"No Markdown articles found in {article_directory}")
        return 0

    api_base_url = os.environ.get("QIITA_API_URL", API_BASE_URL).rstrip("/")
    for article_path in article_paths:
        relative_path = article_path.relative_to(root).as_posix()
        metadata, body = parse_front_matter(article_path.read_text(encoding="utf-8"), article_path)
        article = validate_article(metadata, body, article_path)
        if not article["published"]:
            print(f"SKIP  {relative_path} (published: false)")
            continue

        payload = article_payload(article)
        content_hash = payload_hash(payload)
        previous = registered_articles.get(relative_path, {})
        if not isinstance(previous, dict):
            previous = {}
        item_id = article["qiita_id"] or previous.get("id")
        if item_id and previous.get("id") == item_id and previous.get("content_hash") == content_hash:
            print(f"SKIP  {relative_path} (unchanged: {previous.get('url', item_id)})")
            continue

        action = "update" if item_id else "create"
        if args.dry_run:
            print(f"DRY   {action} {relative_path}" + (f" -> {item_id}" if item_id else ""))
            continue

        if not token:
            raise RuntimeError("QIITA_TOKEN environment variable is not set")

        if item_id:
            result = request_qiita("PUT", f"{api_base_url}/items/{item_id}", token, payload)
        else:
            result = request_qiita("POST", f"{api_base_url}/items", token, payload)

        result_id = result.get("id")
        result_url = result.get("url")
        if not result_id or not result_url:
            raise RuntimeError(f"Qiita API response did not include id and url for {relative_path}")
        registered_articles[relative_path] = {
            "id": result_id,
            "url": result_url,
            "content_hash": content_hash,
            "title": article["title"],
        }
        changed = True
        print(f"OK    {action}d {relative_path}: {result_url}")

    if changed and not args.dry_run:
        save_state(state_path, state)
        print(f"Saved publication state to {state_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ArticleError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
