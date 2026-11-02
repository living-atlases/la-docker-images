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
#   2026-11-02: one base layer went inconsistent in the daemon's snapshot
#   metadata (TASK-5):
#     failed to prepare extraction snapshot ...: missing parent
#     "moby/3052/sha256:97e4ece8..." bucket: not found
#
# The second one is why this script probes a LIST of base images instead of
# one. On 2026-11-02 the wedged layer was busybox's, which was this check's
# only probe image, so the check failed the whole run (#295-#316) while the
# agent could build everything else: debian:bookworm-slim pulled and built
# fine throughout, and `ctr -n moby snapshots info` could neither find that
# layer nor let docker recreate it ("AlreadyExists") -- after an image prune
# and after 98GB of build cache was pruned. A wedged probe image is the
# check's problem, not the run's: say so and carry on.
#
# So the probe reports WHICH of the three it is:
#   dns     - no image can resolve the artifact hosts. Nothing will build.
#   cache   - no probe image can be extracted. The agent needs a human.
#   wedged  - some probe image cannot, another can. Warn, and build anyway.
set -eu

HOSTS=${BUILD_AGENT_HOSTS:-"nexus.ala.org.au deb.debian.org"}
ATTEMPTS=${BUILD_AGENT_ATTEMPTS:-3}
PAUSE=${BUILD_AGENT_PAUSE:-15}
# BUILD_AGENT_PROBE_IMAGE (singular) stays accepted: it pins the probe to one
# image, which is what the tests and a manual check want.
PROBE_IMAGES=${BUILD_AGENT_PROBE_IMAGES:-${BUILD_AGENT_PROBE_IMAGE:-"busybox:stable alpine:3 debian:bookworm-slim"}}
PRUNE_TIMEOUT=${BUILD_AGENT_PRUNE_TIMEOUT:-600}
DOCKER=${DOCKER:-docker}

context=$(mktemp -d)
trap 'rm -rf "$context"' EXIT

probe() {
  {
    echo "FROM $1"
    echo "RUN for h in $HOSTS; do nslookup \$h >/dev/null 2>&1 || { echo \"cannot resolve \$h\" >&2; exit 1; }; done"
  } > "$context/Dockerfile"
  $DOCKER build --quiet --no-cache -f "$context/Dockerfile" "$context" >/dev/null 2>"$context/err"
}

classify() {
  if grep -q "cannot resolve" "$context/err"; then
    echo dns
  elif grep -qE "missing parent|bucket: not found|failed to prepare extraction snapshot|target snapshot .* already exists" "$context/err"; then
    echo cache
  else
    echo unknown
  fi
}

pruned=no
wedged=""
reason=unknown

for image in $PROBE_IMAGES; do
  attempt=1
  while [ "$attempt" -le "$ATTEMPTS" ]; do
    if probe "$image"; then
      echo "build agent: ok, containers build and resolve $HOSTS (probe $image)"
      if [ -n "$wedged" ]; then
        cat >&2 <<EOF
build agent: these probe images cannot be extracted here:$wedged
Their layers are wedged in the daemon's snapshot metadata, and that is not the
build cache: on 2026-11-02 busybox:stable's layer survived an image prune and a
98GB cache prune. The agent builds everything else, so this run goes on.
To repair it when the agent is idle, as root:
  systemctl restart docker containerd   (the snapshot buckets are reloaded)
  docker pull$wedged                  (confirms it took)
EOF
      fi
      exit 0
    fi

    echo "build agent: probe $image, attempt $attempt of $ATTEMPTS failed" >&2
    sed 's/^/   /' "$context/err" >&2
    reason=$(classify)

    case $reason in
      dns)
        # Agent-wide: no other probe image resolves any better.
        break 2
        ;;
      cache)
        # The build cache is the one thing here that is safe to throw away, and
        # a stale snapshot parent can live in it. Capped and quiet: on
        # 2026-11-02 this took 38 minutes and printed one line per entry for
        # 98GB, inside a run that was going to fail anyway. Worth trying once
        # -- it is what fixed October's incident -- but not worth holding the
        # executor for an hour. Images are left alone: they are the layer cache
        # every other job on this agent shares.
        if [ "$pruned" = no ]; then
          echo "build agent: snapshot metadata is inconsistent; clearing the build cache" >&2
          timeout "$PRUNE_TIMEOUT" $DOCKER builder prune -af >/dev/null 2>&1 || true
          pruned=yes
          continue
        fi
        $DOCKER rmi -f "$image" >/dev/null 2>&1 || true
        wedged="$wedged $image"
        break
        ;;
    esac

    attempt=$((attempt + 1))
    if [ "$attempt" -le "$ATTEMPTS" ]; then sleep "$PAUSE"; fi
  done
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
build agent: no probe image can be extracted, so nothing will build here.
Not the build cache: build #312 pruned 98GB and the same parent was still
missing, and an image prune left the store empty with the fault intact. The
broken buckets are in the daemon's snapshot metadata, so on the agent, as root:
  systemctl restart docker containerd   (the snapshot buckets are reloaded)
  docker pull busybox:stable            (confirms it took)
Nothing here can do that: it affects every job on this agent.
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
