# syntax=docker/dockerfile:1.4
# ${SERVICE_NAME} - ${DESCRIPTION}
# Custom Dockerfile (build.py uses this verbatim, with ${...} substitution).
#
# Generic, runtime-configurable SDS static home page (gen 2: ala-sds-static-home).
# Reusability ground rule: this is an LA-side build only — ALA's source and its
# S3/CloudFront static-home workflow are untouched. We *consume* master, we don't fork.
#
# One image, configured per portal at DEPLOY time (no rebuild):
#   the Vite SPA is built with placeholder tokens; an entrypoint substitutes them
#   from env vars at container start, defaulting to relative paths.
#
# Runtime env vars (all optional; defaults are relative so a reverse proxy can route them):
#   SDS_WS_URL       (default /ws)                          - ala-sensitive-data-server base
#   SDS_SWAGGER_URL  (default /ws/swagger)
#   SDS_LISTS_URL    (default /specieslists?isSDS=eq:true)
#   SDS_HELP_URL     (default https://support.ala.org.au)
#   SDS_XML_URL      (default /sensitive-species-data.xml)  - sensitive-species-data.xml

# Consume build.py's --build-arg values quietly (silences "unused build-arg" warnings)
ARG REGISTRY=""
ARG BUILD_METHOD=repo-branch
ARG VERSION=latest
ARG COMMIT=HEAD
ARG CACHE_BYPASS=0

# ---------- Stage 1: build the Vite/React SPA ----------
FROM node:20-bookworm-slim AS builder

ARG REPO=${REPO}
ARG BRANCH=${BRANCH}
ARG BUILD_DIR=${BUILD_DIR}

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --branch "${BRANCH}" --depth 1 "${REPO}" . && git log -1 --oneline

WORKDIR /src/${BUILD_DIR}

# Local safety net: index.html references /src/main.jsx but the entry is main.tsx
RUN if [ -f index.html ]; then sed -i 's#/src/main\.jsx#/src/main.tsx#g' index.html; fi

# Bake placeholder tokens so endpoints become runtime-configurable (no per-portal rebuild)
RUN cat > config/.env.production <<'EOF'
VITE_APP_SENSITIVE_WS_URL=__SDS_WS_URL__
VITE_APP_SENSITIVE_SWAGGER_URL=__SDS_SWAGGER_URL__
VITE_APP_LISTS_URL=__SDS_LISTS_URL__
VITE_APP_ALA_HELP_SENSITIVE_URL=__SDS_HELP_URL__
VITE_APP_SENSITIVE_SPECIES_XML_URL=__SDS_XML_URL__
EOF

# Use the Yarn bundled in the node image (classic 1.22, like upstream CI) —
# avoids npm's rollup optional-deps bug (npm ci skips @rollup/rollup-linux-x64-gnu).
RUN yarn install --frozen-lockfile || yarn install
RUN yarn build:production && ls -lh dist

# ---------- Stage 2: runtime (non-root nginx on 8080) ----------
FROM nginxinc/nginx-unprivileged:stable-alpine

ARG REPO=${REPO}

USER root

# Built SPA, owned by the runtime user (101) so the entrypoint can sed-substitute tokens
COPY --from=builder --chown=101:101 /src/${BUILD_DIR}/dist /usr/share/nginx/html

# SPA routing (client-side routes fall back to index.html)
RUN cat > /etc/nginx/conf.d/default.conf <<'EOF'
server {
  listen 8080;
  server_name _;
  root /usr/share/nginx/html;
  location = /index.html { add_header Cache-Control "no-cache"; }
  location / { try_files $uri $uri/ /index.html; }
}
EOF

# Runtime config: substitute placeholder tokens from env vars before nginx starts.
# The nginx base image auto-runs /docker-entrypoint.d/*.sh on container start.
RUN cat > /docker-entrypoint.d/40-sds-runtime-env.sh <<'EOF'
#!/bin/sh
set -eu
: "${SDS_WS_URL:=/ws}"
: "${SDS_SWAGGER_URL:=/ws/swagger}"
: "${SDS_LISTS_URL:=/specieslists?isSDS=eq:true}"
: "${SDS_HELP_URL:=https://support.ala.org.au}"
: "${SDS_XML_URL:=/sensitive-species-data.xml}"
# escape sed-special chars in replacement values (& | \)
esc() { printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'; }
echo "sds-static-home: applying runtime endpoints (WS=$SDS_WS_URL XML=$SDS_XML_URL)"
for f in $(find /usr/share/nginx/html -type f -name '*.js'); do
  sed -i \
    -e "s|__SDS_WS_URL__|$(esc "$SDS_WS_URL")|g" \
    -e "s|__SDS_SWAGGER_URL__|$(esc "$SDS_SWAGGER_URL")|g" \
    -e "s|__SDS_LISTS_URL__|$(esc "$SDS_LISTS_URL")|g" \
    -e "s|__SDS_HELP_URL__|$(esc "$SDS_HELP_URL")|g" \
    -e "s|__SDS_XML_URL__|$(esc "$SDS_XML_URL")|g" "$f"
done
EOF
RUN chmod +x /docker-entrypoint.d/40-sds-runtime-env.sh

LABEL org.opencontainers.image.title="${SERVICE_NAME}"
LABEL org.opencontainers.image.description="${DESCRIPTION}"
LABEL org.opencontainers.image.source="${REPO}"

EXPOSE 8080
USER 101
