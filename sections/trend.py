import base64

import requests
from bs4 import BeautifulSoup

from config import get_env
from sections.common import USER_AGENT

GITHUB_TRENDING_URL = "https://github.com/trending"
GITHUB_API_BASE = "https://api.github.com"


def _build_github_headers(api: bool = False) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    github_key = get_env("GITHUB_KEY").strip()
    if github_key:
        headers["Authorization"] = f"Bearer {github_key}"

    if api:
        headers["Accept"] = "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"

    return headers


def get_trending(limit: int = 3) -> list[dict[str, str]]:
    response = requests.get(
        GITHUB_TRENDING_URL,
        headers=_build_github_headers(),
        timeout=15,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    repos: list[dict[str, str]] = []

    for repo in soup.select("article.Box-row")[:limit]:
        title = repo.h2.text.strip().replace("\n", "").replace(" ", "")
        desc = repo.p.text.strip() if repo.p else ""
        repos.append(
            {
                "full_name": title,
                "url": f"https://github.com/{title}",
                "desc": desc,
            }
        )

    return repos


def fetch_repo_readme(full_name: str, max_chars: int = 8000) -> str:
    url = f"{GITHUB_API_BASE}/repos/{full_name}/readme"
    response = requests.get(
        url,
        headers=_build_github_headers(api=True),
        timeout=15,
    )

    if response.status_code != 200:
        return ""

    try:
        data = response.json()
        content = data.get("content", "")
        encoding = data.get("encoding", "")
        if encoding == "base64" and content:
            text = base64.b64decode(content).decode("utf-8", errors="ignore")
        else:
            text = ""
    except Exception:
        return ""

    return text[:max_chars]


def build_trend_payload(repos: list[dict[str, str]]) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for repo in repos:
        readme = fetch_repo_readme(repo["full_name"])
        payload.append(
            {
                "full_name": repo["full_name"],
                "url": repo["url"],
                "desc": repo["desc"],
                "readme_excerpt": readme,
            }
        )
    return payload
