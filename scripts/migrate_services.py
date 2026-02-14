import yaml
import os

# Source and destination paths
SOURCE_FILE = '/home/vjrj/proyectos/gbif/dev/ala-install-docker/ansible/roles/docker-compose/vars/docker-services-desc.yaml'
DEST_FILE = '../services-definition.yml'

def main():
    if not os.path.exists(SOURCE_FILE):
        print(f"Error: Source file not found: {SOURCE_FILE}")
        return

    with open(SOURCE_FILE, 'r') as f:
        source_data = yaml.safe_load(f)

    services = {}
    
    # Fields to keep (and rename if necessary)
    # We keep: name, description (desc), build_tool (buildTool), repository, artifacts, classifier, port
    # We discard: versions, java_versions (handled by backend), etc.
    
    source_services = source_data.get('docker_services_desc', {})
    
    for key, data in source_services.items():
        # Determine the new key (Nexus artifact name)
        artifact_name = data.get('artifacts', '').split(' ')[0] # Take first if multiple
        if not artifact_name:
            print(f"Skipping {key}: No artifact name found")
            continue
            
        # Special case for pipelines -> la-pipelines (as per user instruction)
        if key == 'pipelines':
            artifact_name = 'la-pipelines'
            
        new_service = {}
        
        # Map fields
        new_service['name'] = data.get('name', artifact_name)
        new_service['description'] = data.get('desc', '')
        new_service['build_tool'] = data.get('buildTool', 'maven') # Default to maven? Or unknown?
        new_service['repository'] = data.get('repository', '')
        
        # Keep artifacts field just in case, but key is the main identifier now
        new_service['artifacts'] = artifact_name
        
        # Special handling for pipelines build_dir
        if key == 'pipelines':
             new_service['build_dir'] = 'livingatlas'
             new_service['build_tool'] = 'maven' # Ensure maven
             
        # Copy other useful fields if present
        if 'port' in data:
            new_service['port'] = data['port']
            
        if 'log_config_filename' in data:
            new_service['log_config_filename'] = data['log_config_filename']

        # Add to new dictionary
        services[artifact_name] = new_service
        print(f"Migrated {key} -> {artifact_name}")

    # Write to destination
    with open(DEST_FILE, 'w') as f:
        yaml.dump({'services': services}, f, default_flow_style=False, sort_keys=False)
    
    print(f"Successfully migrated {len(services)} services to {DEST_FILE}")

if __name__ == '__main__':
    main()
