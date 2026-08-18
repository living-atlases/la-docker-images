#!/bin/bash
# Container Structure Test Runner for Living Atlas Services
# Validates Docker images have required structure, permissions, tools
# Usage: ./scripts/run-container-tests.sh <service> <image-tag>
# Example: ./scripts/run-container-tests.sh cas 6.5.6-3

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SERVICE=$1
IMAGE_TAG=${2:-test}
REGISTRY=${REGISTRY:-}

# 128m expressed in bytes. A power of two is already heap-aligned, so the JVM
# reports MaxHeapSize back exactly instead of rounding it up.
JAVA_OPTS_PROBE_BYTES=134217728

# Validate inputs
if [ -z "$SERVICE" ]; then
  cat << EOF
❌ Usage: $0 <service> <image-tag>

Examples:
  $0 cas 6.5.6-3
  $0 collectory 3.3.1
  $0 cas test

Available services:
EOF
  ls "$REPO_ROOT/build/" | grep -v "^temp_" | sed 's/^/  /'
  exit 1
fi

# Check if service directory exists
if [ ! -d "$REPO_ROOT/build/$SERVICE" ]; then
  echo "❌ Service not found: $SERVICE"
  exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🧪 Container Structure Test: $SERVICE:$IMAGE_TAG"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Construct full image name
if [ -n "$REGISTRY" ]; then
  FULL_IMAGE="$REGISTRY/$SERVICE:$IMAGE_TAG"
else
  FULL_IMAGE="$SERVICE:$IMAGE_TAG"
fi

DOCKERFILE="$REPO_ROOT/build/$SERVICE/Dockerfile"
SERVICE_TEST_FILE="$REPO_ROOT/build/$SERVICE/container-test.yaml"
COMMON_TEST_FILE="$REPO_ROOT/common-tests/java-base-test.yaml"

echo "📦 Image: $FULL_IMAGE"
echo "📄 Dockerfile: $DOCKERFILE"
echo ""

# Simple validation tests based on Dockerfile inspection (no Docker required)
echo "🔍 Running Dockerfile structure validation..."

test_count=0
failed_tests=()

# Check 1: Verify service user is not root
if grep -q "^USER\s" "$DOCKERFILE"; then
  USER_LINE=$(grep "^USER\s" "$DOCKERFILE" | tail -1)
  if [[ $USER_LINE == *"root"* ]]; then
    echo "❌ FAIL: Container runs as root (security risk)"
    failed_tests+=("user-not-root")
  else
    echo "✅ PASS: Container runs as non-root user"
    test_count=$((test_count + 1))
  fi
else
  echo "⚠️  WARNING: No USER directive found (will run as root)"
  failed_tests+=("user-not-root")
fi

# Check 2: Verify EXPOSE port is defined
if grep -q "^EXPOSE\s" "$DOCKERFILE"; then
  echo "✅ PASS: EXPOSE port defined"
  test_count=$((test_count + 1))
else
  echo "⚠️  WARNING: No EXPOSE directive found"
fi

# Check 3: Verify WORKDIR is defined
if grep -q "^WORKDIR\s" "$DOCKERFILE"; then
  echo "✅ PASS: WORKDIR defined"
  test_count=$((test_count + 1))
else
  echo "⚠️  WARNING: No WORKDIR defined"
fi

# Check 4: Verify Java installation for Java services
if grep -q "eclipse-temurin\|openjdk\|JAVA_HOME" "$DOCKERFILE"; then
  echo "✅ PASS: Java base image or Java installation detected"
  test_count=$((test_count + 1))
  
  # Check for JAVA_HOME env
  if grep -q "JAVA_HOME" "$DOCKERFILE"; then
    echo "✅ PASS: JAVA_HOME environment variable set"
    test_count=$((test_count + 1))
  else
    echo "⚠️  WARNING: JAVA_HOME not explicitly set"
  fi
else
  echo "ℹ️  INFO: Non-Java service (skipping Java checks)"
fi

# Check 5: Verify directories for data persistence
if grep -q "/data" "$DOCKERFILE"; then
  echo "✅ PASS: Data directory defined"
  test_count=$((test_count + 1))
else
  echo "⚠️  WARNING: No /data directory found"
fi

# Check 6: Verify VOLUME definition
if grep -q "^VOLUME\s" "$DOCKERFILE"; then
  echo "✅ PASS: VOLUME defined for persistence"
  test_count=$((test_count + 1))
else
  echo "⚠️  WARNING: No VOLUME defined"
fi

# Check 7: Verify service-specific test file exists
if [ -f "$SERVICE_TEST_FILE" ]; then
  echo "✅ PASS: Service-specific test file found"
  echo "   Location: $SERVICE_TEST_FILE"
  test_count=$((test_count + 1))
else
  echo "⚠️  WARNING: No service-specific test file"
  echo "   Expected: $SERVICE_TEST_FILE"
  echo "   (Run: docker inspect $FULL_IMAGE to validate further)"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# The checks above only read the Dockerfile. The ones below need the built image,
# so they are skipped when Docker is unavailable or the image is not present.
if ! command -v docker &> /dev/null || ! docker ps > /dev/null 2>&1; then
  echo "ℹ️  INFO: Docker not accessible, skipping image-level tests"
elif ! docker image inspect "$FULL_IMAGE" > /dev/null 2>&1; then
  echo "ℹ️  INFO: Image $FULL_IMAGE not found locally, skipping image-level tests"
  echo "   Build it first: ./build.py --service=$SERVICE"
else
  echo "🐳 Running image-level tests against $FULL_IMAGE..."

  IMAGE_CMD=$(docker inspect -f '{{json .Config.Cmd}}' "$FULL_IMAGE")

  # gh-3: build.py renders the templates through string.Template.safe_substitute,
  # so a bare ${JAVA_OPTS} in the template CMD is replaced at GENERATION time and
  # the computed defaults are frozen into the image. The container then ignores
  # whatever JAVA_OPTS the compose environment sets, which silently disabled every
  # *_max_memory / *_min_memory override. The CMD has to keep the literal for the
  # shell to expand at container start.
  #
  # This lives here rather than in common-tests/: a commandTest runs its own
  # command and never touches the image CMD, and metadataTest.cmd matches the CMD
  # exactly (no regex), which a file shared by every service cannot do.
  if [[ $IMAGE_CMD == *'${JAVA_OPTS}'* ]]; then
    echo "✅ PASS: CMD keeps a literal \${JAVA_OPTS} to expand at container start"
    test_count=$((test_count + 1))
  else
    echo "❌ FAIL: CMD has no \${JAVA_OPTS}, so runtime overrides are never read"
    echo "   CMD: $IMAGE_CMD"
    failed_tests+=("java-opts-cmd-expands")
  fi

  # Same regression seen from the other side.
  if [[ $IMAGE_CMD =~ -Xm[sx][0-9]|-Xss[0-9] ]]; then
    echo "❌ FAIL: CMD has JVM memory flags baked in at build time"
    echo "   CMD: $IMAGE_CMD"
    failed_tests+=("java-opts-cmd-not-baked")
  else
    echo "✅ PASS: CMD has no JVM memory flags baked in"
    test_count=$((test_count + 1))
  fi

  # End-to-end: run the image's own CMD with an override in the environment and
  # ask the JVM what heap it actually got. PrintFlagsFinal reports at JVM init,
  # before the app starts, so the container is killed straight after.
  echo "   ⏳ Starting container to check the override reaches the JVM..."
  runtime_out=$(timeout 180 docker run --rm \
    -e JAVA_OPTS="-Xmx128m -XX:+PrintFlagsFinal" \
    "$FULL_IMAGE" 2>&1 | grep -m1 'MaxHeapSize' || true)

  if [[ $runtime_out == *"$JAVA_OPTS_PROBE_BYTES"* ]]; then
    echo "✅ PASS: JAVA_OPTS override reaches the JVM (MaxHeapSize=$JAVA_OPTS_PROBE_BYTES)"
    test_count=$((test_count + 1))
  else
    echo "❌ FAIL: JAVA_OPTS override did not reach the JVM"
    echo "   Expected MaxHeapSize=$JAVA_OPTS_PROBE_BYTES, got: ${runtime_out:-<no MaxHeapSize in output>}"
    failed_tests+=("java-opts-runtime")
  fi

  # Structural tests, if the vendored binary is there.
  if [ -x "$REPO_ROOT/container-structure-test" ]; then
    CST_CONFIG="$COMMON_TEST_FILE"
    [ -f "$SERVICE_TEST_FILE" ] && CST_CONFIG="$SERVICE_TEST_FILE"
    echo "   ⏳ container-structure-test with $(basename "$CST_CONFIG")..."
    if "$REPO_ROOT/container-structure-test" test --image "$FULL_IMAGE" --config "$CST_CONFIG" > /dev/null 2>&1; then
      echo "✅ PASS: container-structure-test"
      test_count=$((test_count + 1))
    else
      echo "❌ FAIL: container-structure-test (rerun without -q for detail):"
      echo "   $REPO_ROOT/container-structure-test test --image $FULL_IMAGE --config $CST_CONFIG"
      failed_tests+=("container-structure-test")
    fi
  else
    echo "⚠️  WARNING: container-structure-test binary not found, skipping structural tests"
  fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ ${#failed_tests[@]} -eq 0 ]; then
  echo "✅ Dockerfile validation PASSED for $SERVICE"
  echo "   Tests run: $test_count"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  exit 0
else
  echo "❌ Dockerfile validation FAILED for $SERVICE"
  echo "   Failed checks:"
  for test in "${failed_tests[@]}"; do
    echo "     - $test"
  done
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  exit 1
fi
