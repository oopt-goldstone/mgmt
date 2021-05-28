#!/usr/bin/env python3

import paramiko
import argparse
import sys
from scp import SCPClient
import time

from .common import *


def main(host, username, password):
    with paramiko.SSHClient() as cli:
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cli.connect(host, username=username, password=password)

        ssh(
            cli, "kubectl delete -f /var/lib/rancher/k3s/server/manifests/mgmt || true"
        )  # can fail
        ssh(cli, "rm -rf /var/lib/rancher/k3s/server/manifests/mgmt")

        ssh(cli, "systemctl restart usonic")

        # stop South system service
        ssh(cli, "systemctl stop gs-south-system || true")  # can fail
        # stop Goldstone Management service
        ssh(cli, "systemctl stop gs-mgmt || true")  # can fail
        # stop NETOPEER2 service
        ssh(cli, "systemctl stop netopeer2 || true")  # can fail
        # stop SNMP service
        ssh(cli, "systemctl stop gs-snmp || true")  # can fail

        ssh(cli, "kubectl delete -f /var/lib/rancher/k3s/server/manifests/mgmt || true") # can fail

        run(
            "docker save -o /tmp/gs-mgmt.tar gs-test/gs-mgmt gs-test/gs-mgmt-netopeer2 gs-test/gs-mgmt-snmpd gs-test/gs-mgmt-south-sonic gs-test/gs-mgmt-south-onlp gs-test/gs-mgmt-south-tai gs-test/gs-mgmt-north-snmp gs-test/gs-mgmt-xlate-openconfig"
        )

        scp = SCPClient(cli.get_transport())
        scp.put("/tmp/gs-mgmt.tar", "/tmp")
        ssh(cli, "ctr images import /tmp/gs-mgmt.tar")

        scp.put(
            "./ci/k8s",
            recursive=True,
            remote_path="/var/lib/rancher/k3s/server/manifests/mgmt",
        )

        run("rm -rf deb && mkdir -p deb")
        run(
            'docker run -v `pwd`/deb:/data -w /data gs-test/gs-mgmt-builder:latest sh -c "cp /usr/share/debs/libyang/libyang1_*.deb /usr/share/debs/sysrepo/sysrepo_*.deb /data/"'
        )

        ssh(cli, "rm -rf /tmp/deb")
        scp.put("deb", recursive=True, remote_path="/tmp/deb")
        ssh(cli, "dpkg -i /tmp/deb/*.deb")

        run("make docker")
        ssh(cli, "rm -rf /tmp/wheels")
        ssh(cli, "mkdir -p /tmp/wheels/cli /tmp/wheels/system")
        scp.put("src/north/cli/dist", recursive=True, remote_path="/tmp/wheels/cli")
        scp.put(
            "src/south/system/dist", recursive=True, remote_path="/tmp/wheels/system"
        )
        ssh(cli, "pip3 uninstall -y gscli gssystem")
        ssh(cli, "pip3 install /tmp/wheels/cli/dist/*.whl")
        ssh(cli, "pip3 install /tmp/wheels/system/dist/*.whl")

        # ssh(cli, 'gscli -c "show version"')

        ssh(cli, "rm -rf /dev/shm/sr_*")
        ssh(cli, "rm -rf /var/lib/sysrepo/*")
        ssh(cli, "kubectl apply -f /var/lib/rancher/k3s/server/manifests/mgmt")

        check_pod(cli, "gs-mgmt-sonic")
        check_pod(cli, "gs-mgmt-onlp")
        check_pod(cli, "gs-mgmt-tai")
        check_pod(cli, "gs-mgmt-snmp")
        check_pod(cli, "gs-mgmt-openconfig")

        def restart_gssouth_system():
            max_iteration = 3
            for i in range(max_iteration):
                ssh(cli, "systemctl restart gs-south-system")
                time.sleep(1)
                output = ssh(cli, "systemctl status gs-south-system")
                if "running" in output:
                    print("Goldstone South System daemon is RUNNING")
                    break
                time.sleep(5)
            else:
                print("Goldstone South System daemon is NOT RUNNING")
                ssh(cli, "journalctl -u gssouth_system")
                sys.exit(1)

        restart_gssouth_system()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Goldstone CI tool")
    parser.add_argument("host")
    parser.add_argument("--username", default="root")
    parser.add_argument("--password", default="x1")

    args = parser.parse_args()
    main(args.host, args.username, args.password)
