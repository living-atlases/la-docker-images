# syntax=docker/dockerfile:1.14.0
ARG JAVA_VERSION=17
ARG BUILDER_IMAGE=maven-builder:jdk${JAVA_VERSION}
ARG BUILD_METHOD=debian-package

# ── No-op stage (used when BUILD_METHOD=debian-package) ──────────────────────
FROM busybox AS builder-debian-package
RUN mkdir -p /app

# ── Source-build stage (only used when BUILD_METHOD=repo-branch) ──────────────
FROM ${BUILDER_IMAGE} AS builder-repo-branch

ARG BRANCH=${BRANCH}
ARG VERSION=${VERSION}
ARG COMMIT=HEAD
ARG REPO=${REPO}
ARG SOURCE_DATE_EPOCH

WORKDIR /app
COPY settings.xml /build/settings.xml
RUN git clone --branch ${BRANCH} ${REPO} . && \
  if [ "${COMMIT}" != "HEAD" ]; then git checkout ${COMMIT}; fi

RUN --mount=type=cache,target=/root/.m2 \
  mvn --batch-mode --no-transfer-progress \
  -s /build/settings.xml \
  -Daether.connector.http.connectTimeout=30000 \
  -Daether.connector.http.requestTimeout=180000 \
  -Daether.connector.http.retryHandler.count=5 \
  -Daether.connector.connectTimeout=30000 \
  -Daether.connector.requestTimeout=180000 \
  clean install \
  -Dspotless.check.skip=true -Dmaven.test.skip=true -Dmaven.site.skip=true -Dmaven.javadoc.skip=true \
  -Dproject.build.outputTimestamp=${SOURCE_DATE_EPOCH} \
  -T 10 -U \
  && cd livingatlas \
  && mvn --batch-mode --no-transfer-progress \
  -s /build/settings.xml \
  -Daether.connector.http.connectTimeout=30000 \
  -Daether.connector.http.requestTimeout=180000 \
  -Daether.connector.http.retryHandler.count=5 \
  -Daether.connector.connectTimeout=30000 \
  -Daether.connector.requestTimeout=180000 \
  clean package -P livingatlas-artifacts \
  -Dspotless.check.skip=true -Dmaven.test.skip=true -Dmaven.site.skip=true -Dmaven.javadoc.skip=true \
  -Dproject.build.outputTimestamp=${SOURCE_DATE_EPOCH} \
  -T 10 -U

# ── Select artifact source based on BUILD_METHOD ─────────────────────────────
FROM builder-${BUILD_METHOD} AS artifact-src

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM eclipse-temurin:${JAVA_VERSION}-jre-jammy

ARG BUILD_METHOD=debian-package
ARG VERSION=latest
ARG LA_APT_REPO=unstable
ARG TARGETARCH

LABEL ala.version=${VERSION}

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
  export DEBIAN_FRONTEND=noninteractive && \
  apt-get update && \
  apt-get install -y --no-install-recommends --fix-missing \
  nano \
  file \
  curl \
  wget \
  gnupg \
  ca-certificates \
  unzip \
  debianutils && \
  rm -rf /var/lib/apt/lists/*

RUN mkdir /data && chown 1000:1000 /data
# No `VOLUME /data`: it discards build-time writes under /data and creates
# anonymous volumes when run without compose. The real mount is provided by
# la-docker-compose; runtime config defaults live in /usr/share (see below).

# Install yq
RUN curl -o /usr/bin/yq -LJO https://github.com/mikefarah/yq/releases/download/v4.44.3/yq_linux_${TARGETARCH:-amd64} \
  && chmod +x /usr/bin/yq

# Install docopts
RUN if [ "${TARGETARCH:-amd64}" = "amd64" ] ; then curl -o /usr/bin/docopts -LJO https://github.com/docopt/docopts/releases/download/v0.6.3-rc2/docopts_linux_amd64 ; \
  else curl -o /usr/bin/docopts -LJO https://github.com/docopt/docopts/releases/download/v0.6.3-rc2/docopts_linux_arm ; fi \
  && chmod +x /usr/bin/docopts

# Create the user
RUN groupadd --gid 1000 app \
  && useradd --uid 1000 --gid 1000 -m -d /app app

# ── debian-package: install la-pipelines from apt.gbif.es ────────────────────
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
  --mount=type=cache,target=/var/lib/apt,sharing=locked \
  if [ "${BUILD_METHOD}" = "debian-package" ]; then \
    export DEBIAN_FRONTEND=noninteractive && \
    wget -qO /etc/apt/trusted.gpg.d/apt-gbif-es.asc \
      'https://keyserver.ubuntu.com/pks/lookup?op=hget&search=7524d3b383016eab0ee47bfe253a51f7' && \
    echo "deb [arch=amd64] https://apt.gbif.es/ ${LA_APT_REPO} main" > /etc/apt/sources.list.d/apt_gbif_es.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends la-pipelines && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /usr/share/la-pipelines/config-defaults && \
    cp -a /data/la-pipelines/config/. /usr/share/la-pipelines/config-defaults/ && \
    chown -R 1000:1000 /usr/share/la-pipelines ; \
  fi

# ── repo-branch: place built artifacts in debian-package layout ──────────────
COPY --from=artifact-src --chown=1000:1000 /app /app-src

USER root
RUN if [ "${BUILD_METHOD}" != "debian-package" ]; then \
    set -eu && \
    mkdir -p /usr/share/la-pipelines/config-defaults /usr/share/bash-completion/completions && \
    cp /app-src/livingatlas/pipelines/target/pipelines-*-shaded.jar /usr/share/la-pipelines/la-pipelines.jar && \
    cp /app-src/livingatlas/scripts/la-pipelines /usr/bin/la-pipelines && \
    chmod +x /usr/bin/la-pipelines && \
    sed -i "s#VER=\$(dpkg-query --show -f '\${Version}' la-pipelines)#VER=\$(dpkg-query --show -f '\${Version}' la-pipelines 2>/dev/null || true)#" /usr/bin/la-pipelines && \
    cp /app-src/livingatlas/scripts/logging_lib.sh /usr/share/la-pipelines/ && \
    cp /app-src/livingatlas/configs/la-pipelines.yaml /usr/share/la-pipelines/config-defaults/ && \
    cp /app-src/livingatlas/configs/la-pipelines-local.yaml /usr/share/la-pipelines/config-defaults/la-pipelines-local.yaml.sample && \
    cp /app-src/livingatlas/pipelines/src/main/resources/log4j-colorized.properties /usr/share/la-pipelines/config-defaults/ && \
    cp /app-src/livingatlas/pipelines/src/main/resources/log4j.properties /usr/share/la-pipelines/config-defaults/ && \
    cp /app-src/livingatlas/pipelines/src/main/resources/logback.xml /usr/share/la-pipelines/config-defaults/ && \
    cp /app-src/livingatlas/scripts/la-pipelines-bash-completion.sh /usr/share/bash-completion/completions/la-pipelines && \
    chown -R 1000:1000 /usr/share/la-pipelines ; \
  fi && rm -rf /app-src

# ── Runtime seeding entrypoint (replicates the .deb .install + postinst) ─────
# /data/la-pipelines/config is a bind-mount at runtime, so baked config is
# shadowed. Ship defaults outside the volume and seed missing files on start,
# respecting whatever Ansible already deployed (copy-if-absent).
COPY <<'EOF' /usr/local/bin/docker-entrypoint.sh
#!/usr/bin/env bash
set -e
CONF=/data/la-pipelines/config
DEF=/usr/share/la-pipelines/config-defaults
mkdir -p "$CONF"
for f in la-pipelines.yaml logback.xml log4j.properties log4j-colorized.properties; do
  if [ -f "$DEF/$f" ] && [ ! -f "$CONF/$f" ]; then cp "$DEF/$f" "$CONF/$f"; fi
done
if [ ! -f "$CONF/la-pipelines-local.yaml" ] && [ -f "$DEF/la-pipelines-local.yaml.sample" ]; then
  cp "$DEF/la-pipelines-local.yaml.sample" "$CONF/la-pipelines-local.yaml"
fi
exec "$@"
EOF
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER 1000

ENV TZ="Europe/Paris"
ENV EXTRA_JVM_CLI_ARGS="--add-opens=java.base/java.lang=ALL-UNNAMED \
  --add-opens=java.base/java.lang.invoke=ALL-UNNAMED \
  --add-opens=java.base/java.lang.reflect=ALL-UNNAMED \
  --add-opens=java.base/java.io=ALL-UNNAMED \
  --add-opens=java.base/java.net=ALL-UNNAMED \
  --add-opens=java.base/java.nio=ALL-UNNAMED \
  --add-opens=java.base/java.util=ALL-UNNAMED \
  --add-opens=java.base/java.util.concurrent=ALL-UNNAMED \
  --add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED \
  --add-opens=java.base/sun.nio.ch=ALL-UNNAMED \
  --add-opens=java.base/sun.nio.cs=ALL-UNNAMED \
  --add-opens=java.base/sun.security.action=ALL-UNNAMED \
  --add-opens=java.base/sun.util.calendar=ALL-UNNAMED \
  --add-opens=java.security.jgss/sun.security.krb5=ALL-UNNAMED"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["/bin/bash"]
