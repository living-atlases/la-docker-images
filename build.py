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
  --registry=<reg>        Docker registry to push to [default: hub.docker.com/u/livingatlases].
  --repo=<url>            Override git repository URL.
  --branch=<branch>       Override git branch (for repo-branch method).
  --commit=<commit>       Override git commit (for repo-branch method).
  
  # Actions
  --push                  Push images to registry after successful build.
  --dry-run               Generate Dockerfiles in build/ directory but do NOT build images.
  --no-cache              Do not use Docker cache when building.
  --pull                  Always attempt to pull a newer version of the image.
  
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
import json
import shutil
import subprocess
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
# Constants
DEFAULT_REGISTRY = "hub.docker.com/u/livingatlases"
DEFAULT_DEPENDENCIES_URL = "https://raw.githubusercontent.com/living-atlases/la-toolkit-backend/master/assets/dependencies.yaml"
# Defaults if not found in dependencies
DEFAULT_JAVA_VERSION = "11" 
BUILD_DIR_BASE = os.path.join(SCRIPT_DIR, "build")
TEMPLATES_DIR = os.path.join(SCRIPT_DIR, "templates")
SERVICES_DIR = os.path.join(SCRIPT_DIR, "services")

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
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                return True, nexus_url
            else:
                return False, nexus_url
    except urllib.error.URLError as e:
        return False, nexus_url
    except Exception as e:
        return False, nexus_url

def get_nexus_versions(service_name, config, n=1):
    """Fetch last N versions from Nexus metadata"""
    artifact = config.get('artifacts', service_name)
    # Default to releases for metadata search
    nexus_base = "https://nexus.ala.org.au/repository/releases"
    url = f"{nexus_base}/au/org/ala/{artifact}/maven-metadata.xml"
    
    print(f"   🔎 Fetching metadata for {service_name}: {url}")
    try:
        with urllib.request.urlopen(url) as response:
            if response.status != 200:
                print(f"   ⚠️  Failed to fetch metadata for {service_name}")
                return []
            tree = ET.parse(response)
            root = tree.getroot()
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
        print(f"   ⚠️  Error fetching metadata for {service_name}: {e}")
        return []

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
        'JAVA_VERSION': str(config.get('java_version', DEFAULT_JAVA_VERSION)),
        'ARTIFACT_ID': config.get('artifacts', service_name),
        'EXTENSION': config.get('extension', 'war'),
        'CLASSIFIER': config.get('classifier', ''),
        'REPO': config.get('repository', ''),
        'BRANCH': config.get('branch', 'master'),
        'LOG_DIR': config.get('log_dir', ''),
        'LOG_CONFIG_FILENAME': config.get('log_config_filename', ''),
        'LOGGING_CONFIG': "", 
        'JAVA_OPTS': java_opts.strip(),
        'PORT': str(config.get('port', 8080))
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
        return

    # Docker Build Command
    cmd = [
        "docker", "build",
        "-t", image_name,
        "--build-arg", f"BUILD_METHOD={service_config['build_method']}",
        "--build-arg", f"VERSION={version}",
        "." # Context is build_path
    ]
    
    if no_cache:
        cmd.append("--no-cache")
        
    print(f"   🔨 Building {image_name}...")
    try:
        subprocess.check_call(cmd, cwd=build_path)
        print(f"   ✅ Build successful: {image_name}")
        
        if service_config['push']:
            print(f"   📤 Pushing {image_name}...")
            subprocess.check_call(["docker", "push", image_name])
            print("   ✅ Push successful")
            
    except subprocess.CalledProcessError as e:
        print(f"   ❌ Build failed for {service_name}")
        sys.exit(1)

        print(f"   ❌ Build failed for {service_name}")
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
    else:
        services_to_build_names = []
        
    if not services_to_build_names:
        print("No services selected to build.")
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
            versions = get_nexus_versions(name, svc_conf, n_tags)
            
            if not versions:
                print(f"   ⚠️  No versions found for {name} in Nexus metadata. Keeping 'latest' (will likely fail).")
                expanded_build_list.append((name, svc_conf))
            else:
                print(f"   ✨ Resolved versions for {name}: {versions}")
                for v in versions:
                    v_conf = svc_conf.copy()
                    v_conf['version'] = v
                    expanded_build_list.append((name, v_conf))
        else:
            # Explicit version or not Nexus
            expanded_build_list.append((name, svc_conf))

    # --- RESOLVE VALIDATION & JAVA VERSIONS ---
    final_build_tasks = [] #(name, config)
    
    for name, svc_conf in expanded_build_list:
        # Determine Java Version
        if 'java_version' not in svc_conf or svc_conf['java_version'] == DEFAULT_JAVA_VERSION:
             # Try to resolve dynamically if possible or if it was default
             if deps_utils and dependencies:
                 v = svc_conf.get('version', 'latest')
                 dyn = deps_utils.determine_java_version(name, v, dependencies)
                 svc_conf['java_version'] = dyn
                 # print(f"   ☕ Resolved Java Version for {name} ({v}): {dyn}")
             
             if 'java_version' not in svc_conf:
                 svc_conf['java_version'] = DEFAULT_JAVA_VERSION

        final_build_tasks.append((name, svc_conf))

    # --- GLOBAL CHECK PHASE ---
    print("\n🔎 Checking Nexus URLs for all selected services...")
    check_results = []
    has_failures = False
    
    for name, svc_conf in final_build_tasks:
        version = svc_conf.get('version')
        
        # Only check if build method is nexus
        if svc_conf.get('build_method') == 'nexus':
            success, url = check_nexus_url(name, svc_conf)
            status_icon = "✅" if success else "❌"
            check_results.append({
                'name': name,
                'version': version,
                'url': url,
                'success': success,
                'icon': status_icon
            })
            if not success:
                has_failures = True
            
            # Print immediate feedback
            print(f"   {status_icon} {name} ({version}): {url}")
        else:
             print(f"   ⏭️  {name} ({version}): Skipped (method: {svc_conf.get('build_method')})")

    # If failures, abort (even in dry-run, we show results then stop)
    if has_failures:
        print("\n❌ Nexus URL Check Failed for the following services:")
        for res in check_results:
            if not res['success']:
                 print(f"   - {res['name']} ({res['version']}) -> {res['url']}")
        
        print("\n🛑 Aborting verify/build process due to invalid URLs.")
        sys.exit(1)
        
    print("\n✅ All Nexus URLs validated. Proceeding...\n")

    # --- BUILD PHASE ---
    for name, svc_conf in final_build_tasks:
        build_service(name, svc_conf, args['--dry-run'], args['--no-cache'])

if __name__ == '__main__':
    main()
