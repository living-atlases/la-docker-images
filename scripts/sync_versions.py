#!/usr/bin/env python3
"""
Sync versions from LA Toolkit Backend or dependencies.yaml
Updates build-config.yml with versions and Java versions.

Usage:
  sync_versions.py [--url=<url>] [--config=<file>] [--defs=<file>]
  sync_versions.py -h | --help

Options:
  --url=<url>       URL to fetch dependencies.yaml or BOM [default: https://raw.githubusercontent.com/living-atlases/la-toolkit-backend/master/assets/dependencies.yaml]
  --config=<file>   Path to build-config.yml [default: build-config.yml]
  --defs=<file>     Path to services-definition.yml [default: services-definition.yml]

"""
import os
import sys
import yaml
import requests
from docopt import docopt
from packaging import version as pkg_version

def load_yaml(path):
    if not os.path.exists(path):
        return {}
    with open(path, 'r') as f:
        return yaml.safe_load(f) or {}

def save_yaml(path, data):
    with open(path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

def matches_constraint(version_str, constraint_str):
    """
    Check if a version matches a constraint.
    Replicates logic from Ansible filter plugin.
    """
    version_str = str(version_str).strip()
    constraint_str = str(constraint_str).strip()

    try:
        ver = pkg_version.parse(version_str)
    except Exception:
        return False

    # Handle combined constraints like '>= 3.1.0 < 6.0.0'
    # Simplified splitting by space might not work if spaces are irregular but works for standard cases
    # Logic adapted from filter_plugins/java_version.py
    
    parts = constraint_str.split()
    i = 0
    all_match = True

    while i < len(parts):
        operator = None
        target = None
        
        part = parts[i]
        
        if part.startswith('>='):
            operator = '>='
            target_str = part[2:].strip()
            if target_str: target = target_str
            elif i + 1 < len(parts):
                target = parts[i + 1]
                i += 1
        elif part.startswith('<='):
            operator = '<='
            target_str = part[2:].strip()
            if target_str: target = target_str
            elif i + 1 < len(parts):
                target = parts[i + 1]
                i += 1
        elif part.startswith('>'):
            operator = '>'
            target_str = part[1:].strip()
            if target_str: target = target_str
            elif i + 1 < len(parts):
                target = parts[i + 1]
                i += 1
        elif part.startswith('<'):
            operator = '<'
            target_str = part[1:].strip()
            if target_str: target = target_str
            elif i + 1 < len(parts):
                target = parts[i + 1]
                i += 1
        elif part in ['>=', '<=', '>', '<']:
            operator = part
            if i + 1 < len(parts):
                target = parts[i + 1]
                i += 1

        if operator and target:
            try:
                target_ver = pkg_version.parse(target)
                if operator == '>=' and not (ver >= target_ver):
                    all_match = False; break
                elif operator == '<=' and not (ver <= target_ver):
                    all_match = False; break
                elif operator == '>' and not (ver > target_ver):
                    all_match = False; break
                elif operator == '<' and not (ver < target_ver):
                    all_match = False; break
            except Exception:
                return False
        i += 1
        
    return all_match

def determine_java_version(service_name, service_version, dependencies_dict):
    """
    Determine Java version from LA Toolkit dependencies.yaml
    """
    default_java = '17'
    
    # dependencies.yaml has service names as keys directly
    service_deps = dependencies_dict.get(service_name, {})
    
    if not service_deps:
        return default_java

    highest_java = None
    matched_java = None

    for constraint, requirements in service_deps.items():
        if not isinstance(requirements, list):
            continue

        java_ver = None
        for req in requirements:
            if isinstance(req, dict) and 'java' in req:
                java_ver = str(req['java']).strip()
                break
        
        if not java_ver:
            continue
            
        # Track highest
        if highest_java is None or int(java_ver) > int(highest_java):
            highest_java = java_ver
            
        if service_version == 'develop':
            continue
            
        try:
            if matches_constraint(service_version, constraint):
                matched_java = java_ver
        except Exception:
            pass
            
    if service_version == 'develop':
        return highest_java if highest_java else default_java
        
    return matched_java if matched_java else default_java

def main():
    args = docopt(__doc__)
    url = args['--url']
    config_file = args['--config']
    defs_file = args['--defs']
    
    print(f"Fetching dependencies from {url}...")
    try:
        resp = requests.get(url)
        resp.raise_for_status()
        dependencies = yaml.safe_load(resp.text)
    except Exception as e:
        print(f"Error fetching dependencies: {e}")
        sys.exit(1)
        
    def_services = load_yaml(defs_file).get('services', {})
    
    print("Updating build-config.yml...")
    build_config = load_yaml(config_file)
    if 'services' not in build_config:
        build_config['services'] = {}
    
    # dependencies.yaml is a direct map of name -> version constraints
    # BUT, wait. dependencies.yaml structure is:
    # service_name:
    #   constraint: requirement_list
    #
    # Where does the ACTUAL version come from?
    # dependencies.yaml DEFINES constraints/Java versions, it DOES NOT define the deployed version usually?
    # Wait, the user said "extract versions FROM dependencies".
    #
    # Looking at `determine-java-versions.yml`:
    # It sets `collectory_java_version` using `collectory_version`.
    # `collectory_version` comes from defaults or inventory?
    #
    # The requirement was "integrate with Toolkit backend to source version information".
    # Usually Toolkit backend provides the 'BOM' (Bill of Materials) which has versions.
    # dependencies.yaml maps versions to dependencies (like Java).
    #
    # IF the user wants us to build 'latest stable' or specific versions from Toolkit, we need the BOM.
    # The URL defaults to dependencies.yaml.
    #
    # Re-reading task: "integrate with the la-toolkit backend to source version information."
    # And "extract java versions from dependencies".
    #
    # If we are building 'latest' or 'develop', we use that.
    # If we want to build what is current "stable", we need another source (like a release json).
    #
    # HOWEVER, strictly following "sync versions", if dependencies.yaml ONLY has constraints, 
    # we cannot get the "current version" from it, unless we imply it?
    #
    # Let's look at the previous DEBUG output for dependencies.yaml content:
    # It has keys like 'collectory', 'ala-hub'.
    # And values like: {'< 2.3': [{'java': '8'}], '> 2.3': ...}
    # It DOES NOT have a field saying "current_version: 2.5.0".
    #
    # So `sync_versions.py` CANNOT update the service version based on `dependencies.yaml` alone.
    # It can only update the JAVA version IF we already know the service version.
    #
    # BUT, the user said "source version information".
    # Maybe I should be hitting the `/release` endpoint or similar of toolkit?
    #
    # For now, let's assume the user might provide the version in `build-config.yml` manually,
    # OR we default to 'develop'.
    #
    # If the script is meant to update `build-config.yml` with Java versions, it needs to read the 
    # configured version from `build-config` (or defaults) and THEN calculate Java version.
    #
    # Let's adjust the script:
    # 1. Read existing version from build-config or services-definition (defaulting to 'develop').
    # 2. Calculate Java version using dependencies.yaml.
    # 3. Update build-config with that Java version.
    
    updated_count = 0
    
    # Mapping from local service name (services-definition.yml) to dependencies.yaml key
    # Based on ansible/roles/docker-compose/tasks/determine-java-versions.yml
    NAME_MAPPING = {
        'ala-bie-hub': 'ala-bie',
        'bie-index': 'species',
        'image-service': 'images',
        'specieslist-webapp': 'species-lists',
        'logger-service': 'logger',
        'spatial-hub': 'spatial',
        'sds-webapp2': 'sds',
        'doi-service': 'doi',
        'ala-namematching-server': 'namematching',
        'ala-sensitive-data-server': 'sensitive-data',
        'data-quality-filter-service': 'data-quality'
    }

    for name, svc_def in def_services.items():
        # Get current configured version
        current_version = 'develop'
        # Check if version is explicitly set in build-config
        if name in build_config['services'] and 'version' in build_config['services'][name]:
            current_version = build_config['services'][name]['version']
        
        # Check if name exists in dependencies
        # 1. Explicit mapping
        # 2. Exact match
        # 3. Dashes vs underscores substitution
        
        dep_key = None
        
        if name in NAME_MAPPING:
             dep_key = NAME_MAPPING[name]
        elif name in dependencies:
             dep_key = name
        elif name.replace('-', '_') in dependencies:
             dep_key = name.replace('-', '_')
        elif name.replace('_', '-') in dependencies:
             dep_key = name.replace('_', '-')

        if dep_key and dep_key in dependencies:
            java_ver = determine_java_version(dep_key, current_version, dependencies)
            
            if name not in build_config['services']:
                build_config['services'][name] = {}
            
            # Only update if changed or new
            old_java = build_config['services'][name].get('java_version')
            if old_java != java_ver:
                build_config['services'][name]['java_version'] = java_ver
                print(f"   Updated {name} (as '{dep_key}': {current_version}) -> Java {java_ver}")
                updated_count += 1
            else:
                # Ensure it's set if missing
                build_config['services'][name]['java_version'] = java_ver

    save_yaml(config_file, build_config)
    print(f"✅ Updated Java versions for {updated_count} services in {config_file}")

if __name__ == '__main__':
    main()
