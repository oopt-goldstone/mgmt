pipeline {
  agent any

  parameters {
    string(name: 'DEVICE', defaultValue: '10.10.10.115', description: 'IP address of the test device')
    string(name: 'ARM_DEVICE', defaultValue: '10.10.10.119', description: 'IP address of the test device')
    booleanParam(name: 'FORCE_BUILD_BUILDER', defaultValue: false, description: 'build builder image forcibly')
  }

  stages {
    stage('Setup') {
      steps {
          sh 'env'
          script {
              env.SKIP = 0
              if (env.BRANCH_NAME == 'master' ) {
                  env.BUILD_BUILDER = 1 // always build builder
              } else if ( env.BRANCH_NAME.startsWith('PR') ) {
                  env.GS_MGMT_IMAGE_PREFIX = 'gs-test/'
                  // if sm/, patches/, builder.Dockerfile, build_onlp.sh is updated
                  // build the builder
                  env.BUILD_BUILDER = sh(returnStatus: true, script: "git diff --compact-summary HEAD origin/master | grep 'sm/\\|patches/\\|builder.Dockerfile'") ? 0 : 1
              } else {
                  env.SKIP = 1
                  env.BUILD_BUILDER = 0
                  currentBuild.result = 'SUCCESS'
                  echo "no need to build ${env.BRANCH_NAME}"
              }
              if ( params.FORCE_BUILD_BUILDER ) {
                  env.BUILD_BUILDER = 1
              }
          }
          sh 'env'
      }
    }

    stage('Lint') {
      when {
        environment name: 'SKIP', value: '0'
      }
      steps {
        sh 'apk add --update docker make python2'
        sh 'make tester'
        sh "docker run -t -v `pwd`:`pwd` -w `pwd` gs-test/gs-mgmt-test:latest-amd64 make lint"
      }
    }

    stage('Unittest') {
      when {
        environment name: 'SKIP', value: '0'
      }
      steps {
        sh 'apk add --update docker make python2'
        sh 'make tester'
        sh "docker run -t -v `pwd`:`pwd` -w `pwd` gs-test/gs-mgmt-test:latest-amd64 make unittest"
      }
    }

    stage('Build') {
      failFast true
      parallel {
        stage('amd64') {
          environment {
            ARCH = 'amd64'
          }
          stages {
            stage('Build Builder') {
              when {
                environment name: 'BUILD_BUILDER', value: '1'
              }
              steps {
                  sh 'make builder'
              }
            }
            stage('Build') {
              when {
                environment name: 'SKIP', value: '0'
              }
              steps {
                  sh 'make snmpd'
                  sh 'make base-image'
                  sh 'make images'
                  sh 'make host-packages'
              }
            }
          }
        }

        stage('arm64') {
          environment {
            ARCH = 'arm64'
          }
          stages {
            stage('Build Builder') {
              when {
                environment name: 'BUILD_BUILDER', value: '1'
              }
              steps {
                  sh 'make builder'
              }
            }

            stage('Build') {
              when {
                environment name: 'SKIP', value: '0'
              }
              steps {
                  sh 'make snmpd'
                  sh 'make base-image'
                  sh 'make images'
                  sh 'make host-packages'
              }
            }
          }
        }
      }
    }

    stage('Load') {
      failFast true
      parallel {
        stage('amd64') {
          when {
            branch pattern: "^PR.*", comparator: "REGEXP"
            environment name: 'SKIP', value: '0'
          }
          environment {
            ARCH = 'amd64'
          }
          stages {
            stage('Load') {
              steps {
                sh 'make tester'
                timeout(time: 30, unit: 'MINUTES') {
                    sh "docker run -v /var/run/docker.sock:/var/run/docker.sock -e GS_MGMT_IMAGE_PREFIX=$GS_MGMT_IMAGE_PREFIX -t -v `pwd`:`pwd` -w `pwd` gs-test/gs-mgmt-test:latest-amd64 python3 -m ci.tools.load ${params.DEVICE} --arch $ARCH"
                }
              }
            }
          }
        }

        stage('arm64') {
          when {
            branch pattern: "^PR.*", comparator: "REGEXP"
            environment name: 'SKIP', value: '0'
          }
          environment {
            ARCH = 'arm64'
          }
          stages {
            stage('Load') {
              steps {
                sh 'ARCH=amd64 make tester' // tester image doesn't need to be arm64
                timeout(time: 30, unit: 'MINUTES') {
                    sh "docker run -v /var/run/docker.sock:/var/run/docker.sock -e GS_MGMT_IMAGE_PREFIX=$GS_MGMT_IMAGE_PREFIX -t -v `pwd`:`pwd` -w `pwd` gs-test/gs-mgmt-test:latest-amd64 python3 -m ci.tools.load ${params.ARM_DEVICE} --arch $ARCH"
                }
              }
            }
          }
        }
      }
    }

    stage('Test') {
      failFast true
      parallel {
        stage('test south SONiC on amd64') {
          when {
            branch pattern: "^PR.*", comparator: "REGEXP"
            environment name: 'SKIP', value: '0'
          }
          environment {
            ARCH = 'amd64'
          }
          stages {
            stage('Test') {
              steps {
                sh 'make tester'
                timeout(time: 30, unit: 'MINUTES') {
                  sh "docker run -v /var/run/docker.sock:/var/run/docker.sock -e GS_MGMT_IMAGE_PREFIX=$GS_MGMT_IMAGE_PREFIX -e GS_TEST_HOST=${params.DEVICE} -t -v `pwd`:`pwd` -w `pwd` gs-test/gs-mgmt-test:latest-amd64 python3 -m ci.tools.test -f -v TestSouthSONiC"
                }
              }
            }
          }
        }
        stage('test south TAI, ONLP and system on amd64') {
          when {
            branch pattern: "^PR.*", comparator: "REGEXP"
            environment name: 'SKIP', value: '0'
          }
          environment {
            ARCH = 'amd64'
          }
          stages {
            stage('Test') {
              steps {
                sh 'make tester'
                timeout(time: 30, unit: 'MINUTES') {
                  sh "docker run -v /var/run/docker.sock:/var/run/docker.sock -e GS_MGMT_IMAGE_PREFIX=$GS_MGMT_IMAGE_PREFIX -e GS_TEST_HOST=${params.DEVICE} -t -v `pwd`:`pwd` -w `pwd` gs-test/gs-mgmt-test:latest-amd64 python3 -m ci.tools.test -f -v TestSouthTAI"
                }
                timeout(time: 30, unit: 'MINUTES') {
                  sh "docker run -v /var/run/docker.sock:/var/run/docker.sock -e GS_MGMT_IMAGE_PREFIX=$GS_MGMT_IMAGE_PREFIX -e GS_TEST_HOST=${params.DEVICE} -t -v `pwd`:`pwd` -w `pwd` gs-test/gs-mgmt-test:latest-amd64 python3 -m ci.tools.test -f -v TestSouthONLP"
                }
                timeout(time: 30, unit: 'MINUTES') {
                  sh "docker run -v /var/run/docker.sock:/var/run/docker.sock -e GS_MGMT_IMAGE_PREFIX=$GS_MGMT_IMAGE_PREFIX -e GS_TEST_HOST=${params.DEVICE} -t -v `pwd`:`pwd` -w `pwd` gs-test/gs-mgmt-test:latest-amd64 python3 -m ci.tools.test -f -v TestSouthSystem"
                }
              }
            }
          }
        }
        stage('test on arm64') {
          when {
            branch pattern: "^PR.*", comparator: "REGEXP"
            environment name: 'SKIP', value: '0'
          }
          environment {
            ARCH = 'arm64'
          }
          stages {
            stage('Test') {
              steps {
                sh 'ARCH=amd64 make tester' // tester image doesn't need to be arm64
                timeout(time: 30, unit: 'MINUTES') {
                  sh "docker run -v /var/run/docker.sock:/var/run/docker.sock -e GS_MGMT_IMAGE_PREFIX=$GS_MGMT_IMAGE_PREFIX -e GS_TEST_HOST=${params.ARM_DEVICE} -t -v `pwd`:`pwd` -w `pwd` gs-test/gs-mgmt-test:latest-amd64 python3 -m ci.tools.test -f -v TestSouthTAI"
                }
                timeout(time: 30, unit: 'MINUTES') {
                    sh "docker run -v /var/run/docker.sock:/var/run/docker.sock -e GS_MGMT_IMAGE_PREFIX=$GS_MGMT_IMAGE_PREFIX -e GS_TEST_HOST=${params.ARM_DEVICE} -t -v `pwd`:`pwd` -w `pwd` gs-test/gs-mgmt-test:latest-amd64 python3 -m ci.tools.test -f -v TestSouthONLP"
                }
                timeout(time: 30, unit: 'MINUTES') {
                  sh "docker run -v /var/run/docker.sock:/var/run/docker.sock -e GS_MGMT_IMAGE_PREFIX=$GS_MGMT_IMAGE_PREFIX -e GS_TEST_HOST=${params.ARM_DEVICE} -t -v `pwd`:`pwd` -w `pwd` gs-test/gs-mgmt-test:latest-amd64 python3 -m ci.tools.test -f -v TestSouthGearbox"
                }
              }
            }
          }
        }
      }
    }

    stage('Test NETCONF') {
      when {
        branch pattern: "^PR.*", comparator: "REGEXP"
        environment name: 'SKIP', value: '0'
      }
      steps {
        sh 'make tester'
        sh "docker run -v /var/run/docker.sock:/var/run/docker.sock -e GS_MGMT_IMAGE_PREFIX=$GS_MGMT_IMAGE_PREFIX -t -v `pwd`:`pwd` -w `pwd` gs-test/gs-mgmt-test:latest-amd64 python3 -m ci.tools.test_np2 ${params.DEVICE}"
      }
    }

    stage('Test SNMP') {
      failFast true
      parallel {
        stage('amd64') {
          when {
            branch pattern: "^PR.*", comparator: "REGEXP"
            environment name: 'SKIP', value: '0'
          }
          environment {
            ARCH = 'amd64'
          }
          stages {
            stage('Test SNMP on amd64') {
              steps {
                sh 'make tester'
                sh "docker run -t -v `pwd`:`pwd` -w `pwd` gs-test/gs-mgmt-test:latest-amd64 python3 -m ci.tools.test_snmp ${params.DEVICE}"
              }
            }
          }
        }
        stage('arm64') {
          when {
            branch pattern: "^PR.*", comparator: "REGEXP"
            environment name: 'SKIP', value: '0'
          }
          environment {
            ARCH = 'arm64'
          }
          stages {
            stage('Test SNMP on arm64') {
              steps {
                sh 'ARCH=amd64 make tester' // tester image doesn't need to be arm64
                sh "docker run -t -v `pwd`:`pwd` -w `pwd` gs-test/gs-mgmt-test:latest-amd64 python3 -m ci.tools.test_snmp ${params.ARM_DEVICE}"
              }
            }
          }
        }
      }
    }

    stage('Release') {
      when {
        buildingTag()
      }
      steps {
        sh 'make release'
        archiveArtifacts artifacts: 'builds/*.tar.gz', fingerprint: true
        withCredentials([string(credentialsId: 'github-token', variable: 'GH_TOKEN')]) {
          sh '''#!/bin/bash
          gh auth login
          gh auth status
          gh release create $TAG_NAME ./builds/*tar.gz
          '''
        }
      }
    }
  }

  post {
    success {
      script {
        if ( env.BRANCH_NAME != 'master' ) {
          deleteDir() /* clean up our workspace */
        }
      }
    }
  }

}
// vim: ft=groovy
