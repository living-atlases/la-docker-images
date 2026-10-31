#!/usr/bin/env python3
"""Pick one image from what a run built, for the container structure tests.

build.py writes build/built-images.tsv (service, version, java_version) at the
end of a run. The Image Tests stage used to probe a fixed pair, userdetails and
cas, so any run without them tested nothing -- which is every --list-tags run,
since those rebuild a single service.

Prints "<service> <version>" for the image to test, or nothing when there is
none. Stdlib only and no Docker: it runs in Jenkins before the venv is on PATH.
"""

import os
import sys

MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "build",
    "built-images.tsv",
)


def pick(rows):
    """The last Java service built, or None.

    Last, because build/<service> is rewritten for every version and only the
    final one still has the Dockerfile run-container-tests.sh reads.

    Java, because that script checks the shape of a Java service (non-root
    USER, EXPOSE, /data): a data container like i18n has none of those on
    purpose and would fail checks that do not apply to it.
    """
    for row in reversed(rows):
        if len(row) < 3:
            continue
        service, version, java = row[0], row[1], row[2]
        if not service or not version:
            continue
        if java and java.lower() not in ("none", ""):
            return service, version
    return None


def read(path):
    with open(path) as handle:
        return [line.rstrip("\n").split("\t") for line in handle if line.strip()]


def main(argv):
    path = argv[1] if len(argv) > 1 else MANIFEST
    if not os.path.exists(path):
        print("no image manifest: this run built nothing", file=sys.stderr)
        return 0
    rows = read(path)
    chosen = pick(rows)
    if not chosen:
        print(
            f"{len(rows)} image(s) built, none of them a Java service", file=sys.stderr
        )
        return 0
    print(f"{chosen[0]} {chosen[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
