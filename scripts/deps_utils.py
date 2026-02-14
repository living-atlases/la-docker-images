import requests
import yaml
import sys

import os
import time
from pathlib import Path
from packaging import version as pkg_version

# Mapping from local service name (services-definition.yml) to dependencies.yaml key
NAME_MAPPING = {
    'ala-bie-hub': 'ala-bie',
    'image-service': 'images',
    'specieslist-webapp': 'species-lists',
    'logger-service': 'logger',
    'spatial-hub': 'spatial',
    'sds-webapp2': 'sds',
    'doi-service': 'doi',
    'ala-namematching-server': 'namematching',
    'ala-sensitive-data-server': 'sensitive-data',
    'data-quality-filter-service': 'data-quality',
    'la-pipelines': 'pipelines'
}

CACHE_DIR = Path.home() / ".cache" / "la-docker-images"
CACHE_FILE = CACHE_DIR / "dependencies.yaml"
CACHE_DURATION_SECONDS = 24 * 60 * 60  # 24 hours

def get_cached_dependencies():
    """
    Retrieve dependencies from cache if valid.
    """
    if not CACHE_FILE.exists():
        return None

    # Check file age
    file_age = time.time() - CACHE_FILE.stat().st_mtime
    if file_age > CACHE_DURATION_SECONDS:
        return None

    try:
        with open(CACHE_FILE, 'r') as f:
            print(f"Loading dependencies from cache: {CACHE_FILE}")
            return yaml.safe_load(f)
    except Exception as e:
        print(f"Warning: Could not load cache: {e}")
        return None

def save_to_cache(content):
    """
    Save dependencies to cache.
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, 'w') as f:
            f.write(content)
        print(f"Saved dependencies to cache: {CACHE_FILE}")
    except Exception as e:
        print(f"Warning: Could not save to cache: {e}")

def load_dependencies(url_or_path):
    """
    Load dependencies from URL or local path.
    """
    try:
        if url_or_path.startswith('http'):
            # Try to load from cache first if it's a remote URL
            cached_data = get_cached_dependencies()
            if cached_data:
                return cached_data

            print(f"Fetching dependencies from {url_or_path}...")
            resp = requests.get(url_or_path)
            resp.raise_for_status()
            
            # Save to cache
            save_to_cache(resp.text)
            
            return yaml.safe_load(resp.text)
        else:
            with open(url_or_path, 'r') as f:
                return yaml.safe_load(f)
    except Exception as e:
        print(f"Warning: Could not load dependencies from {url_or_path}: {e}")
        return {}

def matches_constraint(version_str, constraint_str):
    """
    Check if a version matches a constraint.
    """
    version_str = str(version_str).strip()
    constraint_str = str(constraint_str).strip()

    try:
        ver = pkg_version.parse(version_str)
    except Exception:
        return False

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
    # default_java = '11' # REMOVED: Should not fallback to default
    
    # Resolve name mapping
    dep_key = NAME_MAPPING.get(service_name, service_name)
    
    # Try direct match or variations
    if dep_key not in dependencies_dict:
        if dep_key.replace('-', '_') in dependencies_dict:
            dep_key = dep_key.replace('-', '_')
        elif dep_key.replace('_', '-') in dependencies_dict:
            dep_key = dep_key.replace('_', '-')
        else:
            # Service not found in dependencies
            return None

    service_deps = dependencies_dict.get(dep_key, {})
    if not service_deps:
        return None

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
        try:
            if highest_java is None or int(java_ver) > int(highest_java):
                highest_java = java_ver
        except:
             pass
            
        if service_version == 'develop':
            continue
            
        try:
            if matches_constraint(service_version, constraint):
                matched_java = java_ver
        except Exception:
            pass
            
    if service_version == 'develop' or not service_version:
        return highest_java
        
    return matched_java
