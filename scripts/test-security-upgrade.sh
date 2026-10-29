#!/bin/bash
# Tests templates/scripts/security-upgrade.sh in real base images (needs docker):
# a vulnerable brotli must fail the check, and the full run must upgrade it.
# The vulnerable builds are installed from each release's own archive pocket,
# or pinned by digest where the distro keeps no old package (Alpine).
# Usage: ./scripts/test-security-upgrade.sh
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/templates/scripts/security-upgrade.sh"
# nginx-unprivileged:stable-alpine of 2026-10-19, with brotli-libs 1.2.0-r1
OLD_ALPINE=nginxinc/nginx-unprivileged@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03

failures=0

# case NAME EXPECT(pass|fail) IMAGE SETUP ARGS...
case_() {
  local name=$1 expect=$2 image=$3 setup=$4
  shift 4
  local out rc
  out=$(docker run --rm --user root -v "$SCRIPT:/security-upgrade.sh:ro" --entrypoint sh "$image" \
    -c "$setup sh /security-upgrade.sh $*" 2>&1)
  rc=$?
  if { [ "$expect" = pass ] && [ $rc -eq 0 ]; } || { [ "$expect" = fail ] && [ $rc -ne 0 ]; }; then
    echo "✅ PASS: $name: $(echo "$out" | tail -1)"
  else
    echo "❌ FAIL: $name (exit $rc, expected $expect)"
    echo "$out" | tail -5 | sed 's/^/     /'
    failures=$((failures + 1))
  fi
}

jammy_old='apt-get update -qq >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -qq -y --allow-downgrades libbrotli1=1.0.9-2build6 >/dev/null &&'
bookworm_old='apt-get update -qq >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -qq -y --allow-downgrades libbrotli1=1.0.9-2+b6 >/dev/null &&'
focal_any='apt-get update -qq >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -qq -y libbrotli1 >/dev/null &&'

case_ "jammy 1.0.9-2build6 is caught" fail ubuntu:22.04 "$jammy_old" --check-only
case_ "jammy 1.0.9-2build6 is upgraded" pass ubuntu:22.04 "$jammy_old"
case_ "bookworm 1.0.9-2+b6 is caught" fail debian:bookworm-slim "$bookworm_old" --check-only
case_ "bookworm 1.0.9-2+b6 is upgraded" pass debian:bookworm-slim "$bookworm_old"
case_ "focal has no fixed build" fail ubuntu:20.04 "$focal_any"
case_ "alpine brotli-libs 1.2.0-r1 is caught" fail "$OLD_ALPINE" "" --check-only
case_ "alpine brotli-libs 1.2.0-r1 is upgraded" pass "$OLD_ALPINE" ""
case_ "no brotli installed is fine" pass debian:bookworm-slim "" --check-only

if [ $failures -ne 0 ]; then
  echo "❌ $failures security-upgrade case(s) failed"
  exit 1
fi
echo "✅ security-upgrade.sh: all cases pass"
