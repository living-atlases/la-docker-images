# Context for Agents & Developers

This repository (`la-docker-images`) is the central build system for Living Atlas Docker images. It enables a dedicated build process decoupled from deployment logic.

## Core Philosophy

1.  **Single Source of Truth**: `services-definition.yml` defines *what* to build (repositories, ports, artifacts).
2.  **Dynamic Versioning**: We do *not* hardcode Java versions. The builder (`build.py`) queries the LA Toolkit backend (`dependencies.yaml`) to verify which Java version (8, 11, 17, 21) is required for the specific version of the service being built.
3.  **Templating**: We use shell-variable based templates (`templates/`) instead of complex Jinja2 logic where possible, to keep Dockerfiles readable and debuggable.

## Repository Map

-   **`build.py`**: The main entry point. CLI tool to generate Dockerfiles and build images.
    -   Usage: `./venv/bin/python build.py --service=collectory --all`
-   **`scripts/deps_utils.py`**: Library used by `build.py` to fetch `dependencies.yaml` and resolve version constraints.
-   **`services-definition.yml`**: The "database" of services.
-   **`templates/`**:
    -   `Dockerfile.maven.tmpl` / `Dockerfile.gradle.tmpl`: Generic templates.
-   **`services/`**: Directory for service-specific overrides (e.g., `services/cas-management/Dockerfile`).
-   **`Jenkinsfile`**: GitOps pipeline definition.

## Common Tasks

### Adding a New Service
1.  Add entry to `services-definition.yml`.
2.  If generic templates work, you are done.
3.  If special build steps are needed, create `services/<name>/Dockerfile`.

### debugging Version Issues
Check `scripts/deps_utils.py`. This script handles the mapping between service names (e.g., `image-service`) and the keys in `dependencies.yaml` (`images`), and parses the semantic versioning logic.

### CI/CD
The `Jenkinsfile` is parameterized.
-   **Push**: Images are pushed to Docker Hub (`livingatlases`).
-   **Creds**: Requires `docker-hub` credentials in Jenkins.

## Environment
Requires Python 3. `venv` is recommended.
Dependencies: `docopt`, `PyYAML`, `requests`, `packaging`.
