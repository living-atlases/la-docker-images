#!/bin/sh
# Can this agent build at all? Checked before a run spends hours finding out.
#
# Two outages, two different causes, the same symptom -- every image in the run
# "fails":
#
#   2026-11-01: containers lost DNS for eight hours. Builds #281-#292 each
#   spent ~40 minutes reporting that every tag of a service was broken.
#     curl: (6) Could not resolve host: nexus.ala.org.au
#
#   2026-11-02: BuildKit's snapshot metadata went inconsistent again (TASK-5,
#   first seen in #243-#249). Builds #295-#311 died on the probe:
#     failed to prepare extraction snapshot ...: missing parent
#     "moby/3052/sha256:..." bucket: not found
#
# So the probe says WHICH of the two it is, and for the second one it clears
# the build cache and tries again -- that is what fixed it in October. The
# cache is only a cache: the next build is slower, not wrong.
set -eu

HOSTS=${BUILD_AGENT_HOSTS:-"nexus.ala.org.au deb.debian.org"}
ATTEMPTS=${BUILD_AGENT_ATTEMPTS:-3}
PAUSE=${BUILD_AGENT_PAUSE:-15}
PROBE_IMAGE=${BUILD_AGENT_PROBE_IMAGE:-busybox:stable}
DOCKER=${DOCKER:-docker}

context=$(mktemp -d)
trap 'rm -rf "$context"' EXIT

{
  echo "FROM $PROBE_IMAGE"
  echo "RUN for h in $HOSTS; do nslookup \$h >/dev/null 2>&1 || { echo \"cannot resolve \$h\" >&2; exit 1; }; done"
} > "$context/Dockerfile"

pruned=no
attempt=1
while [ "$attempt" -le "$ATTEMPTS" ]; do
  if $DOCKER build --quiet --no-cache -f "$context/Dockerfile" "$context" >/dev/null 2>"$context/err"; then
    echo "build agent: ok, containers build and resolve $HOSTS"
    exit 0
  fi

  echo "build agent: attempt $attempt of $ATTEMPTS failed" >&2
  sed 's/^/   /' "$context/err" >&2

  if grep -q "cannot resolve" "$context/err"; then
    reason=dns
  elif grep -qE "missing parent|bucket: not found|failed to prepare extraction snapshot" "$context/err"; then
    reason=cache
  else
    reason=unknown
  fi

  # The build cache is the one thing here that is safe to throw away, and a
  # stale snapshot parent is exactly what it holds. Images are left alone:
  # they are the layer cache every other job on this agent shares.
  if [ "$reason" = cache ] && [ "$pruned" = no ]; then
    echo "build agent: BuildKit snapshot metadata is inconsistent; clearing the build cache" >&2
    $DOCKER builder prune -af >&2 || true
    $DOCKER rmi -f "$PROBE_IMAGE" >&2 2>/dev/null || true
    pruned=yes
    continue
  fi

  attempt=$((attempt + 1))
  [ "$attempt" -le "$ATTEMPTS" ] && sleep "$PAUSE"
done

case ${reason:-unknown} in
  dns)
    cat >&2 <<EOF
build agent: containers cannot resolve $HOSTS.
Every image would fail to download its artifact, and each failure would be
filed against the service instead of against the agent. Stopping here.
Check the docker daemon's DNS (/etc/docker/daemon.json, the host resolver).
EOF
    ;;
  cache)
    cat >&2 <<EOF
build agent: BuildKit still cannot extract an image after clearing its cache.
This is TASK-5 again: the snapshot metadata of the docker daemon is
inconsistent ("missing parent ... bucket: not found") and no build can run.
On the agent: docker builder prune -af, and if it persists restart dockerd so
containerd rebuilds its snapshot buckets.
EOF
    ;;
  *)
    cat >&2 <<EOF
build agent: the probe build fails for a reason this check does not recognise.
The error is above. Nothing was built; fix the agent before rebuilding.
EOF
    ;;
esac
exit 1
