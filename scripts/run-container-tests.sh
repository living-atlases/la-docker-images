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
    ((test_count++))
  fi
else
  echo "⚠️  WARNING: No USER directive found (will run as root)"
  failed_tests+=("user-not-root")
fi

# Check 2: Verify EXPOSE port is defined
if grep -q "^EXPOSE\s" "$DOCKERFILE"; then
  echo "✅ PASS: EXPOSE port defined"
  ((test_count++))
else
  echo "⚠️  WARNING: No EXPOSE directive found"
fi

# Check 3: Verify WORKDIR is defined
if grep -q "^WORKDIR\s" "$DOCKERFILE"; then
  echo "✅ PASS: WORKDIR defined"
  ((test_count++))
else
  echo "⚠️  WARNING: No WORKDIR defined"
fi

# Check 4: Verify Java installation for Java services
if grep -q "eclipse-temurin\|openjdk\|JAVA_HOME" "$DOCKERFILE"; then
  echo "✅ PASS: Java base image or Java installation detected"
  ((test_count++))
  
  # Check for JAVA_HOME env
  if grep -q "JAVA_HOME" "$DOCKERFILE"; then
    echo "✅ PASS: JAVA_HOME environment variable set"
    ((test_count++))
  else
    echo "⚠️  WARNING: JAVA_HOME not explicitly set"
  fi
else
  echo "ℹ️  INFO: Non-Java service (skipping Java checks)"
fi

# Check 5: Verify directories for data persistence
if grep -q "/data" "$DOCKERFILE"; then
  echo "✅ PASS: Data directory defined"
  ((test_count++))
else
  echo "⚠️  WARNING: No /data directory found"
fi

# Check 6: Verify VOLUME definition
if grep -q "^VOLUME\s" "$DOCKERFILE"; then
  echo "✅ PASS: VOLUME defined for persistence"
  ((test_count++))
else
  echo "⚠️  WARNING: No VOLUME defined"
fi

# Check 7: Verify service-specific test file exists
if [ -f "$SERVICE_TEST_FILE" ]; then
  echo "✅ PASS: Service-specific test file found"
  echo "   Location: $SERVICE_TEST_FILE"
  ((test_count++))
else
  echo "⚠️  WARNING: No service-specific test file"
  echo "   Expected: $SERVICE_TEST_FILE"
  echo "   (Run: docker inspect $FULL_IMAGE to validate further)"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# If user has Docker access, offer to run full tests
if command -v docker &> /dev/null && docker ps > /dev/null 2>&1; then
  echo ""
  echo "✨ Docker socket accessible! Full validation available."
  echo ""
  echo "To run comprehensive Container Structure Tests:"
  echo ""
  echo "  1. Download and setup container-structure-test:"
  echo "     cd $REPO_ROOT"
  echo "     curl -sSL https://storage.googleapis.com/container-structure-test/latest/container-structure-test-linux-amd64 \\"
  echo "       -o container-structure-test"
  echo "     chmod +x container-structure-test"
  echo ""
  echo "  2. Run tests against running image:"
  echo "     ./container-structure-test test --image $FULL_IMAGE --config $SERVICE_TEST_FILE"
  echo ""
  echo "  3. To test before building:"
  echo "     docker build -t $SERVICE:test ./build/$SERVICE"
  echo "     ./container-structure-test test --image $SERVICE:test --config build/$SERVICE/container-test.yaml"
  echo ""
elif [ -f "$REPO_ROOT/container-structure-test" ]; then
  echo ""
  echo "⚠️  Docker socket not accessible, but CST tool is available."
  echo "   Run with sudo or ensure Docker group membership:"
  echo "     sudo $REPO_ROOT/container-structure-test test --image $FULL_IMAGE --config $SERVICE_TEST_FILE"
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
