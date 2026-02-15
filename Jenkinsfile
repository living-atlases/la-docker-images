pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    parameters {
        string(name: 'SERVICE', defaultValue: 'all', description: 'Service(s) to build (comma-separated, or "all")')
        string(name: 'SKIP_SERVICES', defaultValue: '', description: 'Service(s) to skip (comma-separated)')
        string(name: 'N_TAGS', defaultValue: '1', description: 'Number of recent tags to build if version is latest')
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
                        params.SERVICE.split(',').each { svc ->
                            def s = svc.trim()
                            if (s) args << "--service=${s}"
                        }
                    } else {
                        args << "--all"
                    }

                    if (params.SKIP_SERVICES) {
                        params.SKIP_SERVICES.split(',').each { svc ->
                            def s = svc.trim()
                            if (s) args << "--skip-service=${s}"
                        }
                    }

                    if (params.N_TAGS) {
                        args << "--n-tags=${params.N_TAGS}"
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
