# syntax=docker/dockerfile:1.14.0
# ALA i18n Data Image
# Pre-installs ala-i18n package to be used as init container or data volume

ARG DEBIAN_VERSION=bookworm
FROM debian:${DEBIAN_VERSION}-slim

# Build argument for apt repository (unstable, testing, stable)
ARG LA_APT_REPO=unstable

# Build argument to invalidate cache and force fresh apt-get update
# Usage: docker build --build-arg CACHE_BYPASS=$(date +%s) ...
ARG CACHE_BYPASS=unknown

LABEL org.opencontainers.image.title="ALA i18n Data"
LABEL org.opencontainers.image.description="Atlas of Living Australia i18n translations package"
LABEL org.opencontainers.image.source="https://github.com/living-atlases/ala-i18n"

# The fingerprint of the key aptly signs apt.gbif.es's Release with. Pinned
# here because the key is fetched by a 32-hex search term, which is not a
# fingerprint: whatever the keyserver answers with used to be trusted as-is.
ARG APT_GBIF_ES_FPR=A04AE8DD5C8EEB896B0E004AF697D8D2ADB9E24A

# Install ala-i18n package following roles/la-apt + roles/i18n pattern
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
  --mount=type=cache,target=/var/lib/apt,sharing=locked \
  apt-get update && \
  apt-get install -y --no-install-recommends \
  wget \
  gnupg \
  ca-certificates && \
  # Fetch the apt.gbif.es key and refuse it unless it is the pinned one.
  wget -qO /tmp/apt-gbif-es.asc \
  'https://keyserver.ubuntu.com/pks/lookup?op=hget&search=7524d3b383016eab0ee47bfe253a51f7' && \
  gpg --show-keys --with-colons /tmp/apt-gbif-es.asc \
  | awk -F: '/^fpr:/ {print $10; exit}' > /tmp/apt-gbif-es.fpr && \
  if [ "$(cat /tmp/apt-gbif-es.fpr)" != "${APT_GBIF_ES_FPR}" ]; then \
  echo "apt.gbif.es key is $(cat /tmp/apt-gbif-es.fpr), expected ${APT_GBIF_ES_FPR}" >&2; \
  exit 1; \
  fi && \
  install -D -m 0644 /tmp/apt-gbif-es.asc /etc/apt/keyrings/apt-gbif-es.asc && \
  rm -f /tmp/apt-gbif-es.asc /tmp/apt-gbif-es.fpr && \
  # signed-by, not trusted.gpg.d: a key dropped in trusted.gpg.d can sign for
  # EVERY repository in the image, Debian's own included. This one only gets
  # to vouch for apt.gbif.es.
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/apt-gbif-es.asc] https://apt.gbif.es/ ${LA_APT_REPO} main" > /etc/apt/sources.list.d/apt_gbif_es.list && \
  # Install ala-i18n package (following roles/i18n/tasks/main.yml)
  # Using CACHE_BYPASS logic here if needed, but --no-cache is cleaner
  apt-get update && \
  apt-get install -y --no-install-recommends ala-i18n && \
  # wget and gnupg were only needed for the key above; apt verifies the repo
  # with gpgv, which it depends on. Nothing in the running image downloads.
  apt-get purge -y --auto-remove wget gnupg && \
  # Cleanup
  rm -rf /var/lib/apt/lists/*

# Verify installation
RUN test -d /opt/atlas/i18n && \
  ls -la /opt/atlas/i18n/ && \
  echo "✅ ala-i18n package installed successfully (cache bypass: ${CACHE_BYPASS})"

# Clean on bookworm today only because its ca-certificates is fresh and it has
# no brotli at all -- with nothing enforcing it, the next base bump could bring
# either back unnoticed. Same two scripts as every generated image.
COPY scripts/security-upgrade.sh /usr/local/bin/security-upgrade.sh
RUN echo "cache bypass: ${CACHE_BYPASS}" && sh /usr/local/bin/security-upgrade.sh

COPY scripts/ca-distrust.sh /usr/local/bin/ca-distrust.sh
RUN sh /usr/local/bin/ca-distrust.sh

# The container doesn't need to run anything, it just provides the data
# It will be used as an init container or for data volume copying
CMD ["echo", "ala-i18n data container ready"]
