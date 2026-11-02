#!/bin/sh
# Can a build container resolve the hosts a build needs?
#
# On 2026-11-01 the agent's containers lost DNS for eight hours. Every image
# died on "curl: (6) Could not resolve host: nexus.ala.org.au", and because
# build.py reports a failed download as a failed image, twelve runs spent ~40
# minutes each to report that every tag of a service was broken. They were not:
# the network was.
#
# Checked with `docker build`, not `docker run`, because that is where the
# builds failed. A few retries ride out a blip; a longer outage fails here, in
# seconds, with a message that names the real problem.
set -eu

HOSTS=${BUILD_NETWORK_HOSTS:-"nexus.ala.org.au deb.debian.org"}
ATTEMPTS=${BUILD_NETWORK_ATTEMPTS:-3}
PAUSE=${BUILD_NETWORK_PAUSE:-15}
PROBE_IMAGE=${BUILD_NETWORK_PROBE_IMAGE:-busybox:stable}

context=$(mktemp -d)
trap 'rm -rf "$context"' EXIT

{
  echo "FROM $PROBE_IMAGE"
  echo "RUN for h in $HOSTS; do nslookup \$h >/dev/null 2>&1 || { echo \"cannot resolve \$h\" >&2; exit 1; }; done"
} > "$context/Dockerfile"

attempt=1
while [ "$attempt" -le "$ATTEMPTS" ]; do
  if docker build --quiet --no-cache -f "$context/Dockerfile" "$context" >/dev/null 2>"$context/err"; then
    echo "build network: containers resolve $HOSTS"
    exit 0
  fi
  echo "build network: attempt $attempt of $ATTEMPTS failed" >&2
  sed 's/^/   /' "$context/err" >&2
  attempt=$((attempt + 1))
  [ "$attempt" -le "$ATTEMPTS" ] && sleep "$PAUSE"
done

cat >&2 <<EOF
build network: containers on this agent cannot resolve $HOSTS.
Every image would fail downloading its artifact, and each failure would be
reported against the service rather than against the agent. Stopping here.
Check the docker daemon's DNS (/etc/docker/daemon.json, the host resolver)
before rebuilding.
EOF
exit 1
