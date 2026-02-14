# syntax=docker/dockerfile:1.14.0
ARG JAVA_VERSION=17
ARG BUILDER_IMAGE=maven-builder:jdk${JAVA_VERSION}
FROM ${BUILDER_IMAGE} AS builder

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

# Runtime stage
FROM eclipse-temurin:${JAVA_VERSION}-jre-jammy

LABEL ala.version=pipelines-source-build

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
  export DEBIAN_FRONTEND=noninteractive && \
  apt-get update && \
  apt-get install -y --no-install-recommends --fix-missing \
  nano \
  file \
  curl \
  unzip \
  debianutils && \
  rm -rf /var/lib/apt/lists/*

RUN mkdir /data && chown 1000:1000 /data
VOLUME /data

ARG TARGETARCH

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

USER 1000

# Copy the built artifacts from the builder stage
COPY --from=builder --chown=1000:1000 /app /app

WORKDIR /app/livingatlas/scripts

ENV TZ="Europe/Paris"
ENV LOG_CONFIG="/app/livingatlas/pipelines/src/main/resources/log4j-colorized.properties"
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
  --add-opens=java.security.jgss/sun.security.krb5=ALL-UNNAMED \
  -Dlog4j.configuration=file://${LOG_CONFIG} -Dlog4j.configurationFile=file://${LOG_CONFIG}"

ENTRYPOINT ["bash", "-c"]
