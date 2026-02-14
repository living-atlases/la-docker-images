pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    parameters {
        choice(name: 'SERVICE', choices: ['all', 'collectory', 'ala-hub', 'biocache-service', 'bie-index', 'regions', 'cas', 'userdetails', 'apikey', 'cas-management', 'spatial-service', 'dashboard', 'alerts', 'biocollect', 'pdfgen', 'ecodata', 'ala-namematching-server', 'ala-sensitive-data-server', 'image-service', 'doi-service', 'data-quality-filter-service'], description: 'Service to build')
        string(name: 'TAG', defaultValue: '', description: 'Version/Tag to build (leave empty for latest/develop)')
        string(name: 'BRANCH', defaultValue: '', description: 'Git branch for repo-branch builds (optional)')
        booleanParam(name: 'PUSH', defaultValue: false, description: 'Push images to Docker Hub')
        booleanParam(name: 'DRY_RUN', defaultValue: false, description: 'Dry Run (generate Dockerfiles only)')
        booleanParam(name: 'FORCE_PULL', defaultValue: true, description: 'Pull base images')
    }

    environment {
        // Build Config
        DOCKER_REGISTRY_CREDS = 'docker-hub' // ID of credentials in Jenkins
    }

    stages {
        stage('Setup Environment') {
            steps {
                script {
                    sh "python3 -m venv venv"
                    sh "./venv/bin/pip install -r requirements.txt"
                }
            }
        }

        stage('Build & Push') {
            steps {
                script {
                    def buildCmd = "./venv/bin/python build.py"

                    // Service Selection
                    if (params.SERVICE && params.SERVICE != 'all') {
                        buildCmd += " --service=${params.SERVICE}"
                    } else {
                        buildCmd += " --all"
                    }

                    // Version/Tag
                    if (params.TAG) {
                        buildCmd += " --tag=${params.TAG}"
                    }
                    
                    // Branch override
                    if (params.BRANCH) {
                        buildCmd += " --branch=${params.BRANCH}"
                    }

                    // Options
                    if (params.DRY_RUN) {
                        buildCmd += " --dry-run"
                    }

                    if (params.FORCE_PULL) {
                        buildCmd += " --pull"
                    }

                    // Registry Login & Push
                    if (params.PUSH && !params.DRY_RUN) {
                        buildCmd += " --push"
                        
                        withCredentials([usernamePassword(credentialsId: env.DOCKER_REGISTRY_CREDS, usernameVariable: 'DOCKER_USER', passwordVariable: 'DOCKER_PASS')]) {
                            sh "echo ${DOCKER_PASS} | docker login -u ${DOCKER_USER} --password-stdin"
                            sh buildCmd
                        }
                    } else {
                        sh buildCmd
                    }
                }
            }
        }
    }

    post {
        always {
            cleanWs()
        }
        success {
            echo "Build successful!"
        }
        failure {
            echo "Build failed!"
        }
    }
}
