#!/bin/sh
# Apply the distro's pending updates to the runtime image, then fail if the
# brotli decoder library is still older than the fixed build for its release.
# First case: CVE-2026-111497 (out-of-bounds write in the brotli decoder).
# Base images lag behind the distro (livingatlases/image-service:4.2.0-SNAPSHOT
# still had libbrotli1 1.0.9-2build6 on jammy), so a rebuild with --pull alone
# does not guarantee the fix.
#
#   1. upgrade every package with the image's package manager
#   2. verify libbrotli1 (Debian, Ubuntu) or brotli-libs (Alpine) against the
#      minimum for the release; a release with no fixed build fails
#
# Usage: security-upgrade.sh [--check-only]   (--check-only skips step 1)
set -u

# --- 1. upgrade ------------------------------------------------------------
if [ "${1:-}" != "--check-only" ]; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -q >/dev/null 2>&1 || { echo "security-upgrade: apt-get update failed" >&2; exit 1; }
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -q -o Dpkg::Options::=--force-confold >/dev/null ||
      { echo "security-upgrade: apt-get upgrade failed" >&2; exit 1; }
    rm -rf /var/lib/apt/lists/*
  elif command -v apk >/dev/null 2>&1; then
    apk upgrade --no-cache -q >/dev/null || { echo "security-upgrade: apk upgrade failed" >&2; exit 1; }
  elif command -v microdnf >/dev/null 2>&1; then
    microdnf update -y >/dev/null 2>&1 || { echo "security-upgrade: microdnf update failed" >&2; exit 1; }
  elif command -v yum >/dev/null 2>&1; then
    yum update -y -q >/dev/null 2>&1 || { echo "security-upgrade: yum update failed" >&2; exit 1; }
  fi
fi

# --- 2. verify -------------------------------------------------------------
# Fixed builds: DLA-4855-1, DSA-6582-1, USN-8956-1, upstream 1.2.1 (Alpine).
have=""
codename=$( . /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-${ID:-unknown}}")
if command -v dpkg-query >/dev/null 2>&1; then
  have=$(dpkg-query -W -f='${Status} ${Version}\n' libbrotli1 2>/dev/null | sed -n 's/^install ok installed //p')
  if [ -n "$have" ]; then
    case "$codename" in
      jammy) min=1.0.9-2ubuntu0.1 ;;
      noble) min=1.1.0-2ubuntu0.1 ;;
      bookworm) min=1.0.9-2+deb12u1 ;;
      trixie) min=1.1.0-2+deb13u1 ;;
      *) echo "security-upgrade: libbrotli1 $have on $codename, a release with no known fixed build" >&2; exit 1 ;;
    esac
    dpkg --compare-versions "$have" ge "$min" ||
      { echo "security-upgrade: libbrotli1 $have on $codename, below the fixed $min" >&2; exit 1; }
  fi
elif command -v apk >/dev/null 2>&1; then
  have=$(apk info -e -v brotli-libs 2>/dev/null | sed 's/^brotli-libs-//')
  if [ -n "$have" ]; then
    [ "$(apk version -t "$have" 1.2.1-r0)" != "<" ] ||
      { echo "security-upgrade: brotli-libs $have, below the fixed 1.2.1-r0" >&2; exit 1; }
  fi
fi
echo "security-upgrade: OK (${codename}${have:+, brotli $have})"
