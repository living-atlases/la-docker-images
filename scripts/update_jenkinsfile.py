#!/usr/bin/env python3
"""
Update Jenkinsfile service choices from services-definition.yml.

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

    # Regex to find the choice(name: 'SERVICE', choices: ['all', ...], description: '...')
    # We want to replace the list inside choices: [...]
    pattern = r"(choice\(name:\s*'SERVICE',\s*choices:\s*\[)([^\]]+)(\],\s*description:\s*'Service to build'\))"
    
    match = re.search(pattern, content)
    if not match:
        print(f"Error: Could not find SERVICE parameter in {jenkinsfile}")
        sys.exit(1)

    # Prepare new choices list. 'all' should always be first.
    new_choices = ["'all'"] + [f"'{s}'" for s in services]
    new_choices_str = ", ".join(new_choices)
    
    # Original choices for comparison
    original_choices_str = match.group(2)
    
    # Check if they are already in sync (ignoring whitespace differences within the list if any)
    # But we want a clean 'all', 'service1', 'service2' format.
    if new_choices_str == original_choices_str.strip():
        print("✅ Jenkinsfile is already in sync.")
        return True

    if check_only:
        print("❌ Jenkinsfile is OUT OF SYNC!")
        print(f"Expected: {new_choices_str}")
        print(f"Found:    {original_choices_str.strip()}")
        return False

    new_content = re.sub(pattern, rf"\1{new_choices_str}\3", content)
    
    with open(jenkinsfile, 'w') as f:
        f.write(new_content)
    
    print(f"✅ Updated {jenkinsfile} with {len(services)} services.")
    return True

def main():
    # docopt uses the module __doc__ by default
    args = docopt(__doc__)
    
    # Resolve paths relative to the script's directory if they are the defaults
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
