#!/usr/bin/env python3
"""Generation-level guards for the Dockerfile templates.

Cheap on purpose: renders the templates the way build.py does and inspects the
result as text. No Docker, no network, no venv -- runs in the Validate Sync
stage so a broken template is caught before anything is built.

The container-structure tests (common-tests/, run-container-tests.sh) cover the
built image instead, but they need Docker and are not part of the pipeline.
"""

import glob
import os
import re
import sys
from string import Template

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = ["Dockerfile.maven.tmpl", "Dockerfile.gradle.tmpl"]

# A marker, NOT a copy of build.py's default. build.py computes JAVA_OPTS from
# defaults plus per-service memory settings and extra_params (build.py:557-598),
# and importing it here is not an option: it needs yaml and docopt, while this
# runs in Validate Sync before the venv exists. Hard-coding the real default
# instead would drift, and the test would stay green while build.py regressed.
#
# What matters does not depend on the value at all: whatever build.py computes
# must land in ENV and must NOT land in the CMD. Using a value that could never
# be a real default makes both halves unambiguous.
PROBE_JAVA_OPTS = "-Xmx1234m -Xms1234m -Dprobe.marker=gh-3"

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)


def render(template_name):
    path = os.path.join(REPO_ROOT, "templates", template_name)
    with open(path, "r") as handle:
        content = handle.read()
    # Same call build.py makes, with the placeholders that matter here.
    return Template(content).safe_substitute(
        {
            "JAVA_OPTS": PROBE_JAVA_OPTS,
            "EXTENSION": "war",
            "APP_ARGS": "",
            "SERVICE_USER": "userdetails",
            "PORT": "8080",
        }
    )


def cmd_line(rendered):
    for line in rendered.split("\n"):
        if line.startswith("CMD java"):
            return line
    return None


for template_name in TEMPLATES:
    rendered = render(template_name)
    cmd = cmd_line(rendered)

    check(cmd is not None, f"{template_name}: no 'CMD java' line in the output")
    if cmd is None:
        continue

    # gh-3: a bare ${JAVA_OPTS} in the CMD is substituted at generation time, so
    # the defaults get baked into the image and the JAVA_OPTS the compose
    # environment sets at runtime is never read. Every *_max_memory override
    # silently did nothing for the whole Maven/Gradle image family.
    check(
        "${JAVA_OPTS}" in cmd,
        f"{template_name}: CMD must keep a literal ${{JAVA_OPTS}} for the shell "
        f"to expand at container start (use $$ to escape it). Got: {cmd}",
    )
    check(
        not re.search(r"-Xm[sx]\d|-Xss\d", cmd),
        f"{template_name}: CMD has JVM memory flags baked in at build time, so "
        f"runtime overrides cannot win. Got: {cmd}",
    )
    # The same thing said directly: whatever build.py computed must not have been
    # substituted into the CMD. This is what actually failed before the fix.
    check(
        PROBE_JAVA_OPTS not in cmd,
        f"{template_name}: the computed JAVA_OPTS was substituted into the CMD at "
        f"generation time. Got: {cmd}",
    )

    # It still belongs in ENV, as the default the environment overrides. Losing it
    # would silently drop the per-service tuning build.py computes.
    check(
        f'ENV JAVA_OPTS="{PROBE_JAVA_OPTS}"' in rendered,
        f"{template_name}: ENV JAVA_OPTS must carry the value build.py computes",
    )

    # APP_ARTIFACT is deliberately not in build.py's mapping: it resolves at
    # runtime from its own ENV. If that ever changes the jar path breaks.
    check(
        "${APP_ARTIFACT}" in cmd,
        f"{template_name}: CMD must resolve ${{APP_ARTIFACT}} at runtime. Got: {cmd}",
    )

# ── Hand-written service Dockerfiles ─────────────────────────────────────────
# Not rendered from a template, so the checks above never see them.
service_dockerfiles = sorted(
    path
    for path in glob.glob(os.path.join(REPO_ROOT, "services", "*", "Dockerfile"))
)

for path in service_dockerfiles:
    name = os.path.relpath(path, REPO_ROOT)
    with open(path, "r") as handle:
        raw = handle.read()
    # Comments here discuss trusted.gpg.d and the scripts by name; only the
    # instructions count.
    body = "\n".join(
        line for line in raw.splitlines() if not line.lstrip().startswith("#")
    )

    # sds-static-home:latest was published on 2026-10-29 still trusting emSign
    # ECC Root CA - G3 (86A1ECBA...BE6B): ca-distrust.sh went into the two
    # templates and nobody carried it into the hand-written Dockerfiles. i18n
    # had the same hole and was clean only by luck. build.py copies both
    # scripts into every build context, so there is nothing to opt into.
    for script in ("security-upgrade.sh", "ca-distrust.sh"):
        check(
            f"/{script}" in body,
            f"{name}: the runtime stage must run scripts/{script}, like the "
            f"templates do -- otherwise this image ships whatever the base has",
        )

    # A key in /etc/apt/trusted.gpg.d vouches for EVERY repository in the
    # image, Debian's own included. i18n put the apt.gbif.es key there, and
    # fetched it from a keyserver by a 32-hex search term with no fingerprint
    # check, so whatever came back was trusted for everything.
    check(
        "/etc/apt/trusted.gpg.d" not in body,
        f"{name}: do not install repository keys into /etc/apt/trusted.gpg.d; "
        f"put the key in /etc/apt/keyrings and name it with signed-by=",
    )
    if "sources.list.d" in body:
        check(
            "signed-by=" in body,
            f"{name}: a third-party apt repository must be pinned to its own "
            f"key with signed-by=",
        )

    # A continuation-folded RUN is one logical command; join it before looking.
    commands = re.sub(r"\\\n\s*", " ", body).split("\n")
    for command in commands:
        if " mvn " not in f" {command} " or not re.search(r"\s-T\s*\d", command):
            continue
        # build #266: 10 threads share one local repository, and with a cold
        # cache mount a slow download held the resolver's exclusive lock past
        # its 900s limit, killing the image after 44 minutes of work. Nothing
        # outside the container wants that lock, so the wait must be generous.
        check(
            "-Daether.syncContext.named.time=" in command,
            f"{name}: a parallel mvn (-T) must set "
            f"-Daether.syncContext.named.time, or the resolver's lock timeout "
            f"fails the build on a cold /root/.m2",
        )

if failures:
    print("Dockerfile template checks FAILED:\n")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)

print(
    f"Dockerfile template checks passed ({len(TEMPLATES)} templates, "
    f"{len(service_dockerfiles)} service Dockerfiles)."
)
