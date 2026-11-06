#!/usr/bin/env python3
"""Unit tests for artifact resolution and build failure isolation.

Stdlib only and offline: the Validate Sync stage runs before the venv exists, so
these cannot import pytest, yaml or anything else from requirements.txt, and they
cannot reach Nexus. The asset payloads below are the real responses measured from
nexus.ala.org.au for the versions that broke build #213.

Run: python3 scripts/test_build_logic.py
"""

import io
import json
import pathlib
import re
import os
import sys
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import artifact_resolver as ar


def make_jar(entries):
    """A real ZIP in memory. entries maps archive path -> contents."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


SPRING_BOOT_WAR = make_jar(
    {
        "META-INF/MANIFEST.MF": (
            "Manifest-Version: 1.0\r\n"
            "Main-Class: org.springframework.boot.loader.WarLauncher\r\n"
            "Start-Class: au.org.ala.userdetails.Application\r\n"
            "Spring-Boot-Version: 2.7.0\r\n"
            "\r\n"
        ),
        "WEB-INF/classes/app.properties": "x=1",
    }
)

# pdfgen's shadow jar: runnable, but nothing to do with Spring.
SHADOW_JAR = make_jar(
    {
        "META-INF/MANIFEST.MF": "Manifest-Version: 1.0\r\nMain-Class: au.org.ala.PdfGen\r\n\r\n",
    }
)

# What biocache-service 3.4.0 ships: a war with no embedded server.
PLAIN_WAR = make_jar(
    {
        "META-INF/MANIFEST.MF": "Manifest-Version: 1.0\r\nImplementation-Title: biocache\r\n\r\n",
        "WEB-INF/web.xml": "<web-app/>",
    }
)

NO_MANIFEST_JAR = make_jar({"some/class.txt": "x"})


class RangeHandler(BaseHTTPRequestHandler):
    """Serves FILES with Range support, which SimpleHTTPRequestHandler lacks and
    _HttpRangeFile depends on."""

    FILES = {}

    def log_message(self, *args):
        pass

    def _body(self):
        return self.FILES.get(self.path)

    def do_HEAD(self):
        body = self._body()
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.end_headers()

    def do_GET(self):
        body = self._body()
        if body is None:
            self.send_error(404)
            return
        header = self.headers.get("Range")
        if not header:
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        spec = header.split("=", 1)[1]
        start_text, _, end_text = spec.partition("-")
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else len(body) - 1
        else:  # bytes=-N, the trailing N bytes
            start = max(0, len(body) - int(end_text))
            end = len(body) - 1
        chunk = body[start : end + 1]
        self.send_response(206)
        self.send_header("content-length", str(len(chunk)))
        self.send_header("content-range", f"bytes {start}-{end}/{len(body)}")
        self.end_headers()
        self.wfile.write(chunk)


class BootabilityOverHttpTest(unittest.TestCase):
    """Exercises the real path: zipfile reading a remote archive's central
    directory and manifest through Range requests."""

    @classmethod
    def setUpClass(cls):
        RangeHandler.FILES = {
            "/boot.war": SPRING_BOOT_WAR,
            "/shadow.jar": SHADOW_JAR,
            "/plain.war": PLAIN_WAR,
            "/nomanifest.jar": NO_MANIFEST_JAR,
            "/notazip.war": b"this is not a zip",
        }
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_spring_boot_war_is_bootable(self):
        self.assertTrue(ar.is_bootable(f"{self.base}/boot.war"))

    def test_shadow_jar_is_bootable(self):
        # The check is Main-Class, not "is it Spring Boot": pdfgen would fail a
        # Spring-specific test while being perfectly runnable.
        self.assertTrue(ar.is_bootable(f"{self.base}/shadow.jar"))

    def test_plain_war_is_not_bootable(self):
        self.assertFalse(ar.is_bootable(f"{self.base}/plain.war"))

    def test_jar_without_manifest_is_not_bootable(self):
        self.assertFalse(ar.is_bootable(f"{self.base}/nomanifest.jar"))

    def test_non_zip_is_not_bootable(self):
        self.assertFalse(ar.is_bootable(f"{self.base}/notazip.war"))

    def test_a_short_read_is_retried_not_taken_as_a_verdict(self):
        # A truncated Range response raises BadZipFile, exactly like a corrupt
        # archive. Concluding "nothing to run" from the first one dropped the
        # newest collectory, image-service and biocollect during a full
        # --n-tags=10 run, and cached the mistake.
        original = ar._HttpRangeFile
        attempts = []

        def flaky(url, *args, **kwargs):
            attempts.append(url)
            if len(attempts) == 1:
                raise zipfile.BadZipFile("truncated")
            return original(url, *args, **kwargs)

        ar._HttpRangeFile = flaky
        try:
            self.assertTrue(ar.is_bootable(f"{self.base}/boot.war"))
        finally:
            ar._HttpRangeFile = original
        self.assertEqual(len(attempts), 2)

    def test_persistent_network_failure_is_transient_not_a_verdict(self):
        original = ar._HttpRangeFile

        def broken(*args, **kwargs):
            raise OSError("connection reset by peer")

        ar._HttpRangeFile = broken
        try:
            with self.assertRaises(ar.TransientResolveError):
                ar.is_bootable(f"{self.base}/boot.war")
        finally:
            ar._HttpRangeFile = original

    def test_missing_artifact_is_not_bootable(self):
        self.assertFalse(ar.is_bootable(f"{self.base}/absent.jar"))

    def test_reads_far_less_than_the_whole_file(self):
        handle = ar._HttpRangeFile(f"{self.base}/boot.war")
        zipfile.ZipFile(handle).read("META-INF/MANIFEST.MF")
        self.assertLess(handle.bytes_read, handle.size)


class ManifestParsingTest(unittest.TestCase):
    def test_wrapped_main_class_is_found(self):
        # MANIFEST.MF wraps at 72 bytes; without unfolding, a Main-Class pushed
        # onto a continuation line would be missed and a runnable artifact
        # wrongly discarded.
        manifest = (
            "Manifest-Version: 1.0\r\n"
            "Some-Very-Long-Header: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\r\n"
            " aaaaaaaaaaaaaaaaaaaa\r\n"
            "Main-Class: au.org.ala.App\r\n"
        )
        self.assertTrue(ar.manifest_declares_main_class(manifest))

    def test_main_class_on_a_continuation_line_is_not_a_header(self):
        # ' Main-Class: ...' indented is the tail of the previous value, not a
        # header of its own.
        manifest = "Manifest-Version: 1.0\r\nX-Note: see\r\n Main-Class: nope\r\n"
        self.assertFalse(ar.manifest_declares_main_class(manifest))

    def test_empty_manifest(self):
        self.assertFalse(ar.manifest_declares_main_class(None))
        self.assertFalse(ar.manifest_declares_main_class(""))


class AssetFilteringTest(unittest.TestCase):
    """list_assets must return only things you could actually run."""

    def _assets(self, items):
        payload = json.dumps({"items": items}).encode("utf-8")

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        original = ar.urllib.request.urlopen
        ar.urllib.request.urlopen = lambda *a, **k: FakeResponse(payload)
        try:
            return ar.list_assets("apikey", "1.3")
        finally:
            ar.urllib.request.urlopen = original

    @staticmethod
    def _item(classifier, extension):
        return {
            "downloadUrl": f"https://example/apikey-1.3-{classifier}.{extension}",
            "maven2": {"classifier": classifier, "extension": extension},
        }

    def test_checksums_and_metadata_are_dropped(self):
        assets = self._assets(
            [
                self._item("exec", "jar"),
                self._item("exec", "jar.md5"),
                self._item("exec", "jar.sha1"),
                self._item("", "pom"),
                # biocache-service publishes Gradle module metadata next to its war.
                self._item("", "module"),
                self._item("sources", "jar"),
                self._item("javadoc", "jar"),
                self._item("", "war"),
            ]
        )
        self.assertEqual(
            sorted((a["classifier"], a["extension"]) for a in assets),
            [("", "war"), ("exec", "jar")],
        )

    def test_unreachable_nexus_raises_transient(self):
        original = ar.urllib.request.urlopen

        def boom(*args, **kwargs):
            raise OSError("connection reset")

        ar.urllib.request.urlopen = boom
        try:
            with self.assertRaises(ar.TransientResolveError):
                ar.list_assets("apikey", "1.3")
        finally:
            ar.urllib.request.urlopen = original


class PreferenceTest(unittest.TestCase):
    """Ranking only ever decides between artifacts already known to boot."""

    @staticmethod
    def _rank(classifier, extension, declared, preference=None):
        return ar._preference_rank(
            {"classifier": classifier, "extension": extension}, declared, preference
        )

    def test_declared_wins(self):
        # Newest versions must keep resolving exactly as they do today; this
        # change is only meant to fix the ones that were failing.
        declared = ("exec", "war")
        self.assertLess(
            self._rank("exec", "war", declared), self._rank("exec", "jar", declared)
        )

    def test_bootable_classifier_beats_bare(self):
        # apikey 1.3 publishes exec:jar and a plain :war. Both are candidates by
        # name; only the jar runs, and it is also the one that should be ranked
        # first so the probe stops there.
        declared = ("exec", "war")  # absent from 1.3
        self.assertLess(
            self._rank("exec", "jar", declared), self._rank("", "war", declared)
        )

    def test_jar_beats_war_within_the_same_classifier(self):
        declared = ("exec", "war")
        self.assertLess(
            self._rank("exec", "jar", declared), self._rank("exec", "war2", declared)
        )

    def test_explicit_preference_overrides_everything(self):
        # The escape hatch for next-gen packaging: a service can pin its own
        # order without touching build.py.
        declared = ("exec", "war")
        self.assertLess(
            self._rank("", "war", declared, preference=[":war", "exec:war"]),
            self._rank("exec", "war", declared, preference=[":war", "exec:war"]),
        )


class ResolveArtifactTest(unittest.TestCase):
    """The end-to-end decision, with the network stubbed out."""

    def setUp(self):
        self._list_assets = ar.list_assets
        self._is_bootable = ar.is_bootable

    def tearDown(self):
        ar.list_assets = self._list_assets
        ar.is_bootable = self._is_bootable

    def _stub(self, assets, bootable_urls):
        ar.list_assets = lambda *a, **k: assets
        ar.is_bootable = lambda url: url in bootable_urls

    def test_apikey_1_3_picks_the_jar_not_the_war(self):
        # The case that makes the bootability check necessary: both files exist,
        # picking on name alone would publish an image that dies at startup.
        assets = [
            {"classifier": "exec", "extension": "jar", "url": "jar-url"},
            {"classifier": "", "extension": "war", "url": "war-url"},
        ]
        self._stub(assets, {"jar-url"})
        choice = ar.resolve_artifact("apikey", "1.3", "exec", "war", use_cache=False)
        self.assertEqual((choice["classifier"], choice["extension"]), ("exec", "jar"))

    def test_logger_service_4_3_falls_back_to_the_plain_war(self):
        assets = [{"classifier": "", "extension": "war", "url": "war-url"}]
        self._stub(assets, {"war-url"})
        choice = ar.resolve_artifact(
            "logger-service", "4.3", "exec", "jar", use_cache=False
        )
        self.assertEqual((choice["classifier"], choice["extension"]), ("", "war"))

    def test_nothing_runnable_returns_none(self):
        assets = [{"classifier": "", "extension": "war", "url": "war-url"}]
        self._stub(assets, set())
        self.assertIsNone(
            ar.resolve_artifact("biocache-service", "3.4.0", "exec", "war", use_cache=False)
        )

    def test_declared_combination_is_kept_when_present(self):
        assets = [
            {"classifier": "exec", "extension": "war", "url": "exec-war"},
            {"classifier": "", "extension": "war", "url": "war"},
        ]
        self._stub(assets, {"exec-war", "war"})
        choice = ar.resolve_artifact(
            "biocache-service", "3.9.0", "exec", "war", use_cache=False
        )
        self.assertEqual((choice["classifier"], choice["extension"]), ("exec", "war"))

    def test_transient_errors_propagate(self):
        # Must not be reported as "this version has no artifact": that verdict
        # gets cached and would drop a perfectly good version for good.
        def boom(*args, **kwargs):
            raise ar.TransientResolveError("nexus down")

        ar.list_assets = boom
        with self.assertRaises(ar.TransientResolveError):
            ar.resolve_artifact("apikey", "1.3", "exec", "war", use_cache=False)


class SeverityTest(unittest.TestCase):
    """build.py drops a bad (service, version) pair and keeps going, but the
    newest version of a service is the one tagged `latest`, so a failure there is
    a real regression and must fail the run."""

    @staticmethod
    def _fatal(entries):
        return [entry for entry in entries if entry["is_newest"]]

    def test_historical_failures_are_not_fatal(self):
        entries = [
            {"name": "apikey", "version": "1.3", "is_newest": False},
            {"name": "biocache-service", "version": "3.4.0", "is_newest": False},
        ]
        self.assertEqual(self._fatal(entries), [])

    def test_newest_failure_is_fatal(self):
        entries = [
            {"name": "apikey", "version": "1.3", "is_newest": False},
            {"name": "apikey", "version": "1.7.0", "is_newest": True},
        ]
        self.assertEqual(len(self._fatal(entries)), 1)


class DocoptOptionsTest(unittest.TestCase):
    """build.py's CLI is parsed from its own docstring, so prose can break it."""

    @staticmethod
    def _options_block():
        source = (pathlib.Path(__file__).resolve().parent.parent / "build.py").read_text()
        doc = source.split('"""')[1]
        return doc.split("Options:", 1)[1]

    def test_no_continuation_line_starts_with_a_dash(self):
        # docopt reads ANY line in Options: whose first non-space character is a
        # dash as a new option definition. A wrapped description beginning with
        # an example like "--list-tags=1.3,1.4" therefore registers a second
        # option, and the real one stops resolving:
        #     "--n-tags is not a unique prefix: --n-tags, --n-tags?"
        # It breaks a flag that the edit never touched, which is what makes it
        # worth a test rather than a comment.
        offenders = []
        for line in self._options_block().splitlines():
            stripped = line.strip()
            if not stripped.startswith("-"):
                continue
            # A real definition is indented by 2; docopt's own continuation
            # indent is deeper, and that is exactly where the trap is.
            if len(line) - len(line.lstrip()) > 2:
                offenders.append(stripped[:60])
        self.assertEqual(offenders, [], "continuation lines must not start with '-'")

    def test_every_documented_flag_is_read(self):
        # --list-tags sat in this docstring for months with no implementation:
        # docopt accepted it and nothing ever looked at the value.
        source = (pathlib.Path(__file__).resolve().parent.parent / "build.py").read_text()
        documented = set(re.findall(r"^  (--[a-z-]+)", self._options_block(), re.MULTILINE))
        for flag in documented - {"--help", "--version"}:
            with self.subTest(flag=flag):
                self.assertIn(f'"{flag}"', source, f"{flag} is documented but never read")


class RuntimeSecurityUpgradeTest(unittest.TestCase):
    """Every runtime stage applies pending distro updates and checks brotli.

    Base images lag behind the distro: image-service:4.2.0-SNAPSHOT still had
    libbrotli1 1.0.9-2build6 (CVE-2026-111497) after a --pull rebuild, so the
    upgrade has to happen in our own runtime stage, not be left to the base.
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent
    RUNTIME_DOCKERFILES = (
        "templates/Dockerfile.gradle.tmpl",
        "templates/Dockerfile.maven.tmpl",
        "services/la-pipelines/Dockerfile",
    )

    def test_each_runtime_stage_runs_the_upgrade(self):
        for rel in self.RUNTIME_DOCKERFILES:
            with self.subTest(dockerfile=rel):
                text = (self.ROOT / rel).read_text()
                # The runtime stage is the one after the last FROM.
                runtime = re.split(r"^FROM ", text, flags=re.MULTILINE)[-1]
                self.assertIn("COPY scripts/security-upgrade.sh", runtime)
                self.assertRegex(runtime, r"(?m)^RUN .*sh /usr/local/bin/security-upgrade\.sh")
                # Without a changing build arg the upgrade layer comes from cache
                # and never sees a fix published after the first build.
                self.assertRegex(runtime, r"(?m)^ARG CACHE_BYPASS")
                self.assertLess(runtime.index("security-upgrade.sh"), runtime.index("ca-distrust.sh"))

    def test_build_py_puts_the_script_in_the_context(self):
        source = (self.ROOT / "build.py").read_text()
        self.assertIn('"security-upgrade.sh"', source)
        self.assertTrue((self.ROOT / "templates/scripts/security-upgrade.sh").is_file())


class ReleaseVersionSortTest(unittest.TestCase):
    def test_ecodata_5_14_is_newer_than_5_9_2(self):
        # The real tail of ecodata's metadata, plus the entries that used to send
        # the whole list to a text sort.
        versions = ["1.17-SNANPSHOT", "3.2.1-snapshot", "4.2-SNAPSHOT-paratoo",
                    "5.9", "5.9.1", "5.9.2", "5.10", "5.11", "5.12", "5.13", "5.14"]
        self.assertEqual(ar.sort_release_versions(versions)[-1], "5.14")
        self.assertEqual(ar.sort_release_versions(versions)[-2:], ["5.13", "5.14"])

    def test_one_offs_are_never_built(self):
        versions = ["2.4", "2.5-MDBA", "4.0.1.DQ", "0.3.7-RELEASE", "2.4.PRERELEASE"]
        self.assertEqual(ar.sort_release_versions(versions), ["2.4"])

    def test_rebuild_suffix_is_kept_and_ordered(self):
        versions = ["6.5.6-5", "6.5.6-3", "6.5.6", "6.5.5-2", "5.3.12-5"]
        self.assertEqual(
            ar.sort_release_versions(versions),
            ["5.3.12-5", "6.5.5-2", "6.5.6", "6.5.6-3", "6.5.6-5"],
        )

    def test_build_py_sorts_nexus_versions_with_it(self):
        source = (pathlib.Path(__file__).resolve().parent.parent / "build.py").read_text()
        self.assertIn("artifact_resolver.sort_release_versions(versions)[-n:]", source)


class JenkinsfileDockerAuthTest(unittest.TestCase):
    """`docker login` and `docker logout` write one file per agent user, so the
    pipeline has to keep its credentials out of the shared one."""

    @staticmethod
    def _jenkinsfile():
        text = (pathlib.Path(__file__).resolve().parent.parent / "Jenkinsfile").read_text()
        # Comments here talk about `docker login`/`docker logout`; only the
        # steps count.
        return "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("//")
        )

    def test_login_uses_a_workspace_scoped_docker_config(self):
        # #266 lost 4 images and #265 lost 10 to "insufficient_scope:
        # authorization failed" on pushes that had been working for hours:
        # another job on the agent ran `docker logout` against the shared
        # /var/lib/jenkins/.docker/config.json mid-run. A retry cannot help --
        # the credentials are gone -- so the config has to be per-build.
        text = self._jenkinsfile()
        self.assertIn(
            "DOCKER_CONFIG=${env.WORKSPACE}",
            text,
            "docker credentials must live in the workspace, not in ~/.docker",
        )
        for match in re.finditer(r"docker log(?:in|out)", text):
            self.assertLess(
                text.index("DOCKER_CONFIG=${env.WORKSPACE}"),
                match.start(),
                f"'{match.group(0)}' runs before DOCKER_CONFIG is set, so it "
                f"reads and writes the agent's shared config",
            )

    def test_build_py_runs_unbuffered(self):
        # Without -u, build.py's progress lines land in the log in one block
        # hours later, all with the same timestamp, and a registry error cannot
        # be matched to the image it belongs to.
        self.assertRegex(
            self._jenkinsfile(),
            r"venv/bin/python -u build\.py",
            "build.py must run unbuffered so its output interleaves with docker's",
        )


class ImageSampleTest(unittest.TestCase):
    """What a run tests has to come from what that run built."""

    @staticmethod
    def _pick(rows):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import importlib.util

        path = pathlib.Path(__file__).resolve().parent / "pick-image-to-test.py"
        spec = importlib.util.spec_from_file_location("pick_image_to_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.pick(rows)

    def test_picks_the_last_java_service(self):
        # Last, not first: build/<service> is rewritten for every version, so
        # only the final one still has the Dockerfile the test script reads.
        rows = [
            ["cas", "6.5.6-1", "11"],
            ["cas", "6.5.6-2", "11"],
            ["userdetails", "3.2.0", "17"],
        ]
        self.assertEqual(self._pick(rows), ("userdetails", "3.2.0"))

    def test_skips_non_java_images(self):
        # i18n is a data container: no USER, no EXPOSE, no /data, and
        # run-container-tests.sh fails an image without them. Testing it would
        # turn every i18n run red for checks that do not apply.
        rows = [["userdetails", "3.2.0", "17"], ["i18n", "latest", "none"]]
        self.assertEqual(self._pick(rows), ("userdetails", "3.2.0"))

    def test_no_java_service_is_not_an_error(self):
        self.assertIsNone(self._pick([["i18n", "latest", "none"]]))
        self.assertIsNone(self._pick([]))

    def test_build_py_and_the_jenkinsfile_agree_on_the_manifest(self):
        # Two files, one contract: build.py writes it, the pipeline reads it.
        root = pathlib.Path(__file__).resolve().parent.parent
        build_py = (root / "build.py").read_text()
        self.assertIn('BUILT_IMAGES_FILE = "built-images.tsv"', build_py)
        self.assertIn('f"{name}\\t{version}\\t{java}\\n"', build_py)
        picker = (root / "scripts" / "pick-image-to-test.py").read_text()
        self.assertIn('"built-images.tsv"', picker)

    def test_the_tag_tested_is_not_hardcoded(self):
        # A --list-tags run does not move `latest`, so testing `latest` would
        # check an image the run never touched.
        jenkinsfile = (pathlib.Path(__file__).resolve().parent.parent / "Jenkinsfile").read_text()
        for line in jenkinsfile.splitlines():
            if "run-container-tests.sh" in line and not line.strip().startswith("//"):
                self.assertNotIn(
                    " latest", line, "the tag must come from what the run built"
                )



class GradleTemplateRuntimeTest(unittest.TestCase):
    """pdfgen boots from the generic template: without these it exits on start."""

    root = pathlib.Path(__file__).resolve().parent.parent

    def test_gradle_cmd_passes_app_args(self):
        template = (self.root / "templates/Dockerfile.gradle.tmpl").read_text()
        self.assertRegex(template, r'(?m)^CMD java .*app\.\$\{EXTENSION\}" \$\{APP_ARGS\}$')

    def test_gradle_template_installs_extra_packages_as_root(self):
        template = (self.root / "templates/Dockerfile.gradle.tmpl").read_text()
        self.assertLess(
            template.index("${EXTRA_APT_PACKAGES}"), template.index('USER "${SERVICE_USER}"')
        )

    def test_pdfgen_definition_can_start_and_convert(self):
        definitions = (self.root / "services-definition.yml").read_text()
        pdfgen = definitions.split("\n  pdfgen:\n", 1)[1].split("\n  ecodata:\n", 1)[0]
        self.assertIn("app_args: server /data/pdfgen/config/pdfgen-config.yml", pdfgen)
        self.assertIn("- libreoffice-writer", pdfgen)
        self.assertIn("- wkhtmltopdf", pdfgen)


class BuildAgentPreflightTest(unittest.TestCase):
    """A broken agent must not be reported as broken images.

    Driven with a stub `docker` on PATH, so the three outcomes are exercised
    without a daemon: the real thing only ever runs on the agent, where a
    mistake costs a whole queue.
    """

    ROOT = pathlib.Path(__file__).resolve().parent.parent
    SCRIPT = ROOT / "scripts" / "check-build-agent.sh"

    CORRUPT = (
        'ERROR: failed to solve: failed to prepare extraction snapshot "x": '
        'failed to create snapshot: missing parent '
        '"moby/3052/sha256:deadbeef" bucket: not found'
    )

    def _run(self, fail_builds, message, attempts=2):
        import subprocess
        import tempfile

        tmp = tempfile.mkdtemp()
        stub = pathlib.Path(tmp) / "docker"
        stub.write_text(
            "#!/bin/sh\n"
            'echo "$*" >> "$FAKE_LOG"\n'
            "case \"$1\" in\n"
            "  build)\n"
            "    n=$(grep -c '^build ' \"$FAKE_LOG\")\n"
            '    if [ "$n" -le "$FAIL_BUILDS" ]; then printf \'%s\\n\' "$FAIL_MSG" >&2; exit 1; fi\n'
            "    exit 0 ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n"
        )
        stub.chmod(0o755)
        log = pathlib.Path(tmp) / "calls.log"
        log.write_text("")
        env = dict(
            os.environ,
            DOCKER=str(stub),
            FAKE_LOG=str(log),
            FAIL_BUILDS=str(fail_builds),
            FAIL_MSG=message,
            BUILD_AGENT_PAUSE="0",
            BUILD_AGENT_ATTEMPTS=str(attempts),
            # One probe image, so these cases are about the agent rather than
            # about falling through to the next image (that has its own tests).
            BUILD_AGENT_PROBE_IMAGE="busybox:stable",
        )
        result = subprocess.run(
            ["sh", str(self.SCRIPT)], capture_output=True, text=True, env=env
        )
        return result, log.read_text()

    def test_a_corrupt_build_cache_is_cleared_and_the_run_goes_on(self):
        # TASK-5, twice: #243-#249 in October and #295-#311 on 2026-11-02. The
        # cache is only a cache, so clearing it is the fix, not a workaround.
        result, calls = self._run(fail_builds=1, message=self.CORRUPT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("builder prune -af", calls)

    def test_dns_is_not_blamed_on_the_cache_or_the_other_way_round(self):
        # The first version of this check called every failure a DNS failure,
        # and reported "cannot resolve" for seventeen runs that were really
        # BuildKit being corrupt.
        result, calls = self._run(fail_builds=99, message="cannot resolve nexus.ala.org.au")
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot resolve", result.stderr)
        self.assertNotIn("builder prune", calls, "DNS is not fixed by pruning")

        result, calls = self._run(fail_builds=99, message=self.CORRUPT)
        self.assertEqual(result.returncode, 1)
        self.assertIn("snapshot metadata", result.stderr)
        self.assertEqual(calls.count("builder prune -af"), 1, "prune once, then give up")

    def _run_wedged_image(self, wedged_image, message, images):
        """A stub that fails only for builds whose FROM is `wedged_image`."""
        import subprocess
        import tempfile

        tmp = tempfile.mkdtemp()
        stub = pathlib.Path(tmp) / "docker"
        stub.write_text(
            "#!/bin/sh\n"
            'echo "$*" >> "$FAKE_LOG"\n'
            "case \"$1\" in\n"
            "  build)\n"
            "    df=\n"
            "    while [ $# -gt 0 ]; do\n"
            '      [ "$1" = "-f" ] && df=$2\n'
            "      shift\n"
            "    done\n"
            '    if grep -q "^FROM $WEDGED_IMAGE\\$" "$df"; then\n'
            '      printf \'%s\\n\' "$FAIL_MSG" >&2; exit 1\n'
            "    fi\n"
            "    exit 0 ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n"
        )
        stub.chmod(0o755)
        log = pathlib.Path(tmp) / "calls.log"
        log.write_text("")
        env = dict(
            os.environ,
            DOCKER=str(stub),
            FAKE_LOG=str(log),
            WEDGED_IMAGE=wedged_image,
            FAIL_MSG=message,
            BUILD_AGENT_PAUSE="0",
            BUILD_AGENT_ATTEMPTS="2",
            BUILD_AGENT_PROBE_IMAGES=images,
        )
        result = subprocess.run(
            ["sh", str(self.SCRIPT)], capture_output=True, text=True, env=env
        )
        return result, log.read_text()

    def test_a_wedged_probe_image_does_not_stop_the_run(self):
        # 2026-11-02: busybox:stable's layer could neither be read nor
        # recreated, and it was this check's only probe image, so the check
        # failed #295-#316 while the agent built debian:bookworm-slim fine.
        # A broken probe image is the check's problem, not the run's.
        result, calls = self._run_wedged_image(
            "busybox:stable", self.CORRUPT, "busybox:stable debian:bookworm-slim"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("debian:bookworm-slim", result.stdout)
        self.assertIn("busybox:stable", result.stderr, "the wedged image is named")
        self.assertIn("build agent: ok", result.stdout)
        # #319-#320 paid 13 minutes a run pruning the cache for a layer that
        # the next probe image made irrelevant. Nothing to prune here.
        self.assertNotIn("builder prune", calls)

    def test_every_probe_image_wedged_still_stops_the_run(self):
        # The other half: if no base image can be extracted, nothing will
        # build, and that does need a human on the agent.
        result, _ = self._run_wedged_image(
            "busybox:stable", self.CORRUPT, "busybox:stable busybox:stable"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("no probe image can be extracted", result.stderr)

    def test_an_unknown_failure_says_so_instead_of_guessing(self):
        result, _ = self._run(fail_builds=99, message="no space left on device")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not recognise", result.stderr)

    def test_the_pipeline_checks_the_agent_before_building(self):
        text = (self.ROOT / "Jenkinsfile").read_text()
        steps = [l for l in text.splitlines() if not l.strip().startswith("//")]
        check = next(
            (i for i, l in enumerate(steps) if "check-build-agent.sh" in l), None
        )
        build = next((i for i, l in enumerate(steps) if "build.py" in l), None)
        self.assertIsNotNone(check, "the pipeline must check the agent first")
        self.assertIsNotNone(build)
        self.assertLess(check, build, "the check has to run before the build")

    def test_the_check_script_parses(self):
        import subprocess

        result = subprocess.run(
            ["sh", "-n", str(self.SCRIPT)], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


# --- git-only releases ---------------------------------------------------------
# Versions tagged upstream but never deployed to Nexus (alerts 5.3.2, 6.2.0,
# 6.2.1). No network: ls-remote output and the raw build files are fixtures.

import git_versions as gv

LS_REMOTE = """\
aaa\trefs/tags/5.3.1
bbb\trefs/tags/5.3.2
ccc\trefs/tags/6.1.0-SNAPSHOT
ddd\trefs/tags/6.1.0-SNAPSHOT^{}
eee\trefs/tags/6.2.0
fff\trefs/tags/v6.2.1
ggg\trefs/tags/alerts-1.1
hhh\trefs/tags/6.3.0-RC1
"""

# Real build files of the two alerts tags this was written for.
ALERTS_6_2_1 = {
    "gradle.properties": "grailsVersion=7.1.1\ngroovyVersion=4.0.31\n",
    "build.gradle": "version = \"6.2.1\"\napply plugin:\"war\"\n",
}
ALERTS_5_3_2 = {
    "gradle.properties": "grailsVersion=6.0.0\n",
    "build.gradle": "java {\n    sourceCompatibility = JavaVersion.VERSION_17\n}\n",
}


class GitOnlyReleasesTest(unittest.TestCase):
    def test_tag_names_come_out_of_ls_remote_once(self):
        tags = gv.parse_ls_remote(LS_REMOTE)
        self.assertEqual(tags.count("6.1.0-SNAPSHOT"), 1)
        self.assertIn("v6.2.1", tags)

    def test_only_release_tags_above_the_newest_nexus_release(self):
        found = gv.git_only_releases(gv.parse_ls_remote(LS_REMOTE), ["5.2.5", "5.3.1"])
        # v is stripped but the tag keeps it; SNAPSHOT, RC and alerts-1.1 are not releases
        self.assertEqual(found, [("5.3.2", "5.3.2"), ("6.2.0", "6.2.0"), ("6.2.1", "v6.2.1")])

    def test_old_tags_nexus_lacks_are_history_and_stay_out(self):
        found = gv.git_only_releases(["1.0", "2.0", "5.4.0"], ["3.0.0", "4.0.0"])
        self.assertEqual(found, [("5.4.0", "5.4.0")])

    def test_numbers_compare_as_numbers(self):
        # as text 5.9.2 sorts after 5.14 (the ecodata:latest bug)
        found = gv.git_only_releases(["5.14", "5.9.2"], ["5.9.1"])
        self.assertEqual([v for v, _ in found], ["5.9.2", "5.14"])

    def test_dash_suffix_releases_count(self):
        found = gv.git_only_releases(["6.5.6-6", "6.5.6-5"], ["6.5.6-5"])
        self.assertEqual(found, [("6.5.6-6", "6.5.6-6")])

    def test_without_nexus_releases_nothing_is_built_from_history(self):
        self.assertEqual(gv.git_only_releases(["1.0", "2.0"], []), [])

    def test_nothing_new_means_nothing_extra(self):
        self.assertEqual(gv.git_only_releases(["5.3.1"], ["5.3.1"]), [])


class SelectVersionsTest(unittest.TestCase):
    NEXUS = ["5.2.5", "5.3.1"]
    GIT = [("5.3.2", "5.3.2"), ("6.2.1", "v6.2.1")]

    def test_n_counts_over_both_sources(self):
        picked = gv.select_versions(self.NEXUS, self.GIT, 1)
        self.assertEqual([(p["version"], p["source"]) for p in picked], [("6.2.1", "git")])
        self.assertEqual(picked[0]["tag"], "v6.2.1")

    def test_nexus_stays_in_when_n_reaches_it(self):
        picked = gv.select_versions(self.NEXUS, self.GIT, 3)
        self.assertEqual(
            [(p["version"], p["source"]) for p in picked],
            [("5.3.1", "nexus"), ("5.3.2", "git"), ("6.2.1", "git")],
        )

    def test_without_git_it_is_the_old_behaviour(self):
        picked = gv.select_versions(self.NEXUS, [], 1)
        self.assertEqual([p["version"] for p in picked], ["5.3.1"])


class JavaInferenceTest(unittest.TestCase):
    def test_grails_7_without_source_compatibility_is_java_17(self):
        self.assertEqual(gv.infer_java_version(ALERTS_6_2_1), "17")

    def test_explicit_source_compatibility(self):
        self.assertEqual(gv.infer_java_version(ALERTS_5_3_2), "17")

    def test_the_highest_signal_wins(self):
        files = {"build.gradle": "sourceCompatibility = JavaVersion.VERSION_11",
                 "gradle.properties": "grailsVersion=7.0.0"}
        self.assertEqual(gv.infer_java_version(files), "17")

    def test_old_style_java_8(self):
        self.assertEqual(
            gv.infer_java_version({"build.gradle": "sourceCompatibility = JavaVersion.VERSION_1_8"}), "8"
        )
        self.assertEqual(
            gv.infer_java_version({"build.gradle": "sourceCompatibility = '1.8'"}), "8"
        )

    def test_maven(self):
        self.assertEqual(
            gv.infer_java_version({"pom.xml": "<properties><java.version>21</java.version></properties>"}), "21"
        )
        self.assertEqual(
            gv.infer_java_version({"pom.xml": "<maven.compiler.release>17</maven.compiler.release>"}), "17"
        )

    def test_no_signal_is_none(self):
        self.assertIsNone(gv.infer_java_version({"build.gradle": "version = '1'"}))
        self.assertIsNone(gv.infer_java_version({}))

    def test_the_sources_override_a_stale_dependencies_yaml(self):
        self.assertEqual(gv.resolve_java_version("11", "17"), ("17", True))
        self.assertEqual(gv.resolve_java_version("17", "11"), ("17", False))
        self.assertEqual(gv.resolve_java_version(None, "17"), ("17", False))
        self.assertEqual(gv.resolve_java_version("11", None), ("11", False))
        self.assertEqual(gv.resolve_java_version(None, None), (None, False))

    def test_build_files_are_fetched_from_the_tag(self):
        urls = []
        files = gv.fetch_build_files(
            "https://github.com/AtlasOfLivingAustralia/alerts",
            "v6.2.1",
            fetch=lambda url: urls.append(url) or ("x" if "build.gradle" in url else None),
        )
        self.assertEqual(list(files), ["build.gradle"])
        self.assertIn(
            "https://raw.githubusercontent.com/AtlasOfLivingAustralia/alerts/v6.2.1/gradle.properties", urls
        )


class PlanVersionsTest(unittest.TestCase):
    """build.py's glue: an unresolvable git-only tag must not take the run down."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "build_under_test", pathlib.Path(__file__).resolve().parent.parent / "build.py"
        )
        cls.build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.build)

    CONF = {"repository": "https://github.com/AtlasOfLivingAustralia/alerts"}
    DEPS = {"alerts": {">= 2.0.0": [{"java": "11"}]}}

    def plan(self, conf=None, files=None, tags=("5.3.1", "6.2.1"), n=1, nexus=("5.3.1",)):
        gvm = self.build.git_versions
        saved = gvm.list_remote_tags, gvm.fetch_build_files
        gvm.list_remote_tags = lambda repo: list(tags)
        gvm.fetch_build_files = lambda repo, tag: ALERTS_6_2_1 if files is None else files
        try:
            return self.build.plan_versions(
                "alerts", {**self.CONF, **(conf or {})}, list(nexus), n, self.DEPS
            )
        finally:
            gvm.list_remote_tags, gvm.fetch_build_files = saved

    def test_git_only_newest_is_built_from_source_with_the_inferred_java(self):
        picked = self.plan()
        self.assertEqual([(p["version"], p["source"], p["java_version"]) for p in picked],
                         [("6.2.1", "git", "17")])

    def test_unreadable_build_files_leave_the_tag_out_and_nexus_keeps_latest(self):
        # dependencies.yaml says Java 11 for alerts: trusting it alone is the bug
        picked = self.plan(files={})
        self.assertEqual([(p["version"], p["source"]) for p in picked], [("5.3.1", "nexus")])

    def test_files_without_any_java_signal_fall_back_to_dependencies_yaml(self):
        picked = self.plan(files={"build.gradle": "version = '1'"})
        self.assertEqual([(p["version"], p["java_version"]) for p in picked], [("6.2.1", "11")])

    def test_no_signal_and_no_dependencies_entry_leaves_the_tag_out(self):
        saved = self.build.deps_utils
        self.build.deps_utils = None
        try:
            picked = self.plan(files={"build.gradle": "version = '1'"})
        finally:
            self.build.deps_utils = saved
        self.assertEqual([(p["version"], p["source"]) for p in picked], [("5.3.1", "nexus")])

    def test_opt_out_per_service(self):
        picked = self.plan(conf={"git_releases": False})
        self.assertEqual([(p["version"], p["source"]) for p in picked], [("5.3.1", "nexus")])

    def test_a_forced_java_version_is_respected(self):
        picked = self.plan(conf={"java_version": "21"})
        self.assertEqual(picked[0]["java_version"], "21")

    def test_ls_remote_failure_falls_back_to_nexus(self):
        gvm = self.build.git_versions
        saved = gvm.list_remote_tags
        def boom(repo):
            raise RuntimeError("no network")
        gvm.list_remote_tags = boom
        try:
            picked = self.build.plan_versions("alerts", dict(self.CONF), ["5.3.1"], 1, self.DEPS)
        finally:
            gvm.list_remote_tags = saved
        self.assertEqual([(p["version"], p["source"]) for p in picked], [("5.3.1", "nexus")])



if __name__ == "__main__":
    unittest.main(verbosity=2)
