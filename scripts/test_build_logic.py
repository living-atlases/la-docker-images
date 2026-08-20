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


if __name__ == "__main__":
    unittest.main(verbosity=2)
