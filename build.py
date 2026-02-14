#!/usr/bin/env python3
"""Living Atlas Docker Image Builder.

This script automates the creation of Docker images for Living Atlas components.
It supports building from Nexus artifacts (default) or source code (git), handles
dynamic Java version selection based on component versions, and integrates with
Jenkins CI/CD pipelines.

Usage:
  build.py --service=<name>... [options]
  build.py --all [options]
  build.py --from-file=<file> [options]
  build.py -h | --help
  build.py --version

Examples:
  # Build a single service using Nexus artifact (default)
  ./build.py --service=collectory --tag=2.3.1

  # Build multiple services
  ./build.py --service=collectory --service=ala-hub --tag=latest

  # Build from source code (git branch)
  ./build.py --service=collectory --build-method=repo-branch --branch=my-feature

  # Build with dynamic dependency resolution from a local file
  ./build.py --service=image-service --dependencies=./local-deps.yaml

  # Dry run to see generated Dockerfiles without building
  ./build.py --all --dry-run

Options:
  --service=<name>        Service(s) to build. Can be specified multiple times.
  --all                   Build all services defined in services-definition.yml.
  --from-file=<file>      Build services listed in a JSON/YAML file.
  
  # Version Selection
  --tag=<tag>             Force a specific version/tag for the build.
                          If not specified, defaults to 'latest' or 'develop'.
  --list-tags=<tags>      Build multiple versions (comma-separated).
  --n-tags=<n>            Build the last N versions from Nexus [default: 1].
  
  # Build Configuration
  --java-version=<ver>    Override Java version (8, 11, 17, 21).
                          Default: Auto-detected from dependencies.yaml.
  --java-base=<image>     Override base Java image (e.g. eclipse-temurin).
  --build-method=<method> Build method:
                            nexus:       Download WAR/JAR from Nexus (Default)
                            repo-branch: Clone git repo and build from branch
                            repo-tag:    Clone git repo and build from tag
                            url:         Download artifact from direct URL
  --registry=<reg>        Docker registry to push to [default: livingatlases].
  --repo=<url>            Override git repository URL.
  --branch=<branch>       Override git branch (for repo-branch method).
  --commit=<commit>       Override git commit (for repo-branch method).
  
  # Actions
  --push                  Push images to registry after successful build.
  --dry-run               Generate Dockerfiles in build/ directory but do NOT build images.
  --check                 Only check Nexus URLs and Java versions without building or generating files.
  --no-cache              Do not use Docker cache when building.
  --pull                  Always attempt to pull a newer version of the image.
  --update-metadata       Force update of Nexus metadata (ignore cache).
  --build-builders        Force rebuilding of base builder images (gradle/maven).
  
  # Configuration Files
  --config=<file>         Path to local build config overrides [default: build-config.yml].
  --defs=<file>           Path to service definitions [default: services-definition.yml].
  --dependencies=<url>    URL or path to dependencies.yaml for Java version resolution.
                          [default: https://raw.githubusercontent.com/living-atlases/la-toolkit-backend/master/assets/dependencies.yaml]
  
  -h --help               Show this help message.
  --version               Show version.

"""

import os
import sys
import yaml
from packaging import version as pkg_version
import json
import shutil
import subprocess
from docopt import docopt
from string import Template
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

# Add scripts dir to path to import deps_utils
sys.path.append(os.path.join(os.path.dirname(__file__), 'scripts'))
try:
    import deps_utils
except ImportError:
    deps_utils = None

# Determine script location to resolve relative paths correctly
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

# Constants
import hashlib
DEFAULT_REGISTRY = "livingatlases"
DEFAULT_DEPENDENCIES_URL = "https://raw.githubusercontent.com/living-atlases/la-toolkit-backend/master/assets/dependencies.yaml"
# Defaults if not found in dependencies
DEFAULT_JAVA_VERSION = "11" 
BUILD_DIR_BASE = os.path.join(SCRIPT_DIR, "build")
TEMPLATES_DIR = os.path.join(SCRIPT_DIR, "templates")
SERVICES_DIR = os.path.join(SCRIPT_DIR, "services")
BUILDERS_DIR = os.path.join(SCRIPT_DIR, "builders")

def ensure_builders(registry, java_version, tool, force_rebuild=False, pull=False, dry_run=False):
    """
    Ensure the required builder image exists locally.
    If not, build it from the builders/ directory.
    Note: Builders are kept LOCAL-ONLY and not prefixed with registry.
    """
    image_name = f"{tool}-builder:jdk{java_version}"
    
    # Check if image exists locally
    try:
        if not force_rebuild and not pull:
            subprocess.check_call(["docker", "image", "inspect", image_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    except subprocess.CalledProcessError:
        pass # Not found or forced
        
    # If using pull, we pull the EXTERNAL BASE image of the builder
    if pull:
        base_image = ""
        if tool == "gradle":
            # From builders/gradle/Dockerfile: gradle:${GRADLE_VERSION}-jdk${JDK_VERSION}-jammy
            base_image = f"gradle:7-jdk{java_version}-jammy"
        elif tool == "maven":
            # From builders/maven/Dockerfile: maven:${MAVEN_VERSION}-eclipse-temurin-${JDK_VERSION}
            base_image = f"maven:3.9-eclipse-temurin-{java_version}"
            
        if base_image:
            if dry_run:
                print(f"   [Dry Run] Would pull external base image for builder: {base_image}")
            else:
                print(f"   📡 Pulling external base image for builder: {base_image}...")
                try:
                    subprocess.check_call(["docker", "pull", base_image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except subprocess.CalledProcessError:
                    print(f"   ⚠️  Warning: Failed to pull base image {base_image}. Build might use local cache.")

    if dry_run:
        print(f"   [Dry Run] Would build builder image: {image_name}")
        return

    print(f"   🔨 Building builder image: {image_name}...")
    
    builder_path = os.path.join(BUILDERS_DIR, tool)
    if not os.path.exists(builder_path):
        print(f"   ⚠️  Warning: Builder path {builder_path} not found. Cannot build {image_name}.")
        return

    cmd = [
        "docker", "build",
        "-t", image_name,
        "--build-arg", f"JDK_VERSION={java_version}",
        "."
    ]
    
    # Specific versions for builders if needed
    if tool == 'gradle':
        gradle_version = "8" if str(java_version) == "21" else "7"
        cmd.extend(["--build-arg", f"GRADLE_VERSION={gradle_version}"])
    
    try:
        subprocess.check_call(cmd, cwd=builder_path)
        print(f"   ✅ Builder image {image_name} built successfully.")
    except subprocess.CalledProcessError as e:
        print(f"   ❌ Failed to build builder image {image_name}")
        sys.exit(1)

def load_config(config_file, defs_file):
    """Load and merge configuration"""
    # Resolve paths relative to script if they are relative
    # But user might pass absolute paths or paths relative to CWD.
    # Default values should be relative to SCRIPT_DIR
    
    if not os.path.isabs(defs_file) and defs_file == 'services-definition.yml':
         defs_file = os.path.join(SCRIPT_DIR, defs_file)
         
    if not os.path.exists(defs_file):
        print(f"Error: Services definition file not found: {defs_file}")
        sys.exit(1)
        
    with open(defs_file, 'r') as f:
        defs = yaml.safe_load(f)
        
    config = {'services': {}}
    
    if not os.path.isabs(config_file) and config_file == 'build-config.yml':
        config_file = os.path.join(SCRIPT_DIR, config_file)

    if os.path.exists(config_file):
        with open(config_file, 'r') as f:
            user_config = yaml.safe_load(f) or {}
            config.update(user_config)
    
    # Merge definitions into config (user config takes precedence)
    merged_services = defs.get('services', {}).copy()
    
    # Update with overrides from build-config
    if 'services' in config and config['services']:
        for name, overrides in config['services'].items():
            if name in merged_services:
                merged_services[name].update(overrides)
            else:
                merged_services[name] = overrides
    
    return {
        'global_defaults': config.get('global_defaults', {}),
        'services': merged_services
    }

def get_service_config(service_name, config, args):
    """Resolve final configuration for a service"""
    if service_name not in config['services']:
        print(f"Error: Service '{service_name}' not defined")
        sys.exit(1)
        
    svc = config['services'][service_name].copy()
    defaults = config['global_defaults']
    
    # 1. Defaults
    final_config = {
        'registry': defaults.get('registry', DEFAULT_REGISTRY),
        #'java_version': defaults.get('java_version', DEFAULT_JAVA_VERSION), # Now determined dynamically
        'build_method': defaults.get('build_method', 'nexus'),
        'push': False
    }
    
    # 2. Service config
    final_config.update(svc)
    
    # 3. CLI arguments overrides
    if args['--tag']: final_config['version'] = args['--tag']
    if args['--registry']: final_config['registry'] = args['--registry']
    if args['--build-method']: final_config['build_method'] = args['--build-method']
    if args['--java-version']: final_config['java_version'] = args['--java-version']
    if args['--java-base']: final_config['java_base_image'] = args['--java-base']
    if args['--repo']: final_config['repository'] = args['--repo']
    if args['--branch']: final_config['branch'] = args['--branch']
    if args['--commit']: final_config['commit'] = args['--commit']
    if args['--push']: final_config['push'] = True
    if args['--pull']: final_config['pull'] = True
    
    return final_config

def check_nexus_url(service_name, config):
    """
    Check if the Nexus URL for the artifact is reachable.
    Replicates logic from download-artifact.sh.
    """
    artifact = config.get('artifacts', service_name)
    version = config.get('version', 'latest')
    classifier = config.get('classifier', '')
    extension = config.get('extension', 'war')
    
    # Determine repository
    nexus_repo = "releases"
    if "SNAPSHOT" in version:
        nexus_repo = "snapshots"
        
    # Construct artifact name
    # artifact-version[-classifier].extension
    artifact_name = f"{artifact}-{version}"
    if classifier:
        artifact_name += f"-{classifier}"
    full_artifact_name = f"{artifact_name}.{extension}"
    
    # Construct URL
    # https://nexus.ala.org.au/repository/{nexus_repo}/au/org/ala/{ARTIFACT}/{VERSION}/{full_artifact_name}
    nexus_base_url = f"https://nexus.ala.org.au/repository/{nexus_repo}"
    nexus_url = f"{nexus_base_url}/au/org/ala/{artifact}/{version}/{full_artifact_name}"
    
    try:
        req = urllib.request.Request(nexus_url, method='HEAD')
        # Add a timeout to avoid hanging
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                return True, nexus_url
            else:
                return False, nexus_url
    except (urllib.error.URLError, Exception):
        return False, nexus_url

import hashlib
import time
from pathlib import Path

# Metadata Cache Configuration
METADATA_CACHE_DIR = Path.home() / ".cache" / "la-docker-images" / "metadata"
TAGS_CACHE_DIR = Path.home() / ".cache" / "la-docker-images" / "tags"
CACHE_DURATION_SECONDS = 24 * 60 * 60  # 24 hours

def get_cached_metadata(url):
    """
    Retrieve metadata from cache if valid.
    """
    if not METADATA_CACHE_DIR.exists():
        return None

    # Generate filename from URL hash
    filename = hashlib.md5(url.encode('utf-8')).hexdigest() + ".xml"
    cache_file = METADATA_CACHE_DIR / filename
    
    if not cache_file.exists():
        return None

    # Check file age
    file_age = time.time() - cache_file.stat().st_mtime
    if file_age > CACHE_DURATION_SECONDS:
        return None

    try:
        with open(cache_file, 'r') as f:
            # Check if file is empty
            content = f.read()
            if not content:
                 return None
            return content
    except Exception as e:
        print(f"   ⚠️  Warning: Could not load cache: {e}")
        return None

def save_cached_metadata(url, content):
    """
    Save metadata to cache.
    """
    try:
        METADATA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        
        # Generate filename from URL hash
        filename = hashlib.md5(url.encode('utf-8')).hexdigest() + ".xml"
        cache_file = METADATA_CACHE_DIR / filename
        
        with open(cache_file, 'w') as f:
            f.write(content)
    except Exception as e:
        print(f"   ⚠️  Warning: Could not save to cache: {e}")

def get_nexus_versions(service_name, config, n=1, update_metadata=False):
    """Fetch last N versions from Nexus metadata"""
    artifact = config.get('artifacts', service_name)
    # Default to releases for metadata search
    nexus_base = "https://nexus.ala.org.au/repository/releases"
    url = f"{nexus_base}/au/org/ala/{artifact}/maven-metadata.xml"
    
    xml_content = None
    from_cache = False
    
    if not update_metadata:
        xml_content = get_cached_metadata(url)
        if xml_content:
             from_cache = True
             # print(f"   📦 Using cached metadata for {service_name}")

    if not xml_content:
        print(f"   🔎 Fetching metadata for {service_name}: {url}")
        try:
            with urllib.request.urlopen(url) as response:
                if response.status != 200:
                    print(f"   ⚠️  Failed to fetch metadata for {service_name}")
                    return []
                xml_content = response.read().decode('utf-8')
                save_cached_metadata(url, xml_content)
        except Exception as e:
             print(f"   ⚠️  Error fetching metadata for {service_name}: {e}")
             return []
    else:
        print(f"   📦 Using cached metadata for {service_name}")

    try:
        root = ET.fromstring(xml_content)
        # <versioning><versions><version>...</version></versions></versioning>
        versions = [v.text for v in root.findall(".//version")]
        
        # Filter out snapshots if we are looking for releases
        # (Though metadata from releases repo shouldn't have them usually)
        
        # Sort versions
        try:
            if deps_utils:
                versions.sort(key=lambda v: deps_utils.pkg_version.parse(v))
            else:
                versions.sort()
        except Exception:
            versions.sort()
            
        return versions[-n:]
            
    except Exception as e:
        print(f"   ⚠️  Error parsing metadata for {service_name}: {e}")
        return []

def get_github_tags(service_name, config, n=1):
    """
    Fetch tags from GitHub API
    Wrapper to get Clean Version -> Original Tag mapping
    """
    repo_url = config.get('repository', '')
    if 'github.com' not in repo_url:
        print(f"   ⚠️  Not a GitHub URL: {repo_url}")
        return []

    # Extract clean owner/repo
    # e.g. https://github.com/gbif/pipelines.git -> gbif/pipelines
    try:
        path = repo_url.split('github.com/')[-1]
        if path.endswith('.git'):
            path = path[:-4]
        owner_repo = path.strip('/')
    except IndexError:
         print(f"   ⚠️  Could not parse GitHub URL: {repo_url}")
         return []

    api_url = f"https://api.github.com/repos/{owner_repo}/tags"
    
    # Check cache
    json_content = None
    cache_file = TAGS_CACHE_DIR / (hashlib.md5(api_url.encode('utf-8')).hexdigest() + ".json")
    
    if TAGS_CACHE_DIR.exists() and cache_file.exists():
        file_age = time.time() - cache_file.stat().st_mtime
        if file_age <= CACHE_DURATION_SECONDS:
             try:
                 with open(cache_file, 'r') as f:
                     json_content = json.load(f)
                 # print(f"   📦 Using cached tags for {service_name}")
             except Exception:
                 pass

    if not json_content:
        print(f"   🔎 Fetching tags from GitHub for {service_name}: {api_url}")
        try:
            req = urllib.request.Request(api_url)
            # Add User-Agent to avoid generic blocking
            req.add_header('User-Agent', 'python-urllib')
            with urllib.request.urlopen(req) as response:
                if response.status == 200:
                    data = response.read()
                    json_content = json.loads(data)
                    
                    # Save to cache
                    TAGS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    with open(cache_file, 'w') as f:
                        f.write(data.decode('utf-8')) # Save raw string
                else:
                    print(f"   ⚠️  Failed to fetch tags: HTTP {response.status}")
                    return []
        except Exception as e:
            print(f"   ⚠️  Error fetching GitHub tags: {e}")
            return []

    if not json_content:
        return []

    # Process tags
    # Return list of (clean_version, original_tag)
    results = []
    
    for item in json_content:
        tag_name = item.get('name')
        if not tag_name: continue
        
        clean_version = tag_name
        
        # Specific logic for pipelines
        if tag_name.startswith('pipelines-parent-'):
            clean_version = tag_name.replace('pipelines-parent-', '')
            
        results.append((clean_version, tag_name))
        
    # Sort
    # Try semver sort
    try:
        results.sort(key=lambda x: pkg_version.parse(x[0]))
    except Exception:
        results.sort(key=lambda x: x[0])
        
    return results[-n:]

def generate_dockerfile(service_name, config, build_path):
    """Generate Dockerfile from template"""
    
    # Check for custom Dockerfile first
    custom_dockerfile = os.path.join(SERVICES_DIR, service_name, 'Dockerfile')
    template_content = ""
    
    if os.path.exists(custom_dockerfile):
        print(f"   ℹ️  Using custom Dockerfile from {custom_dockerfile}")
        with open(custom_dockerfile, 'r') as f:
            template_content = f.read()
    else:
        # Use generic template
        build_tool = config.get('build_tool', 'gradle')
        template_file = os.path.join(TEMPLATES_DIR, f"Dockerfile.{build_tool}.tmpl")
        if not os.path.exists(template_file):
             print(f"Error: Template not found: {template_file}")
             sys.exit(1)
             
        with open(template_file, 'r') as f:
            template_content = f.read()

    # Prepare variables for substitution
    java_opts = config.get('java_opts', '') 
    
    if 'extra_params' in config:
        extra_flags = []
        for param in config['extra_params']:
            if isinstance(param, dict):
                key = param.get('key')
                value = param.get('value')
                if key and value:
                    extra_flags.append(f"-D{key}={value}")
        
        if extra_flags:
            java_opts += " " + " ".join(extra_flags)
            
    # Standard mapping
    mapping = {
        'SERVICE_NAME': service_name,
        'SERVICE_NAME_UPPER': service_name.upper().replace('-', '_'),
        'DESCRIPTION': config.get('description', ''),
        'VERSION': config.get('version', 'latest'),
        'JAVA_VERSION': str(config.get('java_version')),
        'ARTIFACT_ID': config.get('artifacts', service_name),
        'EXTENSION': config.get('extension', 'war'),
        'CLASSIFIER': config.get('classifier', ''),
        'REPO': config.get('repository', ''),
        'BRANCH': config.get('branch', 'master'),
        'LOG_DIR': config.get('log_dir', ''),
        'LOG_CONFIG_FILENAME': config.get('log_config_filename', ''),
        'LOGGING_CONFIG': "", 
        'JAVA_OPTS': java_opts.strip(),
        'PORT': str(config.get('port', 8080)),
        'REGISTRY': config.get('registry', DEFAULT_REGISTRY) + ('/' if config.get('registry') and not config.get('registry').endswith('/') else ''),
        'JAVA_BASE_IMAGE': config.get('java_base_image', f"eclipse-temurin:{config.get('java_version')}-jre-jammy"),
        'APP_ARGS': config.get('app_args', ''),
        'MAVEN_PROFILES': config.get('maven_profiles', ''),
        'BUILD_DIR': config.get('build_dir', '.'),
        'COMMIT': config.get('commit', 'HEAD'),
        'BUILD_TOOL': config.get('build_tool', 'gradle'),
        'BUILD_METHOD': config.get('build_method', 'nexus'),
        'SERVICE_USER': config.get('name', service_name)
    }
    
    if config.get('log_config_filename'):
        artifact_id = mapping['ARTIFACT_ID']
        filename = config['log_config_filename']
        mapping['LOGGING_CONFIG'] = f"/data/{artifact_id}/config/{filename}"

    # Safe substitution
    try:
        t = Template(template_content)
        dockerfile_content = t.safe_substitute(mapping)
        
        with open(os.path.join(build_path, 'Dockerfile'), 'w') as f:
            f.write(dockerfile_content)
            
    except Exception as e:
        print(f"Error generating Dockerfile: {e}")
        sys.exit(1)

def build_service(service_name, service_config, dry_run=False, no_cache=False):
    """Build the docker image"""
    
    version = service_config.get('version', 'latest')
    registry = service_config.get('registry', DEFAULT_REGISTRY)
    image_name = f"{registry}/{service_name}:{version}"
    
    print(f"\n🚀 Processing {service_name} ({version})...")
    
    # Prepare build directory
    build_path = os.path.join(BUILD_DIR_BASE, service_name)
    if os.path.exists(build_path):
        shutil.rmtree(build_path)
    os.makedirs(build_path)
    
    # Generate Dockerfile
    generate_dockerfile(service_name, service_config, build_path)
    
    # Copy scripts/download-artifact.sh to build context
    scripts_dest = os.path.join(build_path, 'scripts')
    os.makedirs(scripts_dest, exist_ok=True)
    src_script = os.path.join(TEMPLATES_DIR, 'scripts', 'download-artifact.sh')
    dest_script = os.path.join(scripts_dest, 'download-artifact.sh')
    
    if os.path.exists(src_script):
        shutil.copy(src_script, dest_script)
    else:
        print(f"Warning: {src_script} not found. Build might fail if using Nexus method.")

    # Copy settings.xml if it exists in templates, else create default
    src_settings = os.path.join(TEMPLATES_DIR, 'settings.xml')
    dest_settings = os.path.join(build_path, 'settings.xml')
    if os.path.exists(src_settings):
        shutil.copy(src_settings, dest_settings)
        print(f"   ℹ️  Copied settings.xml to {build_path}")
    else:
        # Create minimal settings
        with open(dest_settings, 'w') as f:
            f.write('<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0"><mirrors/></settings>')    
    if dry_run:
        print(f"   ✅ Dockerfile generated in {build_path}")
        print(f"   [Dry Run] Would build: {image_name}")
        for tag in service_config.get('additional_tags', []):
            print(f"   [Dry Run] Would tag: {registry}/{service_name}:{tag}")
        return

    # Docker Build Command
    cmd = [
        "docker", "build",
        "-t", image_name,
        "--build-arg", f"BUILD_METHOD={service_config['build_method']}",
        "--build-arg", f"VERSION={version}",
        "--build-arg", f"BUILD_DIR={service_config.get('build_dir', '.')}",
        "--build-arg", f"COMMIT={service_config.get('commit', 'HEAD')}",
        "." # Context is build_path
    ]
    
    if no_cache:
        cmd.append("--no-cache")
        
    # We DO NOT use --pull directly because it fails with our local builders.
    # Instead, we pull the external runtime base image if requested.
    if service_config.get('pull'):
        java_version = service_config.get('java_version')
        if not java_version:
             print(f"   ❌ Error: java_version not defined for {service_name}. Cannot pull base image.")
             sys.exit(1)
        base_image = f"eclipse-temurin:{java_version}-jre-jammy"
        print(f"   📡 Pulling external runtime base image: {base_image}...")
        try:
            subprocess.check_call(["docker", "pull", base_image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            print(f"   ⚠️  Warning: Failed to pull base image {base_image}. Build might use local cache.")
        
    print(f"   🔨 Building {image_name}...")
    try:
        subprocess.check_call(cmd, cwd=build_path)
        print(f"   ✅ Build successful: {image_name}")
        
        if service_config['push']:
            print(f"   📤 Pushing {image_name}...")
            subprocess.check_call(["docker", "push", image_name])
            print("   ✅ Push successful")
            
        # Additional Tags (e.g. latest)
        for tag in service_config.get('additional_tags', []):
            tag_name = f"{registry}/{service_name}:{tag}"
            print(f"   🏷️  Tagging {tag_name}...")
            subprocess.check_call(["docker", "tag", image_name, tag_name])
            if service_config['push']:
                print(f"   📤 Pushing {tag_name}...")
                subprocess.check_call(["docker", "push", tag_name])
            
    except subprocess.CalledProcessError as e:
        sys.exit(1)

def main():
    args = docopt(__doc__, version='LA Docker Builder 0.1')
    
    # Load dependencies
    dependencies_url = args['--dependencies']
    dependencies = {}
    if deps_utils:
        try:
             dependencies = deps_utils.load_dependencies(dependencies_url)
             if not dependencies:
                 print(f"⚠️  Warning: Failed to load dependencies from {dependencies_url}")
        except Exception as e:
                print(f"⚠️  Warning: Error loading dependencies: {e}")
    else:
         print("⚠️  Warning: deps_utils module not found. Dynamic Java versioning disabled.")
    
    # If args are passed, they override default strings.
    # If args['--config'] is None, use default 'build-config.yml'
    config_file = args['--config'] or 'build-config.yml'
    defs_file = args['--defs'] or 'services-definition.yml'
    
    config = load_config(config_file, defs_file)
    
    services_to_build = []
    
    if args['--all']:
        services_to_build_names = list(config['services'].keys())
    elif args['--service']:
        services_to_build_names = args['--service']
    elif args['--from-file']:
        from_file = args['--from-file']
        if not os.path.exists(from_file):
             print(f"Error: File not found: {from_file}")
             sys.exit(1)
        with open(from_file, 'r') as f:
            if from_file.endswith('.json'):
                data = json.load(f)
            else:
                data = yaml.safe_load(f)
            
            # Expecting either a list of names or a dict with a 'services' list
            if isinstance(data, list):
                services_to_build_names = data
            elif isinstance(data, dict) and 'services' in data:
                services_to_build_names = data['services']
            else:
                print(f"Error: Invalid format in {from_file}. Expected list of services.")
                sys.exit(1)
    else:
        services_to_build_names = []
        
    if not services_to_build_names:
        print("No services selected to build. Use --service, --all or --from-file.")
        sys.exit(0)
    
    # --- EXPANSION PHASE ---
    # Expand services based on versions (e.g. latest -> [1.0.1, 1.0.2])
    expanded_build_list = [] # List of (service_name, final_config)
    
    for name in services_to_build_names:
        svc_conf = get_service_config(name, config, args)
        
        is_nexus = svc_conf.get('build_method') == 'nexus'
        version = svc_conf.get('version', 'latest')
        
        # Check if we need to deduce versions from Nexus
        # Condition: 
        # 1. Method is Nexus
        # 2. Version is 'latest' (meaning user didn't specify a specific version tag)
        if is_nexus and version == 'latest':
            n_tags = int(args.get('--n-tags', 1))
            update_metadata = args.get('--update-metadata', False)
            versions = get_nexus_versions(name, svc_conf, n_tags, update_metadata)
            
            if not versions:
                print(f"   ⚠️  No versions found for {name} in Nexus metadata. Keeping 'latest' (will likely fail).")
                expanded_build_list.append((name, svc_conf))
            else:
                print(f"   ✨ Resolved versions for {name}: {versions}")
                for i, v in enumerate(versions):
                    v_conf = svc_conf.copy()
                    v_conf['version'] = v
                    # Tag the last one as latest
                    if i == len(versions) - 1:
                        v_conf['additional_tags'] = ['latest']
                    expanded_build_list.append((name, v_conf))
        
        elif svc_conf.get('build_method') == 'repo-tags':
             n_tags = int(args.get('--n-tags', 1))
             # Fetch tags
             tags_info = get_github_tags(name, svc_conf, n_tags)
             
             if not tags_info:
                 print(f"   ⚠️  No tags found for {name}. Skipping.")
             else:
                 print(f"   ✨ Resolved tags for {name}: {[t[0] for t in tags_info]}")
                 for i, (version, original_tag) in enumerate(tags_info):
                     v_conf = svc_conf.copy()
                     v_conf['version'] = version
                     # METHOD MAPPING: repo-tags -> repo-branch with tag as branch
                     v_conf['build_method'] = 'repo-branch'
                     v_conf['branch'] = original_tag
                     # Tag the last one as latest
                     if i == len(tags_info) - 1:
                           v_conf['additional_tags'] = ['latest']
                     expanded_build_list.append((name, v_conf))

        else:
            # Explicit version or not Nexus
            expanded_build_list.append((name, svc_conf))

    # --- RESOLVE VALIDATION & JAVA VERSIONS ---
    final_build_tasks = [] #(name, config)
    
    for name, svc_conf in expanded_build_list:
        # Determine Java Version
        if 'java_version' not in svc_conf:
             # Try to resolve dynamically
             if deps_utils and dependencies:
                 v = svc_conf.get('version', 'latest')
                 dyn = deps_utils.determine_java_version(name, v, dependencies)
                 if dyn:
                     svc_conf['java_version'] = dyn
                 else:
                     print(f"   ❌ Error: Could not determine Java version for {name} ({v}) from dependencies.yaml")
                     print(f"      Please specify it manually using --java-version=<ver> or in services-definition.yml")
                     sys.exit(1)
             else:
                 print(f"   ❌ Error: Java version for {name} must be specified manually using --java-version=<ver>")
                 print(f"      or resolved via dependencies.yaml (which is currently unavailable or missing deps_utils).")
                 sys.exit(1)

        final_build_tasks.append((name, svc_conf))

    # --- GLOBAL CHECK PHASE ---
    print("\n🔎 Checking Nexus URLs for all selected services...")
    check_results = []
    has_failures = False
    
    for name, svc_conf in final_build_tasks:
        version = svc_conf.get('version')
        
        # Only check if build method is nexus
        java_version = svc_conf.get('java_version')
        if svc_conf.get('build_method') == 'nexus':
            success, url = check_nexus_url(name, svc_conf)
            status_icon = "✅" if success else "❌"
            check_results.append({
                'name': name,
                'version': version,
                'java_version': java_version,
                'url': url,
                'success': success,
                'icon': status_icon
            })
            if not success:
                has_failures = True
            
            # Print immediate feedback
            print(f"   {status_icon} {name} ({version}) [Java {java_version}]: {url}")
        else:
             print(f"   ⏭️  {name} ({version}) [Java {java_version}]: Skipped (method: {svc_conf.get('build_method')})")

    # If failures, abort (even in dry-run, we show results then stop)
    if has_failures:
        print("\n❌ Nexus URL Check Failed for the following services:")
        for res in check_results:
            if not res['success']:
                 print(f"   - {res['name']} ({res['version']}) -> {res['url']}")
        
        print("\n🛑 Aborting verify/build process due to invalid URLs.")
        sys.exit(1)
        
    print("\n✅ All Nexus URLs validated. Proceeding...\n")

    if args['--check']:
        print("🏁 Check-only mode: Validation successful. Exiting.")
        sys.exit(0)

    # --- BUILD PHASE ---
    for name, svc_conf in final_build_tasks:
        # Ensure builder exists before building the service
        registry = svc_conf.get('registry', DEFAULT_REGISTRY)
        java_version = svc_conf.get('java_version', DEFAULT_JAVA_VERSION)
        tool = svc_conf.get('build_tool', 'gradle')
        
        # We only need builders for repo-branch/repo-tag methods, 
        # but the templates use them as base stages anyway for Nexus/URL too
        # to have a consistent build environment (scripts, etc)
        ensure_builders(registry, java_version, tool, args.get('--build-builders'), args.get('--pull'), dry_run=args['--dry-run'])
        
        build_service(name, svc_conf, args['--dry-run'], args['--no-cache'])

if __name__ == '__main__':
    main()
