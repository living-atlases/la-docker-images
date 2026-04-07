# syntax=docker/dockerfile:1.14.0
ARG JAVA_VERSION=17
ARG BUILDER_IMAGE=maven-builder:jdk${JAVA_VERSION}
ARG BUILD_METHOD=debian-package

# ── Source-build stage (only used when BUILD_METHOD=repo-branch) ──────────────
FROM ${BUILDER_IMAGE} AS builder-repo-branch

ARG BRANCH=${BRANCH}
ARG VERSION=${VERSION}
ARG COMMIT=HEAD
ARG REPO=${REPO}
ARG SOURCE_DATE_EPOCH

WORKDIR /app
RUN git clone --branch ${BRANCH} ${REPO} . && \
  if [ "${COMMIT}" != "HEAD" ]; then git checkout ${COMMIT}; fi

RUN --mount=type=cache,target=/root/.m2 \
  mvn --batch-mode --no-transfer-progress \
  clean install \
  -Dspotless.check.skip=true -Dmaven.test.skip=true -Dmaven.site.skip=true -Dmaven.javadoc.skip=true \
  -Dproject.build.outputTimestamp=${SOURCE_DATE_EPOCH} \
  -T 10 -U \
  && cd livingatlas \
  && mvn --batch-mode --no-transfer-progress \
  clean package -P livingatlas-artifacts \
  -Dspotless.check.skip=true -Dmaven.test.skip=true -Dmaven.site.skip=true -Dmaven.javadoc.skip=true \
  -Dproject.build.outputTimestamp=${SOURCE_DATE_EPOCH} \
  -T 10 -U

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
VOLUME /data

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
    rm -rf /var/lib/apt/lists/* ; \
  fi

# ── repo-branch: copy built artifacts ────────────────────────────────────────
COPY --from=builder-repo-branch --chown=1000:1000 /app /app-src

RUN if [ "${BUILD_METHOD}" != "debian-package" ]; then \
    cp -a /app-src/. /app/ ; \
  fi && rm -rf /app-src

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

ENTRYPOINT ["/bin/bash"]
