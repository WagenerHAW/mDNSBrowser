"""
Async mDNS and UPnP Service Browser

Rewritten to use python-zeroconf's asyncio API. Runs an asyncio event loop in a background thread
and communicates with the PyQt6 main thread using Qt signals so UI updates are thread-safe.

Notes:
- Requires python-zeroconf (newer versions exposing zeroconf.asyncio.AsyncZeroconf and AsyncServiceBrowser).
- This implementation falls back to the same UI structure but relies on async get_service_info() to avoid
  missed answers caused by blocking calls.
- Tested conceptually; adapt to your environment and zeroconf version if necessary.

Author: Thorsten Wagener
Date: 2025-09-18
License: MIT
"""
import sys
import socket
import threading
import asyncio
from ipaddress import IPv4Address
import psutil
from urllib.parse import urlparse
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QListWidget, QListWidgetItem,
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QLabel, QLineEdit, QComboBox, QDialog, QGridLayout,
    QTabWidget, QScrollArea
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QIcon

if __package__:
    from .upnp import (
        parse_upnp_description, upnp_device_title, upnp_device_summary, upnp_type_label,
    )
else:
    from upnp import (
        parse_upnp_description, upnp_device_title, upnp_device_summary, upnp_type_label,
    )

# Keep your resource helpers if you have them
try:
    if __package__:
        from .os_qt_tools import get_os_logo, resource_path
    else:
        from os_qt_tools import get_os_logo, resource_path
except Exception:
    def resource_path(p):
        return p
    def get_os_logo():
        return ''

# Import async zeroconf
from zeroconf.asyncio import AsyncZeroconf, AsyncServiceBrowser
from zeroconf import ServiceStateChange

try:
    from async_upnp_client.search import SsdpSearchListener
    from async_upnp_client.ssdp import SSDP_ST_ALL
    from async_upnp_client.aiohttp import AiohttpRequester
    from async_upnp_client.const import HttpRequest
except ImportError:
    SsdpSearchListener = None
    AiohttpRequester = None
    SSDP_ST_ALL = "ssdp:all"

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class AsyncZCSignals(QObject):
    service_type_found = pyqtSignal(str, str)
    service_added = pyqtSignal(str, dict)
    service_removed = pyqtSignal(str)
    error = pyqtSignal(str)


class AsyncZCWorker:
    """Runs an asyncio loop in a background thread, manages mDNS and UPnP discovery."""

    def __init__(self, signals: AsyncZCSignals, interface: Optional[str] = None):
        self.signals = signals
        self.interface = interface
        self.thread: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.aiozc: Optional[AsyncZeroconf] = None
        self._stop_event = threading.Event()
        self._upnp_task: Optional[asyncio.Task] = None
        self._upnp_requester = AiohttpRequester(timeout=4) if AiohttpRequester else None
        self._upnp_descriptions = {}
        self._upnp_description_tasks = {}
        self._upnp_callbacks = set()
        self._upnp_description_semaphore = None

    def start(self):
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop_event.set()
        # _main owns cleanup. Closing Zeroconf from a second coroutine races
        # with _main finishing and can leave a shutdown future on a stopped loop.
        if self.thread:
            self.thread.join(timeout=5)

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception:
            logger.exception("Async loop crashed")
        finally:
            try:
                if not self.loop.is_closed():
                    self.loop.close()
            except Exception:
                pass

    async def _main(self):
        interfaces = [self.interface] if self.interface else None
        try:
            if self.interface:
                self.aiozc = AsyncZeroconf(interfaces=interfaces)
            else:
                self.aiozc = AsyncZeroconf()
        except Exception as exc:
            self.signals.error.emit(f"Failed to start AsyncZeroconf: {exc}")
            return

        enum_browser = AsyncServiceBrowser(self.aiozc.zeroconf, "_services._dns-sd._udp.local.", handlers=[self._on_service_discovered])
        self._upnp_task = asyncio.create_task(self._upnp_search_loop())

        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)

        if self._upnp_task:
            self._upnp_task.cancel()
            try:
                await self._upnp_task
            except asyncio.CancelledError:
                pass
        await self._cancel_upnp_resolution()
        await enum_browser.async_cancel()
        await self.aiozc.async_close()

    def _upnp_search_sources(self):
        if self.interface:
            return [(self.interface, 0)]

        sources = set()
        stats = psutil.net_if_stats()
        for name, addresses in psutil.net_if_addrs().items():
            interface = stats.get(name)
            if interface is None or not interface.isup:
                continue
            flags = set(getattr(interface, 'flags', '').lower().split(',')) - {''}
            if flags and 'multicast' not in flags:
                continue
            for address in addresses:
                if address.family != socket.AF_INET:
                    continue
                ip = IPv4Address(address.address)
                if not ip.is_loopback and not ip.is_unspecified:
                    sources.add((address.address, 0))
        return sorted(sources)

    async def _search_upnp_interface(self, source):
        listener = SsdpSearchListener(
            async_callback=self._on_upnp_device,
            timeout=3,
            search_target=SSDP_ST_ALL,
            source=source,
            loop=self.loop,
        )
        try:
            await listener.async_start()
            listener.async_search()
            await asyncio.sleep(3)
        finally:
            # The convenience async_search() does not close its transport when
            # cancelled. Own the listener so switching interfaces closes every
            # search socket, including searches still waiting for responses.
            listener.async_stop()

    async def _upnp_search_loop(self):
        if SsdpSearchListener is None:
            self.signals.error.emit("UPnP discovery requires async-upnp-client. Install dependencies and restart the app.")
            return

        error_reported = False
        while not self._stop_event.is_set():
            try:
                # A wildcard SSDP source only follows the default multicast
                # route. Explicit sources are needed to reach every network.
                # Refresh the list each pass to handle adapter changes.
                sources = self._upnp_search_sources()
                if not sources:
                    raise RuntimeError("No active IPv4 multicast interfaces are available")
                results = await asyncio.gather(
                    *(self._search_upnp_interface(source) for source in sources),
                    return_exceptions=True,
                )
                failures = [(source, result) for source, result in zip(sources, results)
                            if isinstance(result, Exception)]
                for source, error in failures:
                    logger.warning("UPnP discovery failed on %s: %s", source[0], error)
                if len(failures) == len(sources):
                    raise RuntimeError("Search failed on every selected interface")
                error_reported = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("UPnP discovery failed: %s", exc)
                if not error_reported:
                    self.signals.error.emit(f"UPnP discovery failed: {exc}")
                    error_reported = True

            for _ in range(60):
                if self._stop_event.is_set():
                    return
                await asyncio.sleep(0.5)

    async def _on_upnp_device(self, headers):
        if self._stop_event.is_set():
            return
        callback = asyncio.current_task()
        self._upnp_callbacks.add(callback)
        try:
            info = self._serialize_upnp_headers(headers)
            self.signals.service_type_found.emit(info['protocol'], info['type'])
            self.signals.service_added.emit(info['name'], info)

            descriptions = await self._get_upnp_description(info['location'])
            udn = info['udn'].casefold()
            metadata = next(
                (device for device in descriptions if udn and device.get('udn', '').casefold() == udn),
                None,
            )
            # Some older devices omit UDN entirely. Never give an unmatched
            # embedded device the root device's name just because the URL matches.
            if metadata is None and descriptions and (not udn or not descriptions[0].get('udn')):
                metadata = descriptions[0]
            if metadata and not self._stop_event.is_set():
                enriched = {**info, **metadata}
                if metadata.get('device_type'):
                    self.signals.service_type_found.emit('UPnP', metadata['device_type'])
                self.signals.service_added.emit(info['name'], enriched)
        except Exception:
            logger.exception("Error processing UPnP device")
        finally:
            self._upnp_callbacks.discard(callback)

    async def _get_upnp_description(self, location):
        if not self._upnp_requester or urlparse(location).scheme not in ('http', 'https'):
            return []
        now = asyncio.get_running_loop().time()
        cached = self._upnp_descriptions.get(location)
        if cached and cached[0] > now:
            return cached[1]
        task = self._upnp_description_tasks.get(location)
        if task is None:
            task = asyncio.create_task(self._load_upnp_description(location))
            self._upnp_description_tasks[location] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self._upnp_description_tasks.get(location) is task:
                self._upnp_description_tasks.pop(location, None)

    async def _load_upnp_description(self, location):
        if self._upnp_description_semaphore is None:
            self._upnp_description_semaphore = asyncio.Semaphore(4)
        descriptions = []
        async with self._upnp_description_semaphore:
            try:
                response = await self._upnp_requester.async_http_request(
                    HttpRequest('GET', location, {}, None)
                )
                if response.status_code != 200:
                    raise ValueError(f"HTTP {response.status_code}")
                descriptions = parse_upnp_description(response.body or '')
            except Exception as exc:
                logger.debug("Could not read UPnP description at %s: %s", location, exc)
        # Reuse successful descriptions for five minutes; retry failures on a
        # later discovery pass. Repeated SSDP packets share one in-flight request.
        ttl = 300 if descriptions else 30
        self._upnp_descriptions[location] = (asyncio.get_running_loop().time() + ttl, descriptions)
        return descriptions

    async def _cancel_upnp_resolution(self):
        current = asyncio.current_task()
        tasks = set(self._upnp_description_tasks.values()) | self._upnp_callbacks
        tasks.discard(current)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._upnp_description_tasks.clear()

    def _upnp_header(self, headers, key: str, default: str = ""):
        try:
            value = headers.get_lower(key.lower(), default)
        except AttributeError:
            value = headers.get(key, headers.get(key.lower(), headers.get(key.upper(), default)))
        return default if value is None else value

    def _serialize_upnp_headers(self, headers):
        location = str(self._upnp_header(headers, "location"))
        usn = str(self._upnp_header(headers, "usn"))
        st = str(self._upnp_header(headers, "st", "ssdp:all"))
        server = str(self._upnp_header(headers, "server"))
        remote_addr = self._upnp_header(headers, "_remote_addr", "")

        parsed_location = urlparse(location)
        try:
            port = parsed_location.port
        except ValueError:
            port = None
        host = parsed_location.netloc or parsed_location.path
        udn = usn.split('::', 1)[0] if usn.lower().startswith('uuid:') else ''
        name = udn.casefold() or usn or location or str(remote_addr)
        service_type = st

        props = {}
        try:
            items = headers.items()
        except AttributeError:
            items = []
        for key, value in items:
            props[str(key)] = str(value)

        addresses = []
        if host:
            addresses.append(host)
        if location:
            addresses.append(location)
        if remote_addr:
            addresses.append(str(remote_addr))

        return {
            'name': name,
            'type': service_type,
            'protocol': 'UPnP',
            'udn': udn,
            'host': host or (str(remote_addr[0]) if isinstance(remote_addr, tuple) else str(remote_addr)),
            'location': location,
            'usn': usn,
            'addresses': addresses,
            'port': port,
            'weight': None,
            'priority': None,
            'server': server,
            'properties': props,
        }

    def _on_service_discovered(self, zeroconf, service_type, name, state_change):
        if self.loop is None or self._stop_event.is_set():
            return
        asyncio.run_coroutine_threadsafe(self._handle_discovered(service_type, name, state_change), self.loop)

    async def _handle_discovered(self, service_type, name, state_change):
        try:
            if service_type == "_services._dns-sd._udp.local.":
                short_name = name
                if len(short_name.split('.')) > 4:
                    short_name = '.'.join(short_name.split('.')[-4:])
                self.signals.service_type_found.emit("mDNS", short_name)
                try:
                    _ = AsyncServiceBrowser(self.aiozc.zeroconf, short_name, handlers=[self._on_type_state_change])
                except Exception as exc:
                    logger.exception("Could not start browser for %s: %s", short_name, exc)
            else:
                await self._on_type_state_change(self.aiozc.zeroconf, service_type, name, state_change)
        except Exception:
            logger.exception("Error processing discovered service")

    def _on_type_state_change(self, zeroconf, service_type, name, state_change):
        if self.loop is None or self._stop_event.is_set():
            return
        asyncio.run_coroutine_threadsafe(self._handle_type_state_change(service_type, name, state_change), self.loop)

    async def _handle_type_state_change(self, service_type, name, state_change):
        try:
            if state_change is ServiceStateChange.Added:
                info = await self.aiozc.async_get_service_info(service_type, name, timeout=3000)
                if info:
                    info_dict = self._serialize_info(info)
                    self.signals.service_added.emit(name, info_dict)
            elif state_change is ServiceStateChange.Removed:
                self.signals.service_removed.emit(name)
        except Exception:
            logger.exception("Error during type state change handling for %s", name)

    def _serialize_info(self, info):
        try:
            addresses = [f"{a}:{int(info.port)}" for a in info.parsed_addresses()]
        except Exception:
            addresses = []
        props = {}
        try:
            for k, v in info.properties.items():
                try:
                    key = k.decode('utf-8', errors='replace') if isinstance(k, (bytes, bytearray)) else str(k)
                except Exception:
                    key = str(k)
                if v is None:
                    val = None
                else:
                    try:
                        val = v.decode('utf-8')
                    except Exception:
                        val = v.hex() if isinstance(v, (bytes, bytearray)) else str(v)
                props[key] = val
        except Exception:
            props = {}

        return {
            'name': info.name,
            'type': info.type,
            'protocol': 'mDNS',
            'addresses': addresses,
            'port': int(info.port),
            'weight': getattr(info, 'weight', None),
            'priority': getattr(info, 'priority', None),
            'server': getattr(info, 'server', None),
            'properties': props,
        }


class ToggleFilterListWidget(QListWidget):
    """Allow clicking the current selection again to clear a type filter."""

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if (event.button() == Qt.MouseButton.LeftButton and item is not None
                and item == self.currentItem() and item.isSelected()):
            self.setCurrentItem(None)
            event.accept()
            return
        super().mousePressEvent(event)


class MDNSBrowser(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('mDNS and UPnP Service Browser')
        self.setGeometry(100, 100, 1024, 768)

        self.interface_ip = None
        self.service_types = {"mDNS": set(), "UPnP": set()}
        self.services = {"mDNS": {}, "UPnP": {}}
        self._filters = {"mDNS": "", "UPnP": ""}
        self.type_widgets = {}
        self.list_widgets = {}
        self.filter_labels = {}
        self.upnp_search_input = QLineEdit()
        self.upnp_search_input.setPlaceholderText("Search by name, model, manufacturer or address…")
        self.upnp_search_input.setClearButtonEnabled(True)
        self.upnp_search_input.textChanged.connect(lambda: self._refresh_service_list('UPnP'))

        self.interface_selector = QComboBox()
        self.interface_selector.addItem("All interfaces", userData=None)
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    self.interface_selector.addItem(f"{iface} ({addr.address})", userData=addr.address)
        self.interface_selector.currentIndexChanged.connect(self.interface_changed)

        self.manual_query_label = QLabel("Manual mDNS Service Query:")
        self.manual_query_input = QLineEdit()
        self.manual_query_button = QPushButton("Query mDNS Service")
        self.manual_query_button.clicked.connect(self.manual_query)

        self.dante_query_button = QPushButton("Add Dante mDNS Services")
        self.dante_query_button.clicked.connect(self.dante_query)

        self.discovery_tabs = QTabWidget()
        self.discovery_tabs.addTab(
            self._create_discovery_tab("mDNS", "mDNS service types:", "mDNS services:"),
            "mDNS",
        )
        self.discovery_tabs.addTab(
            self._create_discovery_tab("UPnP", "UPnP device / service types:", "UPnP devices:"),
            "UPnP",
        )

        self.rescan_button = QPushButton("Clear and rescan Network")
        self.rescan_button.clicked.connect(self.rescan_network)

        main_layout = QVBoxLayout()
        main_layout.addWidget(QLabel("Select Network Interface:"))
        main_layout.addWidget(self.interface_selector)
        main_layout.addWidget(self.discovery_tabs)
        main_layout.addWidget(self.rescan_button)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self.signals = AsyncZCSignals()
        self.signals.service_type_found.connect(self._on_service_type_found)
        self.signals.service_added.connect(self._on_service_added)
        self.signals.service_removed.connect(self._on_service_removed)
        self.signals.error.connect(self._on_error)

        self.worker = AsyncZCWorker(self.signals, interface=self.interface_ip)
        self.worker.start()

    def _create_discovery_tab(self, protocol: str, type_header: str, list_header: str):
        type_widget = ToggleFilterListWidget() if protocol == 'mDNS' else QListWidget()
        list_widget = QListWidget()
        filter_label = QLabel("Filter:")

        type_widget.currentItemChanged.connect(
            lambda current, previous, protocol=protocol: self.set_service_filter(protocol, current, previous)
        )
        list_widget.itemClicked.connect(self.show_info)

        self.type_widgets[protocol] = type_widget
        self.list_widgets[protocol] = list_widget
        self.filter_labels[protocol] = filter_label
        if protocol == 'UPnP':
            self._add_upnp_all_devices_item()

        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel(type_header))
        left_layout.addWidget(type_widget)

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel(list_header))
        if protocol == 'UPnP':
            right_layout.addWidget(self.upnp_search_input)
        right_layout.addWidget(list_widget)

        lists_layout = QHBoxLayout()
        lists_layout.addLayout(left_layout, 1)
        lists_layout.addLayout(right_layout, 2 if protocol == 'UPnP' else 1)

        tab_layout = QVBoxLayout()
        tab_layout.addLayout(lists_layout)
        tab_layout.addWidget(filter_label)
        if protocol == "mDNS":
            manual_layout = QHBoxLayout()
            manual_layout.addWidget(self.manual_query_input)
            manual_layout.addWidget(self.manual_query_button)

            tab_layout.addWidget(self.manual_query_label)
            tab_layout.addLayout(manual_layout)
            tab_layout.addWidget(self.dante_query_button)

        tab = QWidget()
        tab.setLayout(tab_layout)
        return tab

    def _add_upnp_all_devices_item(self):
        item = QListWidgetItem("All devices")
        item.setData(Qt.ItemDataRole.UserRole, '')
        self.type_widgets['UPnP'].insertItem(0, item)
        self.type_widgets['UPnP'].setCurrentItem(item)

    def interface_changed(self):
        self.interface_ip = self.interface_selector.currentData()
        logger.info(f"Selected interface: {self.interface_ip or 'All interfaces'}")
        self.worker.stop()
        self.worker = AsyncZCWorker(self.signals, interface=self.interface_ip)
        self.clear_discovery_results()
        self.worker.start()

    def set_service_filter(
        self,
        protocol: str,
        list_item: Optional[QListWidgetItem],
        _previous_item: Optional[QListWidgetItem] = None,
    ):
        if list_item is None:
            self._filters[protocol] = ""
            self.filter_labels[protocol].setText("Filter:")
            self._refresh_service_list(protocol)
            return

        selected_type = list_item.data(Qt.ItemDataRole.UserRole) if protocol == 'UPnP' else list_item.text()
        self._filters[protocol] = selected_type
        self.filter_labels[protocol].setText(f"Filter: {list_item.text()}")
        self._refresh_service_list(protocol)

    def normalize_service_type(self, service: str) -> str:
        service = service.strip()
        if not service:
            return ""
        if service.endswith(".local."):
            return service
        if service.endswith(".local"):
            return service + "."
        if service.endswith("."):
            return service + "local."
        return service + ".local."

    def manual_query(self):
        service_query = self.manual_query_input.text().strip()
        if not service_query:
            return
        service_query = self.normalize_service_type(service_query)
        if self.worker.loop and self.worker.aiozc:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._start_manual_browser(service_query), self.worker.loop
                )
            except Exception as exc:
                self._on_error(str(exc))

    def dante_query(self):
        dante_services = [
            "_dante-safe._udp",
            "_dante-upgr._udp",
            "_netaudio-arc._udp",
            "_netaudio-chan._udp",
            "_netaudio-cmc._udp",
            "_netaudio-dbc._udp",
            "_dante-ddm-d._udp",
            "_dante-ddm-c._tcp"
        ]
        for service_query in dante_services:
            if self.worker.loop and self.worker.aiozc:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._start_manual_browser(service_query+".local."), self.worker.loop
                    )
                except Exception as exc:
                    self._on_error(str(exc))

    async def _start_manual_browser(self, service_query: str):
        try:
            if service_query == "_services._dns-sd._udp.local.":
                return
            AsyncServiceBrowser(self.worker.aiozc.zeroconf, service_query, handlers=[self.worker._on_type_state_change])
            self.signals.service_type_found.emit("mDNS", service_query)
        except Exception as exc:
            self.signals.error.emit(str(exc))

    def _on_service_type_found(self, protocol: str, type_str: str):
        if protocol not in self.service_types:
            return
        if protocol == 'UPnP' and (not type_str or type_str.lower().startswith('uuid:') or type_str == SSDP_ST_ALL):
            return
        if type_str not in self.service_types[protocol]:
            self.service_types[protocol].add(type_str)
            type_widget = self.type_widgets[protocol]
            if protocol == 'UPnP':
                item = QListWidgetItem(upnp_type_label(type_str))
                item.setData(Qt.ItemDataRole.UserRole, type_str)
                item.setToolTip(type_str)
                # Keep "All devices" pinned above the sorted, readable types.
                row = 1
                while row < type_widget.count() and type_widget.item(row).text().casefold() < item.text().casefold():
                    row += 1
                type_widget.insertItem(row, item)
            else:
                type_widget.addItem(type_str)
                type_widget.sortItems()

    def _on_service_added(self, name: str, info: dict):
        protocol = info.get('protocol', 'mDNS')
        if protocol not in self.services:
            return
        if protocol == 'UPnP':
            previous = self.services[protocol].get(name, {})
            types = set(previous.get('types', []))
            types.update(filter(None, (info.get('type'), info.get('device_type'))))
            advertisements = dict(previous.get('advertisements', {}))
            advertisements[info.get('usn') or info.get('type', '')] = info.get('properties', {})
            info = {
                **previous,
                **info,
                'types': sorted(types),
                'advertisements': advertisements,
            }
        self.services[protocol][name] = info
        self._refresh_service_list(protocol)

    def _on_service_removed(self, name: str):
        if name in self.services["mDNS"]:
            del self.services["mDNS"][name]
        self._refresh_service_list("mDNS")

    def _on_error(self, message: str):
        dialog = QDialog(self)
        dialog.setWindowTitle("Error")
        layout = QVBoxLayout()
        layout.addWidget(QLabel(message, alignment=Qt.AlignmentFlag.AlignCenter))
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.accept)
        layout.addWidget(ok_button)
        dialog.setLayout(layout)
        dialog.exec()

    def _refresh_service_list(self, protocol: str):
        list_widget = self.list_widgets[protocol]
        active_filter = self._filters[protocol]
        selected = list_widget.currentItem()
        selected_name = selected.data(Qt.ItemDataRole.UserRole).get('name') if selected else None
        scroll_position = list_widget.verticalScrollBar().value()
        list_widget.clear()
        services = self.services[protocol].items()
        if protocol == 'UPnP':
            services = sorted(services, key=lambda entry: (upnp_device_title(entry[1]).casefold(), entry[0]))
        else:
            services = sorted(services)
        for name, info in services:
            if protocol == 'UPnP':
                if active_filter and active_filter not in info.get('types', []):
                    continue
                title = upnp_device_title(info)
                summary = upnp_device_summary(info)
                search = self.upnp_search_input.text().strip().casefold()
                if search and search not in ' '.join((title, summary, name, info.get('location', ''))).casefold():
                    continue
                item = QListWidgetItem(f"{title}\n{summary}")
                item.setToolTip('\n'.join(filter(None, (title, summary, info.get('udn'), info.get('location')))))
            else:
                if active_filter and active_filter not in info.get('type', ''):
                    continue
                item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, info)
            list_widget.addItem(item)
            if info.get('name') == selected_name:
                list_widget.setCurrentItem(item)
        list_widget.verticalScrollBar().setValue(scroll_position)

    def show_info(self, list_item: QListWidgetItem):
        info = list_item.data(Qt.ItemDataRole.UserRole)
        if not info:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Service Information")
        layout = QGridLayout()
        row = 0
        head_css = "font-size: 16pt; padding: 7px;"
        value_css = "background-color: rgba(0, 0, 0, 200); color: #ccc; padding: 6px;"
        label_css = "padding-top: 3px;"

        is_upnp = info.get('protocol') == 'UPnP'
        prop_head = QLabel(upnp_device_title(info) if is_upnp else info.get('name', ''))
        prop_head.setTextFormat(Qt.TextFormat.PlainText)
        prop_head.setStyleSheet(head_css)
        layout.addWidget(prop_head, row, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
        row += 1

        content = {
            'Protocol:': info.get('protocol', ''),
            'Type:': info.get('type', ''),
            'Server:': info.get('server', ''),
            'Address / location:': '\n'.join(info.get('addresses', [])),
        }
        if is_upnp:
            content['Type:'] = '\n'.join(info.get('types', [info.get('type', '')]))
            for label, key in (
                ('Manufacturer:', 'manufacturer'), ('Model:', 'model_name'),
                ('Model number:', 'model_number'), ('Description:', 'model_description'),
                ('Serial number:', 'serial_number'), ('Device ID:', 'udn'),
                ('Presentation URL:', 'presentation_url'),
            ):
                if info.get(key):
                    content[label] = info[key]
        if info.get('priority') is not None or info.get('weight') is not None:
            content['Loadbalance:'] = f"Priority: {info.get('priority')} weight: {info.get('weight')}"

        for label_text, value_text in content.items():
            label = QLabel(label_text)
            label.setStyleSheet(label_css)
            value = QLabel(value_text)
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setStyleSheet(value_css)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(value, row, 1, Qt.AlignmentFlag.AlignTop)
            row += 1

        if is_upnp:
            property_groups = [
                (f"SSDP: {usn}", props)
                for usn, props in sorted(info.get('advertisements', {}).items())
            ]
        else:
            property_groups = [('Properties', info.get('properties', {}))]
        for heading, props in property_groups:
            if not props:
                continue
            prop_row = row
            for k, v in props.items():
                if k == '' and v == None:
                    continue
                row += 1
                key_label = QLabel(f"{k}:")
                val_label = QLabel(str(v))
                key_label.setTextFormat(Qt.TextFormat.PlainText)
                val_label.setTextFormat(Qt.TextFormat.PlainText)
                key_label.setStyleSheet(label_css)
                val_label.setStyleSheet(value_css)
                val_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                layout.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignTop)
                layout.addWidget(val_label, row, 1, Qt.AlignmentFlag.AlignTop)

            if row > prop_row:
                prop_head2 = QLabel(heading)
                prop_head2.setTextFormat(Qt.TextFormat.PlainText)
                prop_head2.setStyleSheet(head_css)
                layout.addWidget(prop_head2, prop_row, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
                row += 1

        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.accept)
        dialog.setLayout(QVBoxLayout())
        if is_upnp:
            content_widget = QWidget()
            content_widget.setLayout(layout)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content_widget)
            dialog.layout().addWidget(scroll)
            dialog.resize(800, 650)
        else:
            dialog.layout().addLayout(layout)
        dialog.layout().addWidget(ok_button)
        dialog.exec()

    def clear_filter(self, protocol: str):
        self._filters[protocol] = ""
        self.filter_labels[protocol].setText("Filter:")

    def clear_discovery_results(self):
        for protocol in self.services:
            self.service_types[protocol].clear()
            self.type_widgets[protocol].clear()
            self.services[protocol].clear()
            self.list_widgets[protocol].clear()
            self.clear_filter(protocol)
        self.upnp_search_input.clear()
        self._add_upnp_all_devices_item()

    def rescan_network(self):
        self.worker.stop()
        self.worker = AsyncZCWorker(self.signals, interface=self.interface_ip)
        self.clear_discovery_results()
        self.worker.start()

    def closeEvent(self, event):
        try:
            self.worker.stop()
        except Exception:
            pass
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    logo = resource_path(get_os_logo()) if 'get_os_logo' in globals() else ''
    if logo:
        app.setWindowIcon(QIcon(logo))
    window = MDNSBrowser()
    window.show()
    sys.exit(app.exec())
