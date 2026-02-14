# LA Docker Images

Build repository for Living Atlas Docker images.

**Scope**: This repository is dedicated to building and publishing Docker images. It is separate from `ala-install` ansible roles.

## Structure

- `services-definition.yml`: Metadata for all services (extracted from legacy docker-compose role).
- `templates/`: Generic Dockerfile templates (Gradle, Maven).
- `services/`: Service-specific overrides (e.g. `la-pipelines`, `cas-management`).
- `scripts/`: Build automation scripts.
- `build.py`: Main CLI tool.

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

# Build all services
./venv/bin/python build.py --all
```

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

## Version Management

## Dynamic Java Versioning

The builder automatically determines the required Java version (8, 11, 17, 21) based on the service version being built.
It does this by checking the `dependencies.yaml` file from the LA Toolkit backend.

You can override the dependencies source:

```bash
# Use a local file
./venv/bin/python build.py --service=image-service --dependencies=/path/to/dependencies.yaml
```

The `sync_versions.py` script is now deprecated as `build.py` handles this dynamically.

