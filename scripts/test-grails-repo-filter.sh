#!/bin/bash
# Test for templates/scripts/grails-repo-filter.gradle.
#
# Runs Gradle in the gradle builder image against a fake repo.grails.org (the
# init script matches on that host name, so the container maps it to a local
# server) and checks which requests arrive:
#   - nothing under /grails/restricted
#   - nothing for au.org.ala.* or org.grails.plugins:ala-* on any repo.grails.org repo
#   - everything else (grails-mail, a third-party module) still asked of grails/core
# A control run without the init script must show the unwanted requests, so the
# test cannot pass just because Gradle never asked.
# Usage: scripts/test-grails-repo-filter.sh [builder-image]   (default gradle-builder:jdk17)

set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE=${1:-gradle-builder:jdk17}
PORT=18089
WORK=$(mktemp -d)
trap 'kill $SRV 2>/dev/null || true; rm -rf "$WORK"' EXIT

docker image inspect "$IMAGE" > /dev/null 2>&1 || { echo "❌ $IMAGE not found (build any gradle service first)"; exit 1; }

cat > "$WORK/server.py" <<'PY'
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        open(sys.argv[2], "a").write(self.path + "\n")
        self.send_response(404); self.end_headers()
    do_HEAD = do_GET
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY

cat > "$WORK/build.gradle" <<G
repositories {
    maven { url 'http://repo.grails.org:$PORT/grails/restricted'; allowInsecureProtocol = true }
    maven { url 'http://repo.grails.org:$PORT/grails/core'; allowInsecureProtocol = true }
}
configurations { probe }
dependencies {
    probe 'au.org.ala:alerts-lib:1'
    probe 'org.grails.plugins:ala-bootstrap5:1'
    probe 'org.grails.plugins:grails-mail:1'
    probe 'com.example:other:1'
}
task resolveProbe {
    doLast { configurations.probe.resolvedConfiguration.lenientConfiguration.artifacts }
}
G

run() { # run <log> [extra gradle args]
    local log=$1; shift
    : > "$log"
    python3 "$WORK/server.py" $PORT "$log" & SRV=$!
    sleep 1
    docker run --rm -u "$(id -u):$(id -g)" --network host --add-host repo.grails.org:127.0.0.1 \
        -v "$WORK:/p" -v "$REPO_ROOT/templates/scripts:/init:ro" -w /p \
        "$IMAGE" gradle -q --no-daemon -g /tmp/gh resolveProbe "$@" > "$WORK/gradle.out" 2>&1 || true
    kill $SRV; wait $SRV 2>/dev/null || true
}

fail=0
check() { if eval "$2"; then echo "✅ PASS: $1"; else echo "❌ FAIL: $1"; fail=1; fi; }

run "$WORK/control.log"
check "control (no init script) does ask for ala modules, so the probe is meaningful" \
    "grep -q 'ala-bootstrap5' '$WORK/control.log' && grep -q '/grails/restricted' '$WORK/control.log'"

run "$WORK/filtered.log" --init-script /init/grails-repo-filter.gradle
check "nothing is requested under /grails/restricted" \
    "! grep -q '/grails/restricted' '$WORK/filtered.log'"
check "no au.org.ala or ala-* module is requested from repo.grails.org" \
    "! grep -Eq 'au/org/ala|ala-bootstrap5' '$WORK/filtered.log'"
check "other modules are still resolved against grails/core" \
    "grep -q '/grails/core/org/grails/plugins/grails-mail' '$WORK/filtered.log' && grep -q '/grails/core/com/example/other' '$WORK/filtered.log'"

[ $fail -eq 0 ] && echo "✅ grails-repo-filter.gradle OK" || { echo "--- filtered requests:"; cat "$WORK/filtered.log"; echo "--- gradle output:"; tail -20 "$WORK/gradle.out"; exit 1; }
