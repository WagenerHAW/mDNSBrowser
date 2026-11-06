"""Network configuration monitoring tests without network or Qt dependencies."""

from dataclasses import replace
import socket
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.network_interfaces import NetworkInterfaceMonitor, read_network_interfaces


def address(ip='192.0.2.1', family=socket.AF_INET, netmask='255.255.255.0'):
    return SimpleNamespace(family=family, address=ip, netmask=netmask, broadcast=None, ptp=None)


def status(is_up=True, mtu=1500, flags='up,multicast'):
    return SimpleNamespace(isup=is_up, mtu=mtu, flags=flags)


class NetworkInterfaceTests(unittest.TestCase):
    def snapshot(self, addresses=None, stats=None):
        with patch('src.network_interfaces.psutil.net_if_addrs', return_value=addresses or {}), \
                patch('src.network_interfaces.psutil.net_if_stats', return_value=stats or {}):
            return read_network_interfaces()

    def test_enumeration_order_does_not_trigger_a_change(self):
        ipv4, ipv6 = address(), address('fe80::1', socket.AF_INET6, 'ffff:ffff:ffff:ffff::')
        first = self.snapshot(
            {'wifi': [ipv4, ipv6], 'ethernet': [address('198.51.100.1')]},
            {'wifi': status(), 'ethernet': status()},
        )
        second = self.snapshot(
            {'ethernet': [address('198.51.100.1')], 'wifi': [ipv6, ipv4]},
            {'ethernet': status(), 'wifi': status(flags='multicast,up')},
        )
        changed = Mock()
        monitor = NetworkInterfaceMonitor(first, changed)
        with patch('src.network_interfaces.read_network_interfaces', return_value=second):
            monitor._check_for_changes()
        changed.assert_not_called()

    def test_detects_adapter_link_and_address_changes_once_each(self):
        initial = self.snapshot({'wifi': [address()]}, {'wifi': status()})
        scenarios = {
            'adapter added': self.snapshot(
                {'wifi': [address()], 'ethernet': [address('198.51.100.1')]},
                {'wifi': status(), 'ethernet': status()},
            ),
            'adapter removed': (),
            'link down with address retained': self.snapshot({'wifi': [address()]}, {'wifi': status(False)}),
            'IPv4 address changed': self.snapshot({'wifi': [address('192.0.2.2')]}, {'wifi': status()}),
            'subnet changed': self.snapshot({'wifi': [address(netmask='255.255.0.0')]}, {'wifi': status()}),
            'IPv6 address added': self.snapshot(
                {'wifi': [address(), address('fe80::1', socket.AF_INET6)]}, {'wifi': status()},
            ),
            'MTU changed': self.snapshot({'wifi': [address()]}, {'wifi': status(mtu=9000)}),
            'multicast capability changed': self.snapshot({'wifi': [address()]}, {'wifi': status(flags='up')}),
            'unaddressed adapter added': self.snapshot(
                {'wifi': [address()]}, {'wifi': status(), 'ethernet': status(False)},
            ),
        }
        for description, snapshot in scenarios.items():
            with self.subTest(description=description):
                changed = Mock()
                monitor = NetworkInterfaceMonitor(initial, changed)
                with patch('src.network_interfaces.read_network_interfaces', return_value=snapshot):
                    monitor._check_for_changes()
                    monitor._check_for_changes()
                changed.assert_called_once_with(snapshot)

        # Reconnecting is another change even when the previous IP is reused.
        changed = Mock()
        monitor = NetworkInterfaceMonitor(scenarios['link down with address retained'], changed)
        with patch('src.network_interfaces.read_network_interfaces', return_value=initial):
            monitor._check_for_changes()
        changed.assert_called_once_with(initial)

    def test_read_failure_keeps_last_state_and_monitor_recovers(self):
        initial = self.snapshot({'wifi': [address()]}, {'wifi': status()})
        updated = (replace(initial[0], is_up=False),)
        changed = Mock()
        monitor = NetworkInterfaceMonitor(initial, changed)
        with patch('src.network_interfaces.read_network_interfaces', side_effect=[
            OSError('Temporary failure'), OSError('Temporary failure'), initial, updated,
        ]), self.assertLogs('src.network_interfaces', level='WARNING') as logs:
            monitor._check_for_changes()
            monitor._check_for_changes()
            monitor._check_for_changes()
            changed.assert_not_called()
            monitor._check_for_changes()
        self.assertEqual(len(logs.output), 1)
        changed.assert_called_once_with(updated)

    def test_polling_runs_in_background_and_stop_prevents_further_updates(self):
        updated = self.snapshot({'wifi': [address()]}, {'wifi': status()})
        notified = threading.Event()
        callback_threads = []

        def changed(snapshot):
            callback_threads.append(threading.get_ident())
            notified.set()

        monitor = NetworkInterfaceMonitor((), changed, interval=0.01)
        with patch('src.network_interfaces.read_network_interfaces', return_value=updated):
            monitor.start()
            try:
                self.assertTrue(notified.wait(timeout=1))
            finally:
                monitor.stop()
        self.assertFalse(monitor.is_alive())
        self.assertEqual(callback_threads, [monitor.ident])
        self.assertNotEqual(monitor.ident, threading.get_ident())
        with patch('src.network_interfaces.read_network_interfaces', return_value=()):
            monitor._check_for_changes()
        self.assertEqual(len(callback_threads), 1)

    def test_stop_wakes_polling_wait(self):
        monitor = NetworkInterfaceMonitor((), Mock(), interval=60)
        monitor.start()
        monitor.stop()
        self.assertFalse(monitor.is_alive())


if __name__ == '__main__':
    unittest.main()
