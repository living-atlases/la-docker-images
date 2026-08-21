# LA Docker Images

Build repository for Living Atlas Docker images.

**Scope**: This repository is dedicated to building and publishing Docker images.

## Structure

- `services-definition.yml`: Metadata and default configuration for all services.
- `templates/`: Generic Dockerfile templates (Gradle, Maven).
- `services/`: Service-specific overrides and Custom Dockerfiles.
- `builders/`: Dockerfiles for base build images (Maven/Gradle with specific JDKs).
- `scripts/`: Automation utilities (Java version resolution, Jenkins synchronization).
- `build.py`: Main CLI tool for building and publishing images.
- `Jenkinsfile`: CI/CD pipeline definition.

## Setup

It is recommended to use a virtual environment to avoid polluting your system packages.

```bash
# Create venv
python3 -m venv venv

# Install dependencies
./venv/bin/pip install -r requirements.txt
```

## Usage

Use the `build.py` script to generate Dockerfiles and build images.

### Basic Build

```bash
# Build a single service (from Nexus)
./venv/bin/python build.py --service=collectory

# Build multiple services
./venv/bin/python build.py --service=collectory --service=ala-hub

# Build all services while skipping some
./venv/bin/python build.py --all --skip-service=cas --skip-service=biocollect

# Build from a list in a file (JSON or YAML)
./venv/bin/python build.py --from-file=my-services.yml
```

### Advanced Build Options

- `--n-tags=N`: Build the last N versions found in Nexus (useful for bulk updates).
- `--list-tags=v1,v2`: Build specific comma-separated versions.
- `--no-cache`: Force build without Docker cache.
- `--pull`: Always attempt to pull a newer version of base images.
- `--build-builders`: Force rebuilding of internal builder images.
- `--check`: Validate Nexus URLs and Java versions without building.

### Build Methods

You can override the build method via CLI or `build-config.yml`.

```bash
# Build from source (git repo)
./venv/bin/python build.py --service=collectory --build-method=repo-branch --branch=master

# Build from local custom Dockerfile (dev mode)
# Just place Dockerfile in services/<service>/Dockerfile and run build.py
```

### Customization

- **Java Version**: `--java-version=17`
- **Base Image**: `--java-base=eclipse-temurin`
- **Dry Run**: `--dry-run` (Generates Dockerfiles in `build/` but does not build image)

## Configuration

- `services-definition.yml`: Base service definitions.
- `build-config.yml`: Local overrides. Use this to set your own registry, repo forks, or branches.

Example `build-config.yml`:

```yaml
global_defaults:
  registry: my-docker-registry.org/ala
  push: true

services:
  collectory:
    branch: my-feature-branch
    build_method: repo-branch
```

## Dynamic Java Versioning

The builder automatically determines the required Java version (8, 11, 17, 21) based on the service version. It uses the `dependencies.yaml` from the [LA Toolkit Backend](https://github.com/living-atlases/la-toolkit-backend) as the source of truth.

- Local cache: Dependencies are cached in `~/.cache/la-docker-images/` for 24 hours.
- Override source: `./venv/bin/python build.py --dependencies=/path/to/local-deps.yaml`

---

## Configuration details

This repository is designed to be highly configurable, allowing you to build images for your own organization using custom registries, forked repositories, or completely custom Dockerfiles.

### 1. Custom Registries

You can configure the Docker registry where images are pushed. This can be done globally or per-service.

**Global Registry Override (CLI):**

```bash
./venv/bin/python build.py --all --registry=my-registry.org/my-org
```

**Global Registry Override (`build-config.yml`):**

```yaml
global_defaults:
  registry: my-registry.org/my-org
```

**Service-Specific Registry:**

```yaml
services:
  collectory:
    registry: specialized-registry.io/auth-team
```

### 2. Custom Repositories (Forks/Mirrors)

If you have forked a Living Atlas component to make customizations, you can point the builder to your repository and branch.

**Example `build-config.yml`:**

```yaml
services:
  collectory:
    build_method: repo-branch
    repository: https://github.com/my-org/collectory-fork.git
    branch: my-custom-branch
```

### 3. Custom Dockerfiles

You can provide a custom `Dockerfile` for any service to completely bypass the standard template generation.

1. Create a directory `services/<service-name>/`.
2. Place your `Dockerfile` inside it.

**Example:**
`services/collectory/Dockerfile`

When you run `./build.py --service=collectory`, the script will detect this file and use it instead of generating one from templates. This is useful for development or when a specific service requires a non-standard build process.

### Comprehensive Configuration Example (`build-config.yml`)

The `build-config.yml` can be used for local configuration.

```yaml
global_defaults:
  registry: docker.my-institution.org/atlas
  push: true
  java_version: 11 # Override default Java version globally

services:
  # Case 1: Standard Nexus build but different registry (inherited from global)
  ala-hub: {}

  # Case 2: Building from a local fork
  collectory:
    build_method: repo-branch
    repository: https://github.com/my-institution/collectory.git
    branch: dev-hotfix

  # Case 3: Pinning a specific version
  biocache-service:
    version: 2.3.0
    build_method: nexus

  # Case 4: Skipping specific tests or passing extra args (if supported by Dockerfile)
  specieslist-webapp:
    extra_params:
      - key: run.tests
        value: false
```

### 4. Flexible Configuration (Spring Boot)

All Java/Spring Boot based images are configured to automatically look for configuration files in `/data/<service>/config/`.

- **Standard Config**: `application.yml` or `application.properties`
- **Local Override**: `application-local-config.yml` (Precedence: High)

By mounting a file to `/data/<service>/config/application-local-config.yml`, you can override any property without rebuilding the image.

**Example `docker-compose.yml`:**

```yaml
services:
  collectory:
    image: gbif/collectory:latest
    volumes:
      - ./my-local-config.yml:/data/collectory/config/application-local-config.yml
```

This works because `JAVA_OPTS` are automatically injected with:
`-Dspring.config.additional-location=/data/.../config/ -Dspring.config.name=application,application-local-config`


## Jenkins Integration

This repository includes a `Jenkinsfile` that automates image building in a CI/CD environment.

### Pipeline Parameters

- `SERVICE`: Comma-separated list of services to build (or `all`).
- `SKIP_SERVICES`: Services to exclude from the build.
- `N_TAGS`: Number of recent versions to build if no specific tag is provided.
- `TAG`: Specific version to build (overrides N_TAGS).
- `LIST_TAGS`: Build exactly these versions, comma-separated. Unlike `N_TAGS` it
  reaches an old version without rebuilding everything above it, and does not move
  `latest`. This is what a backfill should use.
- `BRANCH`: Git branch for `repo-branch` builds.
- `PUSH`: Whether to push images to Docker Hub after a successful build.

### Automatic Synchronization

The Jenkinsfile parameter descriptions (the list of available services) are automatically kept in sync with `services-definition.yml` via the `./scripts/update_jenkinsfile.py` script. This script runs as the first stage of the pipeline to ensure documentation matches the code.

### Rebuilding many tags at once

Use `LIST_TAGS`, not `N_TAGS`. `N_TAGS=N` always takes the N *newest* versions, so
reaching an old one means rebuilding everything above it — and every rebuild of a tag
leaves the image it replaced untagged. A `N_TAGS=10` sweep across 23 services once left
3102 dangling images, filled the agent's 295GB volume and failed every build on the box
until it was pruned by hand.

`LIST_TAGS` builds exactly the versions listed and does not move `latest`:

```
SERVICE=spatial-service
LIST_TAGS=1.1.1,2.1.0,2.1.1,2.1.2,2.1.3,2.1.4,2.1.5,2.2.0,3.0.0
PUSH=true
```

One service per run, since `LIST_TAGS` applies to every service in `SERVICE`. To find
what is actually missing rather than guessing, compare the Nexus versions against what
Docker Hub already has (`hub.docker.com/v2/repositories/livingatlases/<svc>/tags`) — the
Jenkins console buffers Python's output and will look stalled when it is not.

Start with a dry run to see the whole matrix without building anything:

```bash
./venv/bin/python build.py --all --n-tags=10 --check
```

Every run closes with a summary:

```
📊 Summary: 25 built, 3 skipped, 0 failed
```

Versions whose published artifact declares no `Main-Class` are skipped with a reason —
`java -jar` cannot start those, so there is no image to build. About 30 of the 214 pairs
at `N_TAGS=10` are in that state permanently (plain Grails-2 era wars). A failure on the
newest version of a service fails the job; historical ones are reported and the run
continues. Use `--strict` (build.py) for all-or-nothing.

## Agent maintenance

The pipeline's `post` collects dangling images and trims build cache older than a week,
which stops the pathological growth above. It does **not** stop normal growth: every run
legitimately adds tagged images that stay forever, roughly 11 per service (10 versions
plus `latest`).

Measured after a full backfill:

```
Images        216   141.8GB   136.2GB reclaimable (95%)
Build Cache   945    86.12GB   22.67GB reclaimable
```

Those 136GB are our own images, already pushed to Docker Hub, on an agent that never runs
a container. They are safe to drop, and dropping them does not slow anything down: what
makes a rebuild cheap is the **BuildKit cache** and the **base images** it starts `FROM`.
A finished `livingatlases/collectory:6.0.0` in the local store is output, not cache.

What must survive — a blanket `docker system prune -a` would take these, and the next
build would pay to re-pull and rebuild them:

| | |
|---|---|
| `eclipse-temurin:{8,11,17,21}-jre-jammy` | runtime bases |
| `gradle:7-jdk*-jammy` | builder bases |
| `gradle-builder:jdk*`, `maven-builder:jdk*` | built here, **never pushed anywhere** |
| `cypress/browsers`, `gbif-taxonomy-for-la` | other jobs on the same agent |

See issue #4. Also worth capping the build cache by size rather than age
(`docker builder prune --keep-storage=40GB`), since the current one-week trim does not
bound growth.

### Careful with Jenkins "Replay"

Replaying a build with a cut-down script rewrites the job's configuration, because a
Declarative pipeline applies its own `properties` to the job. A replay script without
`parameters {}` and `options {}` silently drops every parameter definition and re-enables
concurrent builds — after which triggered builds run with defaults and the parameters you
passed are discarded without warning. If you replay for a one-off diagnostic, keep those
blocks even if the script does not use them.
