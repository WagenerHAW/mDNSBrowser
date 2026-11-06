"""Poll network configuration independently of the discovery worker."""

from dataclasses import dataclass
import logging
import threading
from typing import Callable

import psutil


logger = logging.getLogger(__name__)


@dataclass(frozen=True, order=True)
class InterfaceAddress:
    family: int
    address: str
    netmask: str
    broadcast: str
    ptp: str


@dataclass(frozen=True)
class NetworkInterface:
    name: str
    is_up: bool
    mtu: int
    flags: tuple[str, ...]
    addresses: tuple[InterfaceAddress, ...]


def read_network_interfaces() -> tuple[NetworkInterface, ...]:
    """Take an order-independent snapshot, including adapters without addresses."""
    stats = psutil.net_if_stats()
    addresses = psutil.net_if_addrs()
    interfaces = []
    for name in sorted(stats.keys() | addresses.keys()):
        status = stats.get(name)
        flags = getattr(status, 'flags', '')
        interfaces.append(NetworkInterface(
            name=name,
            is_up=bool(status and status.isup),
            mtu=getattr(status, 'mtu', 0),
            flags=tuple(sorted(flag.strip().lower() for flag in flags.split(',') if flag.strip())),
            addresses=tuple(sorted({
                InterfaceAddress(
                    int(address.family), address.address,
                    address.netmask or '', address.broadcast or '', address.ptp or '',
                )
                for address in addresses.get(name, ())
            })),
        ))
    return tuple(interfaces)


class NetworkInterfaceMonitor(threading.Thread):
    """Notify on configuration changes; transient read failures retain the last state."""

    def __init__(
        self,
        snapshot: tuple[NetworkInterface, ...],
        on_change: Callable[[tuple[NetworkInterface, ...]], None],
        interval: float = 2.0,
    ):
        super().__init__(name='network-interface-monitor', daemon=True)
        self._snapshot = snapshot
        self._on_change = on_change
        self._interval = interval
        self._stop_event = threading.Event()
        self._read_failed = False

    def run(self):
        while not self._stop_event.wait(self._interval):
            self._check_for_changes()

    def _check_for_changes(self):
        try:
            snapshot = read_network_interfaces()
        except Exception:
            if not self._read_failed:
                logger.warning('Could not read network interfaces; will retry', exc_info=True)
            self._read_failed = True
            return
        self._read_failed = False
        if not self._stop_event.is_set() and snapshot != self._snapshot:
            self._snapshot = snapshot
            self._on_change(snapshot)

    def stop(self):
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=5)
