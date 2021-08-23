import datetime
import io
import os
import logging
import asyncio
import json
import re

import kubernetes as k
import kubernetes_asyncio as k_async

from jinja2 import Template

USONIC_SELECTOR = os.getenv("USONIC_SELECTOR", "app=usonic")
USONIC_CHECKPOINT = os.getenv("USONIC_CHECKPOINT", "usonic-mgrd")
USONIC_NAMESPACE = os.getenv("USONIC_NAMESPACE", "default")
USONIC_CONFIGMAP = os.getenv("USONIC_CONFIGMAP", "usonic-config")
USONIC_TEMPLATE_DIR = os.getenv("USONIC_TEMPLATE_DIR", "/var/lib/usonic")
PORT_PREFIX = "Ethernet"

logger = logging.getLogger(__name__)


class incluster_apis(object):
    def __init__(self):
        k.config.load_incluster_config()
        k_async.config.load_incluster_config()
        self.usonic_deleted = 0
        self.usonic_core = self.get_podname("usonic-core")
        self.update_bcm_portmap()

    def update_bcm_portmap(self):
        output = self.run_bcmcmd("ps")
        portmap = {}
        for line in output.split("\n"):
            m = re.search("(?P<name>\w+)\(\s*(?P<index>[0-9]+)\)", line)
            if m:
                portmap[int(m.group("index"))] = m.group("name")

        with open(USONIC_TEMPLATE_DIR + "/interfaces.json") as f:
            master = {}
            for i, m in enumerate(json.loads(f.read())):
                master[m["port"]] = (i + 1, m)

        pmap = {}
        for index, name in portmap.items():
            if index in master:
                i, m = master[index]
                pmap[f"{PORT_PREFIX}{i}_1"] = (index, name)
            else:
                for j in range(1, 4):
                    if index - j in master:
                        i, m = master[index - j]
                        pmap[f"{PORT_PREFIX}{i}_{1+j}"] = (index, name)
                        break

        logger.debug(pmap)
        self.bcm_portmap = pmap

    def get_podname(self, name):
        w = k.watch.Watch()
        api = k.client.api.CoreV1Api()
        for event in w.stream(api.list_pod_for_all_namespaces):
            n = event["object"].metadata.name
            if name in n:
                w.stop()
                return n
        raise Exception(f"{name} not found")

    def run_bcmcmd(self, cmd):
        api = k.client.api.CoreV1Api()
        exec_command = ["bcmcmd", cmd]
        logger.debug(f"exec command: {exec_command}")
        resp = k.stream.stream(
            api.connect_get_namespaced_pod_exec,
            self.usonic_core,
            USONIC_NAMESPACE,
            command=exec_command,
            container="syncd",
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )
        logger.debug(f"response: {resp}")
        return resp

    def bcm_ports_info(self, ports):
        def parse(output):
            info = {}
            m = re.search("IF\((?P<iftype>.*?)\)", output)
            if m:
                iftype = m.group("iftype")
                info["iftype"] = iftype

            # auto negotiation enabled
            if "Auto" in output:

                def g(t):
                    m = re.search(f"{t} \((?P<v>.*?)\)", output)
                    if m:
                        v = m.group("v")
                        keys = ["fd", "hd", "intf", "medium", "pause", "lb", "flags"]
                        pattern = " ".join(f"{k} =(?P<{k}>.*?)" for k in keys)
                        m = re.search(pattern, v)
                        v = {}
                        for k in keys:
                            e = m.group(k).strip()
                            if e:
                                v[k] = e.split(",")
                        return v

                info["auto-nego"] = {
                    t.lower(): g(t) for t in ("Ability", "Local", "Remote")
                }

            return info

        logger.debug(f"ports: {list(ports)}")
        output = self.run_bcmcmd_port(ports)
        v = {}
        for line in output.split("\n"):
            m = re.search(f"\s+\*?(?P<name>\w+)\s+", line)
            if m:
                name = m.group("name")
                v[name] = parse(line)

        w = {}
        for port in ports:
            _, name = self.bcm_portmap[port]
            if name in v:
                w[port] = v[name]

        return w

    def run_bcmcmd_port(self, ports, cmd=""):

        ports_no = []

        with open(USONIC_TEMPLATE_DIR + "/interfaces.json") as f:
            interface_config = json.loads(f.read())

        if type(ports) == str:
            ports = [ports]

        for port in ports:
            if not port.startswith(PORT_PREFIX):
                raise Exception(f"invalid port name: {port}")

            port = port[len(PORT_PREFIX) :]
            elems = port.split("_")
            if len(elems) != 2:
                raise Exception(f"invalid port name: {port}")

            idx = int(elems[0])
            sub_idx = int(elems[1])

            port_no = int(interface_config[idx - 1]["port"])
            port_no += sub_idx - 1

            ports_no.append(str(port_no))

        ports_no = ",".join(ports_no)

        return self.run_bcmcmd(f"port {ports_no} {cmd}")

    def create_usonic_config_bcm(self, interface_map):
        with open(USONIC_TEMPLATE_DIR + "/interfaces.json") as f:
            master = json.loads(f.read())

        interfaces = []
        for i, m in enumerate(master):
            name = f"{PORT_PREFIX}{i+1}_1"
            channel = 1
            speed = m["speed"] // 1000

            v = interface_map.get(name, (None, None))
            if v[0] != None and v[1] != None:
                channel = v[0]
                speed = v[1] // 1000

            lane_num = m["lane_num"] // channel

            for ii in range(channel):
                interface = {}
                interface["port"] = m["port"] + ii * lane_num
                interface["lane"] = m["first_lane"] + ii * lane_num
                interface["speed"] = speed
                interfaces.append(interface)

        with open(USONIC_TEMPLATE_DIR + "/config.bcm.j2") as f:
            t = Template(f.read())
            return t.render(interfaces=interfaces)

    def create_usonic_vs_lanemap(self, interface_map):
        with open(USONIC_TEMPLATE_DIR + "/interfaces.json") as f:
            master = json.loads(f.read())

        interfaces = []
        for i, m in enumerate(master):
            name = f"{PORT_PREFIX}{i+1}_1"
            channel = 1
            speed = m["speed"]

            v = interface_map.get(name, (None, None))
            if v[0] != None and v[1] != None:
                channel = v[0]
                speed = v[1]

            lane_num = m["lane_num"] // channel

            for ii in range(channel):
                name = f"v{PORT_PREFIX}{i+1}_{ii+1}"
                interface = {"name": name}
                first_lane = m["first_lane"] + ii * lane_num
                interface["lanes"] = ",".join(
                    str(first_lane + idx) for idx in range(lane_num)
                )
                interface["alias"] = f"{m['alias_prefix']}-{m['index']+ii}"
                interface["speed"] = speed
                interface["index"] = m["index"] + ii * lane_num
                interfaces.append(interface)

        with open(USONIC_TEMPLATE_DIR + "/lanemap.ini.j2") as f:
            t = Template(f.read())
            return t.render(interfaces=interfaces)

    def create_usonic_port_config(self, interface_map):
        with open(USONIC_TEMPLATE_DIR + "/interfaces.json") as f:
            master = json.loads(f.read())

        interfaces = []
        for i, m in enumerate(master):
            name = f"{PORT_PREFIX}{i+1}_1"
            channel = 1
            speed = m["speed"]

            v = interface_map.get(name, (None, None))
            if v[0] != None and v[1] != None:
                channel = v[0]
                speed = v[1]

            lane_num = m["lane_num"] // channel

            for ii in range(channel):
                name = f"{PORT_PREFIX}{i+1}_{ii+1}"
                interface = {"name": name}
                first_lane = m["first_lane"] + ii * lane_num
                interface["lanes"] = ",".join(
                    str(first_lane + idx) for idx in range(lane_num)
                )
                interface["alias"] = f"{m['alias_prefix']}-{m['index']+ii}"
                interface["speed"] = speed
                interface["index"] = m["index"] + ii * lane_num
                interfaces.append(interface)

        with open(USONIC_TEMPLATE_DIR + "/port_config.ini.j2") as f:
            t = Template(f.read())
            return t.render(interfaces=interfaces)

    def update_usonic_config(self, interface_map):
        logger.debug(f"interface map: {interface_map}")

        # 1. create complete port_config.ini and config.bcm from the interface_map argument
        #    without using the existing config_map data
        #    Using string.Template (https://docs.python.org/3/library/string.html#template-strings) or Jinja2
        #    might make the code easier to read.
        config_bcm = self.create_usonic_config_bcm(interface_map)
        port_config = self.create_usonic_port_config(interface_map)

        logger.debug(f"port_config.ini file after creating:\n {port_config}")

        logger.debug(f"config.bcm file after creating :\n {config_bcm}")

        api = k.client.api.CoreV1Api()

        # 2. get the config_map using k8s API if it already exists
        config_map = api.read_namespaced_config_map(
            name=USONIC_CONFIGMAP,
            namespace=USONIC_NAMESPACE,
        )

        running_port_config = ""
        running_config_bcm = ""
        try:
            running_port_config = config_map.data["port_config.ini"]
        except:
            logger.error("port_config.ini is not present")
            return False

        try:
            running_config_bcm = config_map.data["config.bcm"]
        except:
            logger.error("config.bcm is not present")
            return False

        logger.debug(f"Running port_config.ini :\n {running_port_config}")

        logger.debug(f"Running config.bcm :\n {running_config_bcm}")

        # 3. if the generated port_config.ini / config.bcm is different from what exists in k8s API, update it
        if (running_port_config == port_config) and (running_config_bcm == config_bcm):
            logger.debug(f"No changes in port_config.ini and config.bcm")
            return False

        config_map.data["port_config.ini"] = port_config
        config_map.data["config.bcm"] = config_bcm

        if "lanemap.ini" in config_map.data:
            logger.debug("lanemap.ini found in config map. update it as well")
            v = self.create_usonic_vs_lanemap(interface_map)
            config_map.data["lanemap.ini"] = v

        api.patch_namespaced_config_map(
            name=USONIC_CONFIGMAP, namespace=USONIC_NAMESPACE, body=config_map
        )

        # 4. return True when we've updated the configmap, return False if not.
        logger.info(f"ConfigMap {USONIC_CONFIGMAP} updated")
        return True

    def restart_usonic(self):

        api = k.client.AppsV1Api()

        l = api.list_namespaced_deployment(
            namespace=USONIC_NAMESPACE, label_selector=USONIC_SELECTOR
        )

        for deployment in l.items:
            # Update annotation, to restart the deployment
            annotations = deployment.spec.template.metadata.annotations
            if annotations:
                annotations["kubectl.kubernetes.io/restartedAt"] = str(
                    datetime.datetime.now()
                )
            else:
                annotations = {
                    "kubectl.kubernetes.io/restartedAt": str(datetime.datetime.now())
                }
            deployment.spec.template.metadata.annotations = annotations

            # Update the deployment
            api.patch_namespaced_deployment(
                name=deployment.metadata.name,
                namespace=USONIC_NAMESPACE,
                body=deployment,
            )
            logger.info("Deployment updated")

    async def watch_pods(self):
        w = k_async.watch.Watch()
        api = k_async.client.CoreV1Api()
        async with w.stream(api.list_pod_for_all_namespaces) as stream:
            async for event in stream:
                name = event["object"].metadata.name
                phase = event["object"].status.phase

                if USONIC_CHECKPOINT not in name:
                    continue

                logger.debug(
                    "Event: %s %s %s %s"
                    % (
                        event["type"],
                        event["object"].kind,
                        name,
                        phase,
                    )
                )

                # Events sequence will be MODIFIED, DELETED, ADDED, MODIFIED
                # We will first wait for the deployment to be DELETED and then
                # will watch for the deployment to be Running
                if self.usonic_deleted == 1 and phase == "Running":
                    logger.debug("uSONiC reached running state, exiting")
                    self.usonic_core = self.get_podname("usonic-core")
                    self.update_bcm_portmap()
                    self.usonic_deleted = 0
                    return
                if self.usonic_deleted != 1 and event["type"] == "DELETED":
                    self.usonic_deleted = 1
