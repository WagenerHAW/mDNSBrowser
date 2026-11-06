"""UPnP discovery regressions; no network or running desktop is required.

Run with QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s test.
"""

import asyncio
import socket
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QScrollArea
from async_upnp_client.const import HttpResponse

from src.app import AsyncZCSignals, AsyncZCWorker, MDNSBrowser
from src.upnp import parse_upnp_description, upnp_device_summary, upnp_device_title, upnp_type_label


DEVICE_TYPE = 'urn:schemas-upnp-org:device:InternetGatewayDevice:1'
SERVICE_TYPE = 'urn:schemas-upnp-org:service:WANIPConnection:1'
LOCATION = 'http://192.0.2.1:49000/device.xml'
DESCRIPTION = f'''<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <deviceType>{DEVICE_TYPE}</deviceType>
    <friendlyName> Office Router </friendlyName>
    <manufacturer>Example</manufacturer>
    <modelName>Gateway</modelName>
    <modelNumber>2000</modelNumber>
    <UDN>uuid:router</UDN>
    <deviceList><device>
      <deviceType>urn:schemas-upnp-org:device:WLANAccessPointDevice:1</deviceType>
      <friendlyName>Office WiFi</friendlyName>
      <UDN>uuid:wifi</UDN>
    </device></deviceList>
  </device>
</root>'''


def headers(target='upnp:rootdevice', udn='uuid:router'):
    return {'LOCATION': LOCATION, 'USN': f'{udn}::{target}', 'ST': target, 'SERVER': 'Test/1.0 UPnP/1.1'}


class DescriptionTests(unittest.TestCase):
    def test_embedded_devices_keep_their_own_names(self):
        root, wifi = parse_upnp_description(DESCRIPTION)
        self.assertEqual(root['friendly_name'], 'Office Router')
        self.assertEqual(wifi['friendly_name'], 'Office WiFi')
        self.assertEqual(wifi['udn'], 'uuid:wifi')
        self.assertNotIn('manufacturer', wifi)

    def test_description_without_namespace(self):
        xml = '<root><device><friendlyName>Speaker &amp; TV</friendlyName></device></root>'
        self.assertEqual(parse_upnp_description(xml), [{'friendly_name': 'Speaker & TV'}])

    def test_unreadable_descriptions_are_rejected(self):
        for xml in ('<broken', '<!DOCTYPE root [<!ENTITY x "expansion">]><root>&x;</root>'):
            with self.subTest(xml=xml), self.assertRaises(Exception):
                parse_upnp_description(xml)

    def test_labels_and_address_fallback(self):
        self.assertEqual(upnp_type_label(DEVICE_TYPE), 'Internet Gateway (v1)')
        self.assertEqual(upnp_type_label('upnp:rootdevice'), 'Root devices')
        self.assertIn('service, v1', upnp_type_label(SERVICE_TYPE))
        info = {'host': '192.0.2.1:49000', 'types': [SERVICE_TYPE, DEVICE_TYPE]}
        self.assertEqual(upnp_device_title(info), '192.0.2.1:49000')
        self.assertEqual(upnp_device_summary(info), 'Internet Gateway (v1) · 192.0.2.1:49000')


class ResolutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.signals = AsyncZCSignals()
        self.worker = AsyncZCWorker(self.signals)
        self.request = AsyncMock(return_value=HttpResponse(200, {}, DESCRIPTION))
        self.worker._upnp_requester = unittest.mock.Mock(async_http_request=self.request)
        self.added = []
        self.signals.service_added.connect(lambda name, info: self.added.append(info))

    async def asyncTearDown(self):
        await self.worker._cancel_upnp_resolution()

    async def test_advertisements_share_description_request_and_cached_result(self):
        await asyncio.gather(
            self.worker._on_upnp_device(headers()),
            self.worker._on_upnp_device(headers(SERVICE_TYPE)),
            self.worker._on_upnp_device(headers(DEVICE_TYPE, 'UUID:WIFI')),
        )
        await self.worker._on_upnp_device(headers(DEVICE_TYPE))
        self.request.assert_awaited_once()
        enriched = [info for info in self.added if info.get('friendly_name')]
        self.assertEqual({info['friendly_name'] for info in enriched}, {'Office Router', 'Office WiFi'})
        self.assertEqual({info['name'] for info in enriched}, {'uuid:router', 'uuid:wifi'})
        # The first signal's fallback data must not be mutated after emission.
        self.assertNotIn('friendly_name', self.added[0])

    async def test_unknown_udn_does_not_take_the_root_name(self):
        await self.worker._on_upnp_device(headers(udn='uuid:unknown'))
        self.assertEqual(len(self.added), 1)
        self.assertNotIn('friendly_name', self.added[0])

    async def test_failures_keep_device_visible_and_can_be_retried(self):
        for response in (TimeoutError(), HttpResponse(404, {}, ''), HttpResponse(200, {}, '<broken')):
            with self.subTest(response=response):
                self.worker._upnp_descriptions.clear()
                self.added.clear()
                self.request.side_effect = response if isinstance(response, Exception) else None
                self.request.return_value = response
                await self.worker._on_upnp_device(headers())
                self.assertEqual(len(self.added), 1)
                self.assertEqual(upnp_device_title(self.added[0]), '192.0.2.1:49000')
        self.request.side_effect = None
        self.request.return_value = HttpResponse(200, {}, DESCRIPTION)
        self.worker._upnp_descriptions[LOCATION] = (0, [])
        await self.worker._on_upnp_device(headers())
        self.assertEqual(self.added[-1]['friendly_name'], 'Office Router')

    async def test_missing_location_and_stopped_worker_do_not_fetch(self):
        await self.worker._on_upnp_device({'USN': 'uuid:router', '_remote_addr': ('192.0.2.2', 1900)})
        self.assertEqual(upnp_device_title(self.added[0]), '192.0.2.2')
        self.worker._stop_event.set()
        await self.worker._on_upnp_device(headers())
        self.request.assert_not_awaited()
        self.assertEqual(len(self.added), 1)

    async def test_stop_cancels_inflight_resolution_without_late_updates(self):
        started = asyncio.Event()

        async def slow_response(_request):
            started.set()
            await asyncio.Event().wait()

        self.request.side_effect = slow_response
        callback = asyncio.create_task(self.worker._on_upnp_device(headers()))
        await asyncio.wait_for(started.wait(), timeout=1)
        self.worker._stop_event.set()
        await self.worker._cancel_upnp_resolution()
        self.assertTrue(callback.cancelled())
        self.assertEqual(len(self.added), 1)
        self.assertFalse(self.worker._upnp_description_tasks)


class InterfaceSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.signals = AsyncZCSignals()
        self.worker = AsyncZCWorker(self.signals)

    def test_all_interfaces_selects_active_ipv4_multicast_addresses(self):
        def address(ip, family=socket.AF_INET):
            return SimpleNamespace(family=family, address=ip)

        def status(isup=True, flags='up,multicast'):
            return SimpleNamespace(isup=isup, flags=flags)

        addresses = {
            'wifi': [address('192.0.2.1'), address('fe80::1', socket.AF_INET6)],
            'ethernet': [address('198.51.100.1')],
            'duplicate': [address('192.0.2.1')],
            'linklocal': [address('169.254.1.2')],
            'loopback': [address('127.0.0.1')],
            'down': [address('203.0.113.1')],
            'unicast_only': [address('203.0.113.2')],
            'unassigned': [address('0.0.0.0')],
            'missing': [address('203.0.113.3')],
            # Some platforms do not expose interface flags.
            'no_flags': [address('203.0.113.4')],
        }
        stats = {name: status() for name in addresses if name != 'missing'}
        stats['down'] = status(False)
        stats['unicast_only'] = status(flags='up,broadcast')
        stats['no_flags'] = SimpleNamespace(isup=True)
        with patch('src.app.psutil.net_if_addrs', return_value=addresses), \
                patch('src.app.psutil.net_if_stats', return_value=stats):
            self.assertEqual(set(self.worker._upnp_search_sources()), {
                ('192.0.2.1', 0), ('198.51.100.1', 0), ('169.254.1.2', 0), ('203.0.113.4', 0),
            })

    def test_explicit_interface_only_searches_that_address(self):
        self.worker.interface = '192.0.2.1'
        with patch('src.app.psutil.net_if_addrs') as enumerate_addresses:
            self.assertEqual(self.worker._upnp_search_sources(), [('192.0.2.1', 0)])
        enumerate_addresses.assert_not_called()

    async def test_all_interfaces_run_in_parallel_and_isolate_failures(self):
        sources = [('192.0.2.1', 0), ('198.51.100.1', 0)]
        started = set()
        all_started = asyncio.Event()
        errors = []
        self.signals.error.connect(errors.append)

        async def search(source):
            started.add(source)
            if len(started) == len(sources):
                all_started.set()
            await all_started.wait()
            self.worker._stop_event.set()
            if source == sources[0]:
                raise OSError('Disconnected adapter')

        with patch.object(self.worker, '_upnp_search_sources', return_value=sources), \
                patch.object(self.worker, '_search_upnp_interface', side_effect=search), \
                self.assertLogs('src.app', level='WARNING'):
            await asyncio.wait_for(self.worker._upnp_search_loop(), timeout=1)
        self.assertEqual(started, set(sources))
        self.assertEqual(errors, [])

    async def test_cancelled_search_closes_listener(self):
        started = asyncio.Event()
        with patch('src.app.SsdpSearchListener') as create_listener:
            listener = create_listener.return_value
            listener.async_start = AsyncMock()
            listener.async_search.side_effect = started.set
            search = asyncio.create_task(self.worker._search_upnp_interface(('192.0.2.1', 0)))
            await asyncio.wait_for(started.wait(), timeout=1)
            search.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await search
            listener.async_stop.assert_called_once()
            self.assertEqual(create_listener.call_args.kwargs['source'], ('192.0.2.1', 0))

    async def test_listener_is_closed_when_start_fails(self):
        with patch('src.app.SsdpSearchListener') as create_listener:
            listener = create_listener.return_value
            listener.async_start = AsyncMock(side_effect=OSError('No route to host'))
            with self.assertRaises(OSError):
                await self.worker._search_upnp_interface(('192.0.2.1', 0))
            listener.async_stop.assert_called_once()

    def test_interface_restart_finishes_and_closes_zeroconf_once(self):
        started = threading.Event()
        with patch('src.app.AsyncZeroconf') as create_zeroconf, \
                patch('src.app.AsyncServiceBrowser') as create_browser, \
                patch.object(self.worker, '_upnp_search_loop', new_callable=AsyncMock):
            zeroconf = create_zeroconf.return_value
            zeroconf.async_close = AsyncMock()
            browser = create_browser.return_value
            browser.async_cancel = AsyncMock()
            create_browser.side_effect = lambda *args, **kwargs: (started.set(), browser)[1]
            self.worker.start()
            try:
                self.assertTrue(started.wait(timeout=1))
            finally:
                self.worker.stop()
            self.assertFalse(self.worker.thread.is_alive())
            zeroconf.async_close.assert_awaited_once()
            browser.async_cancel.assert_awaited_once()


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch.object(AsyncZCWorker, 'start'), patch('src.app.NetworkInterfaceMonitor.start'):
            self.window = MDNSBrowser()
        self.list = self.window.list_widgets['UPnP']
        self.types = self.window.type_widgets['UPnP']

    def tearDown(self):
        self.window.close()

    def add_device(self, target='upnp:rootdevice', udn='uuid:router', metadata=None):
        info = self.window.worker._serialize_upnp_headers(headers(target, udn))
        info.update(metadata or {})
        self.window._on_service_type_found('UPnP', target)
        self.window._on_service_added(info['name'], info)
        return info

    def test_duplicates_merge_and_all_advertisement_headers_remain(self):
        self.add_device(metadata=parse_upnp_description(DESCRIPTION)[0])
        self.add_device(SERVICE_TYPE)
        self.add_device(DEVICE_TYPE)
        self.assertEqual(self.list.count(), 1)
        item = self.list.item(0)
        self.assertTrue(item.text().startswith('Office Router\n'))
        self.assertIn('Example Gateway 2000', item.text())
        self.assertIn('192.0.2.1:49000', item.text())
        info = item.data(Qt.ItemDataRole.UserRole)
        self.assertEqual(len(info['advertisements']), 3)
        self.assertIn(SERVICE_TYPE, info['types'])
        self.add_device(DEVICE_TYPE, udn='uuid:other', metadata={'friendly_name': 'Office Router'})
        self.assertEqual(self.list.count(), 2)  # Equal names are separate devices.

    def test_readable_filters_preserve_raw_values_and_offer_all_devices(self):
        self.add_device(DEVICE_TYPE)
        self.add_device(SERVICE_TYPE)
        self.add_device('uuid:router')
        self.add_device('urn:schemas-upnp-org:device:MediaRenderer:1', 'uuid:speaker')
        self.assertEqual(self.types.item(0).text(), 'All devices')
        self.assertFalse(any(self.types.item(i).text().startswith('uuid:') for i in range(self.types.count())))
        type_item = next(self.types.item(i) for i in range(self.types.count())
                         if self.types.item(i).data(Qt.ItemDataRole.UserRole) == SERVICE_TYPE)
        self.types.setCurrentItem(type_item)
        self.assertEqual(self.window._filters['UPnP'], SERVICE_TYPE)
        self.assertEqual(self.list.count(), 1)
        self.types.setCurrentRow(0)
        self.assertEqual(self.list.count(), 2)

    def test_search_and_selection_survive_metadata_updates(self):
        self.add_device(DEVICE_TYPE, metadata=parse_upnp_description(DESCRIPTION)[0])
        self.add_device(DEVICE_TYPE, 'uuid:other', {'friendly_name': 'Living Room'})
        self.window.upnp_search_input.setText('oFFice')
        self.assertEqual(self.list.count(), 1)
        self.list.setCurrentRow(0)
        self.add_device(SERVICE_TYPE)
        self.assertEqual(self.list.currentItem().data(Qt.ItemDataRole.UserRole)['name'], 'uuid:router')
        self.window.upnp_search_input.setText('2000')
        self.assertEqual(self.list.count(), 1)
        self.window.upnp_search_input.setText('no match')
        self.assertEqual(self.list.count(), 0)
        self.window.upnp_search_input.clear()
        self.assertEqual(self.list.count(), 2)
        self.window.clear_discovery_results()
        self.assertEqual(self.list.count(), 0)
        self.assertEqual(self.types.count(), 1)
        self.assertEqual(self.types.currentItem().text(), 'All devices')

    def test_details_show_metadata_and_each_raw_advertisement_in_scroll_area(self):
        self.add_device(DEVICE_TYPE, metadata=parse_upnp_description(DESCRIPTION)[0])
        self.add_device(SERVICE_TYPE)
        with patch.object(QDialog, 'exec'):
            self.window.show_info(self.list.item(0))
        dialog = self.window.findChildren(QDialog)[-1]
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        self.assertIn('Office Router', labels)
        self.assertIn('Example', labels)
        self.assertIn(f'uuid:router::{DEVICE_TYPE}', labels)
        self.assertIn(f'uuid:router::{SERVICE_TYPE}', labels)
        self.assertEqual(len(dialog.findChildren(QScrollArea)), 1)

    def test_mdns_display_and_type_filter(self):
        service_type = '_http._tcp.local.'
        self.window._on_service_type_found('mDNS', service_type)
        name = 'Example._http._tcp.local.'
        self.window._on_service_added(name, {'name': name, 'type': service_type, 'protocol': 'mDNS'})
        self.window.type_widgets['mDNS'].setCurrentRow(0)
        self.assertEqual(self.window.list_widgets['mDNS'].item(0).text(), name)
        self.assertEqual(self.window._filters['mDNS'], service_type)

    def test_mdns_selected_type_can_be_clicked_again_to_clear_filter(self):
        types = self.window.type_widgets['mDNS']
        services = self.window.list_widgets['mDNS']
        for service_type in ('_http._tcp.local.', '_ssh._tcp.local.'):
            self.window._on_service_type_found('mDNS', service_type)
            name = f'Example.{service_type}'
            self.window._on_service_added(name, {'name': name, 'type': service_type, 'protocol': 'mDNS'})
        self.window.show()
        self.app.processEvents()

        def click_type(row):
            QTest.mouseClick(types.viewport(), Qt.MouseButton.LeftButton,
                             pos=types.visualItemRect(types.item(row)).center())

        click_type(0)
        self.assertEqual(self.window._filters['mDNS'], '_http._tcp.local.')
        self.assertEqual(services.count(), 1)

        click_type(0)
        self.assertEqual(self.window._filters['mDNS'], '')
        self.assertEqual(self.window.filter_labels['mDNS'].text(), 'Filter:')
        self.assertEqual(services.count(), 2)
        self.assertIsNone(types.currentItem())
        self.assertEqual(types.selectedItems(), [])

        click_type(0)
        self.assertEqual(self.window._filters['mDNS'], '_http._tcp.local.')
        self.assertEqual(services.count(), 1)
        click_type(1)
        self.assertEqual(self.window._filters['mDNS'], '_ssh._tcp.local.')
        self.assertEqual(services.count(), 1)

        QTest.keyClick(types, Qt.Key.Key_Up)
        self.assertEqual(self.window._filters['mDNS'], '_http._tcp.local.')
        click_type(0)
        self.assertEqual(self.window._filters['mDNS'], '')
        self.assertEqual(services.count(), 2)


if __name__ == '__main__':
    unittest.main()
