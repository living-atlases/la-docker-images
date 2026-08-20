pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    parameters {
        string(name: 'SERVICE', defaultValue: 'all', description: "Service(s) to build (comma-separated, or 'all'). Available: ala-bie-hub, ala-hub, ala-namematching-server, ala-sensitive-data-server, alerts, apikey, bie-index, biocache-service, biocollect, cas, cas-management, collectory, dashboard, data-quality-filter-service, doi-service, ecodata, i18n, image-service, la-pipelines, logger-service, pdfgen, regions, sds-static-home, sds-webapp2, spatial-hub, spatial-service, specieslist-webapp, userdetails")
        string(name: 'SKIP_SERVICES', defaultValue: '', description: "Service(s) to skip (comma-separated). Available: ala-bie-hub, ala-hub, ala-namematching-server, ala-sensitive-data-server, alerts, apikey, bie-index, biocache-service, biocollect, cas, cas-management, collectory, dashboard, data-quality-filter-service, doi-service, ecodata, i18n, image-service, la-pipelines, logger-service, pdfgen, regions, sds-static-home, sds-webapp2, spatial-hub, spatial-service, specieslist-webapp, userdetails")
        string(name: 'N_TAGS', defaultValue: '1', description: 'Number of recent tags to build if version is latest')
        string(name: 'TAG', defaultValue: '', description: 'Version/Tag to build (leave empty for latest/develop)')
        string(name: 'LIST_TAGS', defaultValue: '', description: 'Build exactly these versions, comma-separated (e.g. 1.3,1.4,1.5). For backfilling old tags: N_TAGS can only take the N newest, so reaching an old version means rebuilding everything above it. Does not move `latest`.')
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
                    // Both are stdlib only and run before the venv exists, so a
                    // broken template or a broken artifact resolution is caught
                    // in seconds rather than after hours of building.
                    sh './scripts/test-dockerfile-generation.py'
                    sh 'python3 scripts/test_build_logic.py'
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
                    if (params.LIST_TAGS) args << "--list-tags=${params.LIST_TAGS}"
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

        stage('Image Tests') {
            when {
                expression { !params.DRY_RUN }
            }
            steps {
                script {
                    // A sample, not all ~214 images: each of these starts a
                    // container to check a JAVA_OPTS override actually reaches
                    // the JVM (gh-3), which costs a minute or so per image.
                    // One gradle-built and one maven-built service, newest tag
                    // only. run-container-tests.sh skips cleanly when an image
                    // was not part of this run.
                    // build/ is gitignored and the workspace is wiped after every
                    // run, so a Dockerfile is only there for what this run built.
                    def tested = 0
                    ['userdetails', 'cas'].each { svc ->
                        if (fileExists("build/${svc}/Dockerfile")) {
                            sh "REGISTRY=livingatlases ./scripts/run-container-tests.sh ${svc} latest"
                            tested++
                        }
                    }
                    if (tested == 0) {
                        echo "No sampled service was built in this run, skipping image tests."
                    }
                }
            }
        }
    }

    post {
        always {
            cleanWs()
            // Every rebuild of a tag leaves the image it replaced untagged, and
            // nothing here ever collected them. A --n-tags=10 sweep over 23
            // services left 3102 dangling images and filled the agent's 295GB
            // containerd volume, which then failed every build on the box until
            // it was pruned by hand (126GB reclaimed).
            //
            // Dangling images only: tagged images are the layer cache that keeps
            // subsequent builds cheap, and `prune -a` would take those too, on a
            // shared agent. Build cache is trimmed at a week, so recent layers
            // survive. Never fails the build -- a full disk should surface as the
            // build error it causes, not as a cleanup step going red.
            sh 'docker image prune -f || true'
            sh 'docker builder prune -f --filter until=168h || true'
            sh 'df -h /var/lib/containerd | tail -1 || true'
        }
        success {
            echo "Build successful!"
        }
        failure {
            echo "Build failed!"
        }
    }
}
