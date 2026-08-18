#!/usr/bin/env python3
"""Generation-level guards for the Dockerfile templates.

Cheap on purpose: renders the templates the way build.py does and inspects the
result as text. No Docker, no network, no venv -- runs in the Validate Sync
stage so a broken template is caught before anything is built.

The container-structure tests (common-tests/, run-container-tests.sh) cover the
built image instead, but they need Docker and are not part of the pipeline.
"""

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

if failures:
    print("Dockerfile template checks FAILED:\n")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)

print(f"Dockerfile template checks passed ({len(TEMPLATES)} templates).")
