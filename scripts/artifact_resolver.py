#!/usr/bin/env python3
"""Work out which Nexus artifact to containerise for a given version.

services-definition.yml declares `classifier` and `extension` once per service,
but the packaging has changed over time and differs between services: apikey,
doi-service and userdetails moved from `exec:jar` to `exec:war`, logger-service
published a plain `.war` until 4.3, biocache-service ships a Gradle `.module`
alongside its war. With --n-tags=1 that never showed, because the newest version
always matches what is declared. Reaching back ten versions, 26 of 214 pairs do
not, which is what aborted the whole build.

Guessing candidate filenames would break again the next time the packaging moves
(ALA is migrating to next-gen/k8s), so this asks Nexus what actually exists and
then checks the candidates are runnable. Both answers are authoritative:

  1. The assets API lists every file published under a version, with its real
     classifier and extension.
  2. A JAR/WAR is a ZIP, so `zipfile` over HTTP Range requests reads just
     META-INF/MANIFEST.MF and looks for Main-Class. The template runs
     `java -jar app.<ext>`, which needs one. Costs ~40-65 KB in ~6 requests
     against artifacts of 75-137 MB.

Step 2 is not redundant: apikey 1.3 publishes both `-exec.jar` and `.war`, and
only the jar is runnable, so picking on name alone would have published an image
that dies at startup.

Stdlib only, so the Validate Sync stage can run its tests before the venv exists.
"""

import hashlib
import json
import io
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

NEXUS_BASE = "https://nexus.ala.org.au"
DEFAULT_GROUP_ID = "au.org.ala"
HTTP_TIMEOUT = 30

# Resolving --n-tags=10 means a few hundred lookups against Nexus in one go, and
# under that load some requests come back short or reset. Whatever the reason, a
# failed read must not be mistaken for a verdict, so retry before concluding.
HTTP_RETRIES = 3
HTTP_BACKOFF_SECONDS = 1.0

CACHE_DIR = Path.home() / ".cache" / "la-docker-images" / "artifacts"

# Published versions never change, so a hit is good forever and needs no TTL.
# Only definitive answers are cached; a network failure must not freeze into one.

# Checksums and metadata, not things you can run.
_SKIPPED_EXTENSIONS = {"pom", "module", "md5", "sha1", "sha256", "sha512", "asc"}
_SKIPPED_CLASSIFIERS = {"sources", "javadoc", "tests", "test-sources"}

# Classifiers that mean "self-contained, has an embedded server": Spring Boot and
# Grails use `exec`, Gradle's shadow plugin (pdfgen) uses `all`.
_BOOTABLE_CLASSIFIERS = ("exec", "all", "boot")

_MAIN_CLASS_RE = re.compile(r"^Main-Class:", re.MULTILINE)


class TransientResolveError(Exception):
    """Nexus could not be reached. Distinct from 'this version has nothing usable'
    so the caller can retry instead of recording a verdict."""


class _HttpRangeFile(io.RawIOBase):
    """A seekable file over HTTP Range requests, so zipfile can read the central
    directory at the end of a remote archive without downloading the rest."""

    def __init__(self, url, timeout=HTTP_TIMEOUT):
        self.url = url
        self.timeout = timeout
        self.pos = 0
        self.requests = 0
        self.bytes_read = 0
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            self.size = int(response.headers["content-length"])

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self.pos + offset
        else:
            position = self.size + offset
        # zipfile hunts for the end-of-central-directory record by seeking back
        # 64 KB from the end, which underflows on an archive smaller than that
        # and would send a malformed 'bytes=-1234-5678'.
        self.pos = max(0, position)
        return self.pos

    def read(self, size=-1):
        if size < 0:
            size = self.size - self.pos
        if size == 0 or self.pos >= self.size:
            return b""
        end = min(self.pos + size, self.size) - 1
        request = urllib.request.Request(
            self.url, headers={"Range": f"bytes={self.pos}-{end}"}
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = response.read()
        self.requests += 1
        self.bytes_read += len(data)
        self.pos += len(data)
        return data


def _repository_for(version):
    return "snapshots" if "SNAPSHOT" in str(version) else "releases"


def artifact_url(artifact_id, version, classifier, extension, group_id=DEFAULT_GROUP_ID):
    """The Nexus download URL for one artifact: name is
    artifact-version[-classifier].extension."""
    name = f"{artifact_id}-{version}"
    if classifier:
        name += f"-{classifier}"
    group_path = group_id.replace(".", "/")
    return (
        f"{NEXUS_BASE}/repository/{_repository_for(version)}/"
        f"{group_path}/{artifact_id}/{version}/{name}.{extension}"
    )


def list_assets(artifact_id, version, group_id=DEFAULT_GROUP_ID):
    """Every deployable file published under this version, newest API first.

    Returns dicts of {classifier, extension, url}. Checksums, poms, Gradle module
    metadata, sources and javadoc are dropped -- none of them is a thing you run.
    """
    query = urllib.parse.urlencode(
        {
            "repository": _repository_for(version),
            "maven.groupId": group_id,
            "maven.artifactId": artifact_id,
            "maven.baseVersion": version,
        }
    )
    url = f"{NEXUS_BASE}/service/rest/v1/search/assets?{query}"

    payload = None
    last_error = None
    for retry in range(HTTP_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except Exception as exc:
            last_error = exc
            if retry < HTTP_RETRIES - 1:
                time.sleep(HTTP_BACKOFF_SECONDS * (2**retry))
    if payload is None:
        raise TransientResolveError(
            f"could not list assets for {artifact_id} {version}: {last_error}"
        )

    assets = []
    for item in payload.get("items", []):
        maven = item.get("maven2", {})
        extension = maven.get("extension", "")
        classifier = maven.get("classifier") or ""
        if extension in _SKIPPED_EXTENSIONS or classifier in _SKIPPED_CLASSIFIERS:
            continue
        # `foo.jar.md5` arrives as extension "jar.md5".
        if any(extension.endswith(f".{skip}") for skip in _SKIPPED_EXTENSIONS):
            continue
        assets.append(
            {
                "classifier": classifier,
                "extension": extension,
                "url": item.get("downloadUrl")
                or artifact_url(artifact_id, version, classifier, extension, group_id),
            }
        )
    return assets


def read_manifest(url):
    """META-INF/MANIFEST.MF out of a remote JAR/WAR, or None if it has none.

    Retries before giving an answer. A Range read that comes back short raises
    BadZipFile, which is indistinguishable from a genuinely corrupt archive, and
    treating that as 'nothing to run here' would drop a good version -- and cache
    the mistake. Only a failure that survives every attempt is taken as real, and
    anything that is not a zip problem is reported as transient instead.
    """

    def attempt():
        archive = zipfile.ZipFile(_HttpRangeFile(url))
        if "META-INF/MANIFEST.MF" not in archive.namelist():
            return None
        return archive.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")

    last_error = None
    for retry in range(HTTP_RETRIES):
        try:
            return attempt()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None  # The artifact is simply not there.
            last_error = exc
        except zipfile.BadZipFile as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc
        if retry < HTTP_RETRIES - 1:
            time.sleep(HTTP_BACKOFF_SECONDS * (2**retry))

    if isinstance(last_error, zipfile.BadZipFile):
        # Unreadable as a zip on every attempt, so it really is not a jar/war.
        return None
    raise TransientResolveError(f"could not read manifest of {url}: {last_error}")


def manifest_declares_main_class(manifest):
    """Whether a MANIFEST.MF text declares a Main-Class."""
    if not manifest:
        return False
    # MANIFEST.MF wraps at 72 bytes and continues the value on the next line with
    # a leading space, so unfold first: a wrapped header would otherwise read as a
    # continuation line and the one we want could be missed.
    unfolded = manifest.replace("\r\n", "\n").replace("\r", "\n").replace("\n ", "")
    return bool(_MAIN_CLASS_RE.search(unfolded))


def is_bootable(url):
    """Whether `java -jar` can start this artifact, i.e. it declares a Main-Class.

    Deliberately not 'is it Spring Boot': pdfgen ships a shadow jar whose
    Main-Class is its own, and next-gen may package differently again.
    """
    return manifest_declares_main_class(read_manifest(url))


def _preference_rank(asset, declared, preference):
    """Lower sorts first. Only ever applied to artifacts already known to boot."""
    classifier = asset["classifier"]
    extension = asset["extension"]

    if preference:
        for index, wanted in enumerate(preference):
            wanted_classifier, _, wanted_extension = wanted.partition(":")
            if classifier == wanted_classifier and extension == wanted_extension:
                return index

    # What the service declares wins, so recent versions keep behaving as they do
    # today and this only changes the ones that were failing.
    if declared and (classifier, extension) == declared:
        return 100

    if classifier in _BOOTABLE_CLASSIFIERS:
        return 200 if extension == "jar" else 201
    if not classifier:
        return 300 if extension == "jar" else 301
    return 400


def _cache_path(artifact_id, version, group_id):
    key = f"{group_id}:{artifact_id}:{version}"
    return CACHE_DIR / (hashlib.md5(key.encode("utf-8")).hexdigest() + ".json")


def _load_cached(path):
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return None


def _save_cached(path, payload):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    except Exception:
        pass  # A cold cache is slow, not wrong.


def resolve_artifact(
    artifact_id,
    version,
    declared_classifier="",
    declared_extension="war",
    group_id=DEFAULT_GROUP_ID,
    preference=None,
    use_cache=True,
):
    """Pick the artifact to containerise for one (artifact, version).

    Returns {classifier, extension, url, candidates} for the best runnable
    artifact, or None when the version publishes nothing that `java -jar` can
    start. Raises TransientResolveError if Nexus could not be reached, so a
    network blip is never mistaken for a missing artifact.
    """
    cache_path = _cache_path(artifact_id, version, group_id)
    if use_cache:
        cached = _load_cached(cache_path)
        if cached is not None:
            return cached.get("choice")

    declared = (declared_classifier or "", declared_extension or "war")
    assets = list_assets(artifact_id, version, group_id)

    # Rank first, then probe in order and stop at the first artifact that boots.
    # Probing every candidate would double the requests for the versions that
    # publish two, which is exactly the set this has to resolve.
    ranked = sorted(assets, key=lambda a: _preference_rank(a, declared, preference))
    candidates = [f"{a['classifier']}:{a['extension']}" for a in assets]

    choice = None
    for asset in ranked:
        if is_bootable(asset["url"]):
            choice = {
                "classifier": asset["classifier"],
                "extension": asset["extension"],
                "url": asset["url"],
                "candidates": candidates,
            }
            break

    if use_cache:
        _save_cached(cache_path, {"choice": choice})
    return choice
