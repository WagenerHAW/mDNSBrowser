"""Interface selection and automatic discovery restart regressions (Qt offscreen)."""

from dataclasses import replace
import socket
import threading
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from src.app import AsyncZCWorker, MDNSBrowser
from src.network_interfaces import InterfaceAddress, NetworkInterface, NetworkInterfaceMonitor


def interface(name, ip, is_up=True):
    return NetworkInterface(
        name, is_up, 1500, ('multicast', 'up'),
        (InterfaceAddress(socket.AF_INET, ip, '255.255.255.0', '', ''),),
    )


class InterfaceUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.wifi = interface('wifi', '192.0.2.1')
        self.ethernet = interface('ethernet', '198.51.100.1')
        for patcher in (
            patch('src.app.read_network_interfaces', return_value=(self.wifi, self.ethernet)),
            patch.object(AsyncZCWorker, 'start'),
            patch.object(AsyncZCWorker, 'stop'),
            patch.object(NetworkInterfaceMonitor, 'start'),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.window = MDNSBrowser()
        self.addCleanup(self.window.close)

    def test_change_refreshes_list_clears_both_protocols_and_restarts_once(self):
        for protocol in ('mDNS', 'UPnP'):
            self.window._on_service_type_found(protocol, '_http._tcp.local.' if protocol == 'mDNS' else 'upnp:rootdevice')
            self.window._on_service_added(protocol, {'name': protocol, 'protocol': protocol})
        worker = self.window.worker
        monitor = self.window.interface_monitor
        added = interface('usb', '203.0.113.1')

        with patch.object(AsyncZCWorker, 'start') as start, patch.object(worker, 'stop') as stop:
            self.window._on_network_interfaces_changed((self.wifi, added))

        start.assert_called_once()
        stop.assert_called_once()
        self.assertIsNot(self.window.worker, worker)
        self.assertIs(self.window.interface_monitor, monitor)
        self.assertIsNone(self.window.worker.interface)
        self.assertEqual([self.window.interface_selector.itemText(i) for i in range(3)], [
            'All interfaces', 'wifi (192.0.2.1)', 'usb (203.0.113.1)',
        ])
        for protocol in ('mDNS', 'UPnP'):
            self.assertEqual(self.window.services[protocol], {})
            self.assertEqual(self.window.service_types[protocol], set())
            self.assertEqual(self.window.list_widgets[protocol].count(), 0)

    def test_selected_adapter_is_preserved_when_another_changes(self):
        self.window.interface_selector.setCurrentIndex(2)
        self.window._on_network_interfaces_changed((interface('usb', '203.0.113.1'), self.ethernet))
        self.assertEqual(self.window.interface_selector.currentText(), 'ethernet (198.51.100.1)')
        self.assertEqual(self.window.worker.interface, '198.51.100.1')

    def test_selected_adapter_follows_dhcp_address_change(self):
        self.window.interface_selector.setCurrentIndex(1)
        updated = interface('wifi', '192.0.2.2')
        with patch.object(AsyncZCWorker, 'start') as start:
            self.window._on_network_interfaces_changed((updated, self.ethernet))
        start.assert_called_once()
        self.assertEqual(self.window.interface_selector.currentText(), 'wifi (192.0.2.2)')
        self.assertEqual(self.window.worker.interface, '192.0.2.2')

    def test_adapter_identity_and_exact_address_survive_duplicate_ips_and_aliases(self):
        self.window.interface_selector.setCurrentIndex(1)
        alias = InterfaceAddress(socket.AF_INET, '192.0.2.2', '255.255.255.0', '', '')
        updated = replace(self.wifi, addresses=(alias, *self.wifi.addresses))
        self.window._on_network_interfaces_changed((interface('ethernet', '192.0.2.1'), updated))
        self.assertEqual(self.window.interface_selector.currentText(), 'wifi (192.0.2.1)')

    def test_down_or_removed_selection_falls_back_and_reconnection_updates_list(self):
        for snapshot in ((replace(self.wifi, is_up=False), self.ethernet), (self.ethernet,)):
            with self.subTest(snapshot=snapshot):
                self.window._on_network_interfaces_changed((self.wifi, self.ethernet))
                self.window.interface_selector.setCurrentIndex(1)
                self.window._on_network_interfaces_changed(snapshot)
                self.assertEqual(self.window.interface_selector.currentText(), 'All interfaces')
                self.assertEqual(self.window.interface_selector.count(), 2)
                self.assertIsNone(self.window.worker.interface)
        self.window._on_network_interfaces_changed(())
        self.assertEqual(self.window.interface_selector.count(), 1)
        self.window._on_network_interfaces_changed((self.wifi,))
        self.assertEqual(self.window.interface_selector.count(), 2)
        self.assertIsNone(self.window.worker.interface)

    def test_retired_worker_cannot_add_or_remove_results_or_show_errors(self):
        old_signals = self.window.signals
        old_signals.service_type_found.emit('mDNS', '_stale._tcp.local.')
        old_signals.service_added.emit('stale', {'name': 'stale', 'protocol': 'mDNS'})
        old_signals.error.emit('Disconnected network')
        # Restart before Qt delivers the old worker's queued signals.
        with patch.object(self.window, '_on_error') as error:
            self.window._on_network_interfaces_changed((self.wifi,))
            self.window.signals.service_type_found.emit('mDNS', '_fresh._tcp.local.')
            self.window.signals.service_added.emit('fresh', {'name': 'fresh', 'protocol': 'mDNS'})
            self.app.processEvents()
            old_signals.service_removed.emit('fresh')
            self.app.processEvents()
        error.assert_not_called()
        self.assertEqual(set(self.window.services['mDNS']), {'fresh'})
        self.assertEqual(self.window.service_types['mDNS'], {'_fresh._tcp.local.'})

    def test_background_monitor_delivers_changes_on_ui_thread(self):
        ui_thread = threading.get_ident()
        rescan_threads = []
        monitor = self.window.interface_monitor
        updated = (self.wifi,)
        with patch('src.network_interfaces.read_network_interfaces', return_value=updated), \
                patch.object(self.window, 'rescan_network', side_effect=lambda: rescan_threads.append(threading.get_ident())):
            thread = threading.Thread(target=monitor._check_for_changes)
            thread.start()
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.assertEqual(rescan_threads, [])
            self.app.processEvents()
        self.assertEqual(rescan_threads, [ui_thread])
        self.assertEqual(self.window.interface_selector.count(), 2)

    def test_shutdown_stops_monitor_and_ignores_pending_change(self):
        self.window.network_signals.changed.emit(())
        with patch.object(self.window.interface_monitor, 'stop') as stop, \
                patch.object(self.window, 'rescan_network') as rescan:
            self.window.close()
            self.app.processEvents()
        stop.assert_called_once()
        rescan.assert_not_called()


if __name__ == '__main__':
    unittest.main()
