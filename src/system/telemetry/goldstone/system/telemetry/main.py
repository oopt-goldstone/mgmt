"""main() function for the streaming telemetry server."""


import logging
import asyncio
import argparse
import signal
import itertools
from goldstone.lib.util import start_probe, call
from goldstone.lib.connector.sysrepo import Connector
from .store import InMemorySubscriptionStore, InMemoryTelemetryStore
from .telemetry import TelemetryServer


logger = logging.getLogger(__name__)


def main():
    async def _main():
        loop = asyncio.get_event_loop()
        stop_event = asyncio.Event()
        loop.add_signal_handler(signal.SIGINT, stop_event.set)
        loop.add_signal_handler(signal.SIGTERM, stop_event.set)

        conn = Connector()
        subscription_store = InMemorySubscriptionStore()
        telemetry_store = InMemoryTelemetryStore()
        gsserver = TelemetryServer(conn, subscription_store, telemetry_store)
        servers = [gsserver]

        try:
            tasks = list(
                itertools.chain.from_iterable([await s.start() for s in servers])
            )

            runner = await start_probe("/healthz", "0.0.0.0", 8080)
            tasks.append(stop_event.wait())
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            logger.debug("done: %s, pending: %s", done, pending)
            for task in done:
                e = task.exception()
                if e:
                    raise e
        finally:
            if runner:
                await runner.cleanup()
            for s in servers:
                await call(s.stop)
            conn.stop()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable detailed output"
    )
    args = parser.parse_args()

    fmt = "%(levelname)s %(module)s %(funcName)s l.%(lineno)d | %(message)s"
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=fmt)
        for noisy in [
            "hpack",
            "kubernetes.client.rest",
            "kubernetes_asyncio.client.rest",
        ]:
            l = logging.getLogger(noisy)
            l.setLevel(logging.INFO)
    else:
        logging.basicConfig(level=logging.INFO, format=fmt)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
