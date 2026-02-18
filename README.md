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

---

## Jenkins Integration

This repository includes a `Jenkinsfile` that automates image building in a CI/CD environment.

### Pipeline Parameters

- `SERVICE`: Comma-separated list of services to build (or `all`).
- `SKIP_SERVICES`: Services to exclude from the build.
- `N_TAGS`: Number of recent versions to build if no specific tag is provided.
- `TAG`: Specific version to build (overrides N_TAGS).
- `BRANCH`: Git branch for `repo-branch` builds.
- `PUSH`: Whether to push images to Docker Hub after a successful build.

### Automatic Synchronization

The Jenkinsfile parameter descriptions (the list of available services) are automatically kept in sync with `services-definition.yml` via the `./scripts/update_jenkinsfile.py` script. This script runs as the first stage of the pipeline to ensure documentation matches the code.
