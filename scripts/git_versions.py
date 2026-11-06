"""Release tags that exist upstream but were never published to Nexus.

Some projects tag a release on GitHub without ever deploying it to ALA's Nexus
(alerts 5.3.2, 6.2.0 and 6.2.1 at the time of writing), so a build driven only by
Nexus metadata never sees them. This module finds those tags and picks the Java
version they need, so build.py can build them from source.

Only tags *strictly newer than the newest Nexus release* count. Older tags that
Nexus lacks are pre-Nexus history that nobody asked for (ala-hub alone has 39 of
them); taking only what is newer keeps the set to genuine "published upstream,
missing here" releases.
"""

import hashlib
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

import artifact_resolver

CACHE_DIR = Path.home() / ".cache" / "la-docker-images" / "git-tags"
CACHE_SECONDS = 3600

# Java a Grails major needs at minimum. Grails 7 targets Java 17 and has no
# sourceCompatibility in alerts' build.gradle, so the version is the only signal.
_GRAILS_MIN_JAVA = {5: 11, 6: 11, 7: 17}

_JAVA_PATTERNS = [
    # sourceCompatibility = JavaVersion.VERSION_17 / VERSION_1_8 / '17' / 17
    r"(?:source|target)Compatibility\s*=?\s*JavaVersion\.VERSION_(\d+(?:_\d+)?)",
    r"(?:source|target)Compatibility\s*=?\s*['\"]?(\d+(?:\.\d+)?)['\"]?",
    r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)",
    r"jvmTarget\s*=?\s*['\"](\d+(?:\.\d+)?)['\"]",
    # Maven
    r"<java\.version>\s*(\d+(?:\.\d+)?)\s*<",
    r"<maven\.compiler\.(?:source|target|release)>\s*(\d+(?:\.\d+)?)\s*<",
    r"<release>\s*(\d+)\s*</release>",
]


def clean_version(tag):
    """v1.2.3 -> 1.2.3. Only strips a v that is followed by a digit."""
    return tag[1:] if re.match(r"^v\d", tag) else tag


def parse_ls_remote(output):
    """Tag names from `git ls-remote --tags`, de-referenced tags folded in."""
    tags = set()
    for line in output.splitlines():
        if "refs/tags/" not in line:
            continue
        tags.add(line.split("refs/tags/", 1)[1].strip().removesuffix("^{}"))
    return sorted(tags)


def list_remote_tags(repo_url, run=subprocess.run, now=time.time):
    """All tags of a repository, cached for an hour.

    `git ls-remote` rather than the GitHub API: it has no rate limit, and a full
    run asks for ~25 repositories.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / (hashlib.md5(repo_url.encode()).hexdigest() + ".json")
    if cache.exists() and now() - cache.stat().st_mtime <= CACHE_SECONDS:
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    result = run(
        ["git", "ls-remote", "--tags", repo_url],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git ls-remote {repo_url}: {result.stderr.strip()}")
    tags = parse_ls_remote(result.stdout)
    cache.write_text(json.dumps(tags))
    return tags


def git_only_releases(tags, nexus_versions):
    """[(version, tag)] for release tags newer than every Nexus release.

    Oldest first. Without any Nexus release there is nothing to be "newer than",
    and building a project's whole tag history is not what anyone wants, so the
    answer is empty.
    """
    released = artifact_resolver.sort_release_versions(nexus_versions)
    if not released:
        return []
    top = released[-1]

    by_version = {}
    for tag in tags:
        version = clean_version(tag)
        if artifact_resolver.sort_release_versions([version]):
            by_version.setdefault(version, tag)

    ordered = artifact_resolver.sort_release_versions([top, *by_version])
    newer = ordered[ordered.index(top) + 1 :]
    return [(v, by_version[v]) for v in newer if v in by_version and v not in nexus_versions]


def select_versions(nexus_versions, git_only, n):
    """The last `n` versions across Nexus and git-only, oldest first.

    Returns dicts {version, tag, source}; `tag` is None for Nexus. Same meaning
    of --n-tags as before, just over the combined list.
    """
    combined = [{"version": v, "tag": None, "source": "nexus"} for v in nexus_versions]
    combined += [{"version": v, "tag": t, "source": "git"} for v, t in git_only]
    order = {
        v: i
        for i, v in enumerate(
            artifact_resolver.sort_release_versions([c["version"] for c in combined])
        )
    }
    combined = [c for c in combined if c["version"] in order]
    combined.sort(key=lambda c: order[c["version"]])
    return combined[-n:]


def infer_java_version(files):
    """Java the sources need, from {filename: text} of the build files.

    Takes the highest signal found: an explicit sourceCompatibility/java.version,
    or the floor of the Grails major in gradle.properties. None when there is no
    signal at all.
    """
    found = []
    for text in files.values():
        for pattern in _JAVA_PATTERNS:
            for raw in re.findall(pattern, text or ""):
                raw = raw.replace("_", ".")
                parts = raw.split(".")
                # 1.8 / 1_8 mean Java 8
                major = int(parts[1]) if parts[0] == "1" and len(parts) > 1 else int(parts[0])
                found.append(major)

    properties = files.get("gradle.properties") or ""
    grails = re.search(r"^grailsVersion\s*=\s*(\d+)", properties, re.MULTILINE)
    if grails:
        floor = _GRAILS_MIN_JAVA.get(int(grails.group(1)))
        if floor:
            found.append(floor)

    return str(max(found)) if found else None


def fetch_build_files(repo_url, tag, fetch=None):
    """build.gradle, gradle.properties and pom.xml of a GitHub repo at a tag."""
    match = re.search(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?/?$", repo_url)
    if not match:
        return {}
    owner, repo = match.groups()

    def default_fetch(url):
        request = urllib.request.Request(url, headers={"User-Agent": "python-urllib"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", "replace")
        except Exception:
            return None

    fetch = fetch or default_fetch
    files = {}
    for name in ("build.gradle", "gradle.properties", "pom.xml"):
        text = fetch(f"https://raw.githubusercontent.com/{owner}/{repo}/{tag}/{name}")
        if text:
            files[name] = text
    return files


def resolve_java_version(from_dependencies, inferred):
    """The higher of dependencies.yaml's Java and the one the sources need.

    dependencies.yaml lags behind: its `>= 2.0.0` entry for alerts says Java 11,
    which also matches 6.2.1 (Grails 7, class files for Java 17). Returns
    (version, overridden) where overridden says the sources won.
    """
    candidates = [int(v) for v in (from_dependencies, inferred) if v]
    if not candidates:
        return None, False
    best = max(candidates)
    return str(best), bool(from_dependencies) and best > int(from_dependencies)
