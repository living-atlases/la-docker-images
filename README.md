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
- `build-config.yml`: Local overrides (gitignored). Use this to set your own registry, repo forks, or branches.

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
