#!/usr/bin/env python3
"""
Update Jenkinsfile service descriptions from services-definition.yml.

Usage:
  update_jenkinsfile.py [--check] [--jenkinsfile=<file>] [--defs=<file>]
  update_jenkinsfile.py -h | --help

Options:
  --check               Check if Jenkinsfile is in sync without modifying it.
  --jenkinsfile=<file>  Path to Jenkinsfile [default: Jenkinsfile]
  --defs=<file>         Path to services-definition.yml [default: services-definition.yml]
  -h --help             Show this help message.
"""

import os
import sys
import yaml
import re
from docopt import docopt

def load_services(defs_file):
    if not os.path.exists(defs_file):
        print(f"Error: Services definition file not found: {defs_file}")
        sys.exit(1)
    
    with open(defs_file, 'r') as f:
        data = yaml.safe_load(f)
        if not data or 'services' not in data:
            print(f"Error: No services found in {defs_file}")
            sys.exit(1)
        return sorted(list(data['services'].keys()))

def update_jenkinsfile(jenkinsfile, services, check_only=False):
    if not os.path.exists(jenkinsfile):
        print(f"Error: Jenkinsfile not found: {jenkinsfile}")
        sys.exit(1)

    with open(jenkinsfile, 'r') as f:
        content = f.read()

    # List of services as a string
    services_list_str = ", ".join(services)
    
    # regex for SERVICE and SKIP_SERVICES descriptions
    # We look for something like: string(name: 'SERVICE', defaultValue: '...', description: '...')
    # and we want to replace the content of the description.
    
    patterns = [
        (r"(string\(name:\s*['\"]SERVICE['\"],[^)]*description:\s*)(['\"].*['\"])", f"\"Service(s) to build (comma-separated, or 'all'). Available: {services_list_str}\""),
        (r"(string\(name:\s*['\"]SKIP_SERVICES['\"],[^)]*description:\s*)(['\"].*['\"])", f"\"Service(s) to skip (comma-separated). Available: {services_list_str}\"")
    ]
    
    new_content = content
    modified = False
    
    for pattern, new_desc in patterns:
        match = re.search(pattern, new_content)
        if match:
            found_desc = match.group(2)
            if found_desc != new_desc:
                # Use a lambda or escape backslashes correctly in the replacement string
                new_content = re.sub(pattern, lambda m: m.group(1) + new_desc, new_content)
                modified = True

    if not modified:
        print("✅ Jenkinsfile descriptions are already in sync.")
        return True

    if check_only:
        print("❌ Jenkinsfile descriptions are OUT OF SYNC!")
        return False

    with open(jenkinsfile, 'w') as f:
        f.write(new_content)
    
    print(f"✅ Updated {jenkinsfile} descriptions with {len(services)} services.")
    return True

def main():
    args = docopt(__doc__)
    
    script_dir = os.path.dirname(os.path.realpath(__file__))
    root_dir = os.path.dirname(script_dir)
    
    defs_file = args['--defs']
    if defs_file == 'services-definition.yml':
        defs_file = os.path.join(root_dir, defs_file)
        
    jenkinsfile = args['--jenkinsfile']
    if jenkinsfile == 'Jenkinsfile':
        jenkinsfile = os.path.join(root_dir, jenkinsfile)

    services = load_services(defs_file)
    success = update_jenkinsfile(jenkinsfile, services, check_only=args['--check'])
    
    if not success:
        sys.exit(1)

if __name__ == '__main__':
    main()
