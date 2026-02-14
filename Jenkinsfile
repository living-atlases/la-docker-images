pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    parameters {
        choice(name: 'SERVICE', choices: ['all', 'ala-bie-hub', 'ala-hub', 'ala-namematching-server', 'ala-sensitive-data-server', 'alerts', 'apikey', 'bie-index', 'biocache-service', 'biocollect', 'cas', 'cas-management', 'collectory', 'dashboard', 'data-quality-filter-service', 'doi-service', 'ecodata', 'image-service', 'la-pipelines', 'logger-service', 'pdfgen', 'regions', 'sds-webapp2', 'spatial-hub', 'spatial-service', 'specieslist-webapp', 'userdetails'], description: 'Service to build')
        string(name: 'TAG', defaultValue: '', description: 'Version/Tag to build (leave empty for latest/develop)')
        string(name: 'BRANCH', defaultValue: '', description: 'Git branch for repo-branch builds (optional)')
        booleanParam(name: 'PUSH', defaultValue: true, description: 'Push images to Docker Hub')
        booleanParam(name: 'DRY_RUN', defaultValue: false, description: 'Dry Run (generate Dockerfiles only)')
        booleanParam(name: 'FORCE_PULL', defaultValue: true, description: 'Pull base images')
    }

    environment {
        // Build Config
        DOCKER_REGISTRY_CREDS = 'docker-hub-la' // ID of credentials in Jenkins
    }

    stages {
        stage('Validate Sync') {
            steps {
                script {
                    sh './scripts/update_jenkinsfile.py --check'
                }
            }
        }

        stage('Setup Environment') {
            steps {
                script {
                    sh '''
                        if [ ! -d venv ]; then
                            python3 -m venv venv
                        fi
                        ./venv/bin/pip install -q -r requirements.txt
                    '''
                }
            }
        }

        stage('Build & Push') {
            steps {
                script {
                    def args = []
                    
                    if (params.SERVICE && params.SERVICE != 'all') {
                        args << "--service=${params.SERVICE}"
                    } else {
                        args << "--all"
                    }

                    if (params.TAG) args << "--tag=${params.TAG}"
                    if (params.BRANCH) args << "--branch=${params.BRANCH}"
                    if (params.DRY_RUN) args << "--dry-run"
                    if (params.FORCE_PULL) args << "--pull"
                    if (params.PUSH && !params.DRY_RUN) args << "--push"

                    def buildCmd = "./venv/bin/python build.py ${args.join(' ')}"

                    if (params.PUSH && !params.DRY_RUN) {
                        withCredentials([usernamePassword(credentialsId: env.DOCKER_REGISTRY_CREDS, usernameVariable: 'DH_USER', passwordVariable: 'DH_PASS')]) {
                            sh 'echo $DH_PASS | docker login -u $DH_USER --password-stdin'
                            try {
                                sh buildCmd
                            } finally {
                                sh 'docker logout'
                            }
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
