from goldstone.lib.core import ServerBase, ChangeHandler, NoOp
import libyang
import taish
import asyncio
import sysrepo
import logging
import json

logger = logging.getLogger(__name__)


class IfChangeHandler(ChangeHandler):
    async def _init(self, user):
        xpath = self.change.xpath

        xpath = list(libyang.xpath_split(xpath))
        assert xpath[0][0] == "goldstone-interfaces"
        assert xpath[0][1] == "interfaces"
        assert xpath[1][1] == "interface"
        assert xpath[1][2][0][0] == "name"
        self.xpath = xpath
        ifname = xpath[1][2][0][1]

        self.obj = await self.server.ifname2taiobj(ifname)
        if self.obj == None:
            raise sysrepo.SysrepoInvalArgError("Invalid Interface name")

        self.ifname = ifname

    async def validate(self, user):
        if not self.tai_attr_name:
            return
        try:
            cap = await self.obj.get_attribute_capability(self.tai_attr_name)
        except taish.TAIException as e:
            raise sysrepo.SysrepoInvalArgError(e.msg)

        logger.info(f"cap: {cap}")

        if self.type == "deleted":
            leaf = self.xpath[-1][1]
            d = self.server.get_default(leaf)
            if d:
                self.value = self.to_tai_value(d)
            elif cap.default_value == "":  # and is_deleted
                raise sysrepo.SysrepoInvalArgError(
                    f"no default value. cannot remove the configuration"
                )
            else:
                self.value = cap.default_value
        else:
            v = self.to_tai_value(self.change.value)
            if cap.min != "" and float(cap.min) > float(v):
                raise sysrepo.SysrepoInvalArgError(
                    f"minimum {k} value is {cap.min}. given {v}"
                )

            if cap.max != "" and float(cap.max) < float(v):
                raise sysrepo.SysrepoInvalArgError(
                    f"maximum {k} value is {cap.max}. given {v}"
                )

            valids = cap.supportedvalues
            if len(valids) > 0 and v not in valids:
                raise sysrepo.SysrepoInvalArgError(
                    f"supported values are {valids}. given {v}"
                )

            self.value = v

    async def apply(self, user):
        if not self.tai_attr_name:
            return
        self.original_value = await self.obj.get(self.tai_attr_name)
        await self.obj.set(self.tai_attr_name, self.value)

    async def revert(self, user):
        logger.warning(
            f"reverting: {self.tai_attr_name} {self.value} => {self.original_value}"
        )
        await self.obj.set(self.tai_attr_name, self.original_value)


class AdminStatusHandler(IfChangeHandler):
    async def _init(self, user):
        await super()._init(user)
        self.tai_attr_name = "tx-dis"

    def to_tai_value(self, v):
        return "false" if v == "UP" else "true"


class FECHandler(IfChangeHandler):
    async def _init(self, user):
        await super()._init(user)
        self.tai_attr_name = "fec-type"

    def to_tai_value(self, v):
        return v.lower()


class MTUHandler(IfChangeHandler):
    async def _init(self, user):
        await super()._init(user)
        self.tai_attr_name = "mtu"

    def to_tai_value(self, v):
        return v


def pcs_status2oper_status(pcs):
    status = "DOWN"
    if (
        "ready" in pcs
        and ("rx-remote-fault" not in pcs)
        and ("rx-local-fault" not in pcs)
    ):
        status = "UP"
    return status


class InterfaceServer(ServerBase):
    def __init__(self, conn, taish_server, platform_info):
        super().__init__(conn, "goldstone-interfaces")
        info = {}
        for i in platform_info:
            if "interface" in i:
                ifname = f"Ethernet{i['interface']['suffix']}"
                info[ifname] = i
        self.platform_info = info
        self.conn = conn
        self.taish = taish.AsyncClient(*taish_server.split(":"))
        self.notif_q = asyncio.Queue()
        self.is_initializing = True
        self.handlers = {
            "interfaces": {
                "interface": {
                    "name": NoOp,
                    "config": {
                        "admin-status": AdminStatusHandler,
                        "name": NoOp,
                        "description": NoOp,
                    },
                    "ethernet": {
                        "config": {
                            "fec": FECHandler,
                            "mtu": MTUHandler,
                        },
                        "auto-negotiate": {
                            "config": {
                                "enabled": NoOp,
                            }
                        },
                    },
                }
            }
        }

    def get_default(self, key, model="goldstone-interfaces"):
        ctx = self.sess.get_ly_ctx()
        if model == "goldstone-interfaces":
            keys = [
                ["interfaces", "interface", "config", key],
                ["interfaces", "interface", "ethernet", "config", key],
                [
                    "interfaces",
                    "interface",
                    "ethernet",
                    "auto-negotiate",
                    "config",
                    key,
                ],
            ]
        elif model == "goldstone-gearbox":
            keys = [["gearboxes", "gearbox", "config", key]]
        else:
            return None

        for k in keys:
            xpath = "".join(f"/{model}:{v}" for v in k)
            try:
                for node in ctx.find_path(xpath):
                    if node.type().name() == "boolean":
                        return node.default() == "true"
                    return node.default()
            except libyang.util.LibyangError:
                pass

        raise None

    async def reconcile(self):
        prefix = "/goldstone-interfaces:interfaces/interface"
        for ifname in await self.get_ifname_list():
            xpath = f"{prefix}[name='{ifname}']"
            config = self.get_running_data(
                xpath, default={}, include_implicit_defaults=True
            )
            admin_status = config.get("config", {}).get("admin-status")
            if admin_status == None:
                admin_status = self.get_default("admin-status")
            value = "false" if admin_status == "UP" else "true"

            obj = await self.ifname2taiobj(ifname)
            await obj.set("tx-dis", value)

            fec = config.get("ethernet", {}).get("config", {}).get("fec")
            if fec == None:
                fec = self.get_default("fec")
            await obj.set("fec-type", fec.lower())

            mtu = config.get("ethernet", {}).get("config", {}).get("mtu")
            if mtu == None:
                mtu = int(self.get_default("mtu"))
            await obj.set("mtu", mtu)

    async def tai_cb(self, obj, attr_meta, msg):
        if isinstance(obj, taish.NetIf):
            type_ = 1
        elif isinstance(obj, taish.HostIf):
            type_ = 0
        else:
            logger.error(f"invalid object: {obj}")
            return

        m_oid = obj.obj.module_oid
        modules = await self.taish.list()

        for location, m in modules.items():
            if m.oid == m_oid:
                loc = location
                break
        else:
            logger.error(f"module not found: {m_oid}")
            return

        index = int(await obj.get("index"))
        ifname = f"Ethernet{loc}/{type_}/{index+1}"

        await self.notif_q.put({"ifname": ifname, "msg": msg, "obj": obj})

    async def notification_tasks(self):
        async def task(obj, attr):
            try:
                await obj.monitor(attr, self.tai_cb, json=True)
            except asyncio.exceptions.CancelledError as e:
                while True:
                    await asyncio.sleep(0.1)
                    v = await obj.get(attr)
                    logger.debug(v)
                    if "(nil)" in v:
                        return
                raise e

        return [
            task(await self.ifname2taiobj(ifname), "alarm-notification")
            for ifname in await self.get_ifname_list()
        ]

    async def start(self):
        async def ping():
            while True:
                await asyncio.sleep(5)
                try:
                    await asyncio.wait_for(self.taish.list(), timeout=2)
                except Exception as e:
                    logger.error(f"ping failed {e}")
                    return

        async def handle_notification(notification):
            logger.info(notification)
            ifname = notification["ifname"]
            msg = notification["msg"]
            obj = notification["obj"]
            eventname = "goldstone-interfaces:interface-link-state-notify-event"

            for attr in msg.attrs:
                meta = await obj.get_attribute_metadata(attr.attr_id)
                if meta.short_name != "pcs-status":
                    continue
                status = pcs_status2oper_status(json.loads(attr.value))
                notif = {"if-name": ifname, "oper-status": status}
                self.send_notification(eventname, notif)

        async def notif_loop():
            while True:
                notification = await self.notif_q.get()
                await handle_notification(notification)
                self.notif_q.task_done()

        tasks = await super().start()
        self.is_initializing = False

        return tasks + [ping(), notif_loop()] + await self.notification_tasks()

    async def stop(self):
        logger.info(f"stop server")
        self.taish.close()
        super().stop()

    def pre(self, user):
        if self.is_initializing:
            raise sysrepo.SysrepoLockedError("initializing")

    async def get_ifname_list(self):
        modules = await self.taish.list()

        interfaces = []
        for loc, module in modules.items():
            m = await self.taish.get_module(loc)
            for hostif in m.obj.hostifs:
                interfaces.append(f"Ethernet{loc}/0/{hostif.index+1}")
            for netif in m.obj.netifs:
                interfaces.append(f"Ethernet{loc}/1/{netif.index+1}")

        return interfaces

    async def ifname2taiobj(self, ifname):
        v = [int(v) for v in ifname.replace("Ethernet", "").split("/")]
        m = await self.taish.get_module(str(v[0]))
        if v[1] == 0:  # hostif
            return m.get_hostif(v[2] - 1)
        elif v[1] == 1:  # netif
            return m.get_netif(v[2] - 1)
        return None

    async def oper_cb(self, sess, xpath, req_xpath, parent, priv):
        logger.debug(f"xpath: {xpath}, req_xpath: {req_xpath}")
        xpath = list(libyang.xpath_split(req_xpath))
        logger.debug(f"xpath: {xpath}")

        if len(xpath) < 2 or len(xpath[1][2]) < 1:
            ifnames = await self.get_ifname_list()
        else:
            if xpath[1][2][0][0] != "name":
                logger.warn(f"invalid request: {xpath}")
                return
            ifnames = [xpath[1][2][0][1]]

        interfaces = []
        for ifname in ifnames:
            i = {"name": ifname, "config": {"name": ifname}}
            if len(xpath) == 3 and xpath[2][1] == "name":
                interfaces.append(i)
                continue

            p = self.platform_info.get(ifname)
            if p:
                v = {}
                if "component" in p:
                    v["platform"] = {"component": p["component"]["name"]}
                if "tai" in p:
                    t = {
                        "module": p["tai"]["module"]["name"],
                        "host-interface": p["tai"]["hostif"]["name"],
                    }
                    v["transponder"] = t
                i["component-connection"] = v

            obj = await self.ifname2taiobj(ifname)
            state = {}
            try:
                state["admin-status"] = (
                    "DOWN" if await obj.get("tx-dis") == "true" else "UP"
                )
            except taish.TAIException:
                pass

            i["state"] = state

            state = {}
            try:
                state["fec"] = (await obj.get("fec-type")).upper()
            except taish.TAIException:
                pass

            try:
                state["mtu"] = int(await obj.get("mtu"))
            except taish.TAIException:
                pass

            try:
                state["speed"] = (
                    "SPEED_100G"
                    if await obj.get("signal-rate") == "100-gbe"
                    else "SPEED_UNKNOWN"
                )
            except taish.TAIException:
                pass

            i["ethernet"] = {"state": state}

            try:
                pcs = json.loads(await obj.get("pcs-status", json=True))
                serdes = json.loads(await obj.get("serdes-status", json=True))
                i["state"]["oper-status"] = pcs_status2oper_status(pcs)
                state = {"pcs-status": pcs, "serdes-status": serdes}
                i["ethernet"]["pcs"] = {"state": state}
            except taish.TAIException:
                pass

            interfaces.append(i)

        return {"goldstone-interfaces:interfaces": {"interface": interfaces}}
