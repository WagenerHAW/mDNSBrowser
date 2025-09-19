"""
Async mDNS Service Browser

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
import psutil
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QListWidget, QListWidgetItem,
    QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QLabel, QLineEdit, QComboBox, QDialog, QGridLayout
)
from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QIcon

# Keep your resource helpers if you have them
try:
    from os_qt_tools import *
except Exception:
    def resource_path(p):
        return p
    def get_os_logo():
        return ''

# Import async zeroconf
from zeroconf.asyncio import AsyncZeroconf, AsyncServiceBrowser
from zeroconf import ServiceStateChange

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class AsyncZCSignals(QObject):
    service_type_found = pyqtSignal(str)
    service_added = pyqtSignal(str, dict)
    service_removed = pyqtSignal(str)
    error = pyqtSignal(str)


class AsyncZCWorker:
    """Runs an asyncio loop in a background thread, manages AsyncZeroconf and browser tasks."""

    def __init__(self, signals: AsyncZCSignals, interface: Optional[str] = None):
        self.signals = signals
        self.interface = interface
        self.thread: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.aiozc: Optional[AsyncZeroconf] = None
        self._stop_event = threading.Event()

    def start(self):
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self._stop_event.set()
        if self.loop and self.loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)
            try:
                fut.result(timeout=5)
            except Exception as exc:
                logger.exception("Error while shutting down async zeroconf: %s", exc)
        if self.thread:
            self.thread.join(timeout=5)

    async def _shutdown(self):
        try:
            if self.aiozc:
                await self.aiozc.async_close()
        except Exception as exc:
            logger.error("Error in AsyncZCWorker._shutdown: %s", exc)

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

        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)

        await enum_browser.async_cancel()
        await self.aiozc.async_close()

    def _on_service_discovered(self, zeroconf, service_type, name, state_change):
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._handle_discovered(service_type, name, state_change), self.loop)

    async def _handle_discovered(self, service_type, name, state_change):
        try:
            if service_type == "_services._dns-sd._udp.local.":
                short_name = name
                if len(short_name.split('.')) > 4:
                    short_name = '.'.join(short_name.split('.')[-4:])
                self.signals.service_type_found.emit(short_name)
                try:
                    _ = AsyncServiceBrowser(self.aiozc.zeroconf, short_name, handlers=[self._on_type_state_change])
                except Exception as exc:
                    logger.exception("Could not start browser for %s: %s", short_name, exc)
            else:
                await self._on_type_state_change(self.aiozc.zeroconf, service_type, name, state_change)
        except Exception:
            logger.exception("Error processing discovered service")

    def _on_type_state_change(self, zeroconf, service_type, name, state_change):
        if self.loop is None:
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
            'addresses': addresses,
            'port': int(info.port),
            'weight': getattr(info, 'weight', None),
            'priority': getattr(info, 'priority', None),
            'server': getattr(info, 'server', None),
            'properties': props,
        }


class MDNSBrowser(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('mDNS Service Browser')
        self.setGeometry(100, 100, 1024, 768)

        self.interface_ip = None
        self.service_types = set()
        self.services = {}
        self._filter = ""

        self.interface_selector = QComboBox()
        self.interface_selector.addItem("All interfaces", userData=None)
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    self.interface_selector.addItem(f"{iface} ({addr.address})", userData=addr.address)
        self.interface_selector.currentIndexChanged.connect(self.interface_changed)

        self.service_type_widget_hl = QLabel('Query results:')
        self.service_type_widget = QListWidget()
        self.service_filter = QLabel("Filter:")
        self.service_list_widget_hl = QLabel('Services:')
        self.service_list_widget = QListWidget()

        self.service_type_widget.itemClicked.connect(self.set_service_filter)
        self.service_list_widget.itemClicked.connect(self.show_info)

        self.rescan_button = QPushButton("Clear and rescan Network")
        self.rescan_button.clicked.connect(self.rescan_network)

        self.manual_query_label = QLabel("Manual Service Query:")
        self.manual_query_input = QLineEdit()
        self.manual_query_button = QPushButton("Query Service")
        self.manual_query_button.clicked.connect(self.manual_query)

        self.dante_query_button = QPushButton("Add Dante Services")
        self.dante_query_button.clicked.connect(self.dante_query)

        left_layout = QVBoxLayout()
        left_layout.addWidget(self.service_type_widget_hl)
        left_layout.addWidget(self.service_type_widget)

        right_layout = QVBoxLayout()
        right_layout.addWidget(self.service_list_widget_hl)
        right_layout.addWidget(self.service_list_widget)

        main_h = QHBoxLayout()
        main_h.addLayout(left_layout)
        main_h.addLayout(right_layout)

        main_layout = QVBoxLayout()
        main_layout.addWidget(QLabel("Select Network Interface:"))
        main_layout.addWidget(self.interface_selector)
        main_layout.addLayout(main_h)
        main_layout.addWidget(self.service_filter)
        main_layout.addWidget(self.rescan_button)
        main_layout.addWidget(self.manual_query_label)

        manual_h = QHBoxLayout()
        manual_h.addWidget(self.manual_query_input)
        manual_h.addWidget(self.manual_query_button)

        main_layout.addLayout(manual_h)
        main_layout.addWidget(self.dante_query_button)

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

    def interface_changed(self):
        self.interface_ip = self.interface_selector.currentData()
        logger.info(f"Selected interface: {self.interface_ip or 'All interfaces'}")
        self.worker.stop()
        self.worker = AsyncZCWorker(self.signals, interface=self.interface_ip)
        self.service_types.clear()
        self.service_type_widget.clear()
        self.services.clear()
        self.service_list_widget.clear()
        self.worker.start()

    def set_service_filter(self, list_item: QListWidgetItem):
        selected_text = list_item.text()
        if self._filter == selected_text:
            self._filter = ""
            list_item.setSelected(False)
            self.service_list_widget.clear()
        else:
            self._filter = selected_text
        self.service_filter.setText(f"Filter: {self._filter}")
        self._refresh_service_list()

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
            self.signals.service_type_found.emit(service_query)
        except Exception as exc:
            self.signals.error.emit(str(exc))

    def _on_service_type_found(self, type_str: str):
        if type_str not in self.service_types:
            self.service_types.add(type_str)
            self.service_type_widget.addItem(type_str)
            self.service_type_widget.sortItems()

    def _on_service_added(self, name: str, info: dict):
        self.services[name] = info
        self._refresh_service_list()

    def _on_service_removed(self, name: str):
        if name in self.services:
            del self.services[name]
        self._refresh_service_list()

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

    def _refresh_service_list(self):
        self.service_list_widget.clear()
        for name, info in sorted(self.services.items()):
            if self._filter and self._filter not in info.get('type', ''):
                continue
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, info)
            self.service_list_widget.addItem(item)
        self.service_list_widget.sortItems()

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

        prop_head = QLabel(info.get('name', ''))
        prop_head.setStyleSheet(head_css)
        layout.addWidget(prop_head, row, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
        row += 1

        content = {
            'Server:': info.get('server', ''),
            'IP address:': '\n'.join(info.get('addresses', [])),
            'Loadbalance:': f"Priority: {info.get('priority')} weight: {info.get('weight')}"
        }

        for label_text, value_text in content.items():
            label = QLabel(label_text)
            label.setStyleSheet(label_css)
            value = QLabel(value_text)
            value.setStyleSheet(value_css)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(value, row, 1, Qt.AlignmentFlag.AlignTop)
            row += 1

        props = info.get('properties', {})
        if props:
            prop_row = row
            for k, v in props.items():
                if k == '' and v == None:
                    continue
                row += 1
                key_label = QLabel(f"{k}:")
                val_label = QLabel(str(v))
                key_label.setStyleSheet(label_css)
                val_label.setStyleSheet(value_css)
                val_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                layout.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignTop)
                layout.addWidget(val_label, row, 1, Qt.AlignmentFlag.AlignTop)

            if row > prop_row:
                prop_head2 = QLabel("Properties")
                prop_head2.setStyleSheet(head_css)
                layout.addWidget(prop_head2, prop_row, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)

        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.accept)
        dialog.setLayout(QVBoxLayout())
        dialog.layout().addLayout(layout)
        dialog.layout().addWidget(ok_button)
        dialog.exec()

    def clear_filter(self):
        self._filter = None
        self.service_filter.setText("")

    def rescan_network(self):
        self.worker.stop()
        self.worker = AsyncZCWorker(self.signals, interface=self.interface_ip)
        self.service_types.clear()
        self.service_type_widget.clear()
        self.services.clear()
        self.service_list_widget.clear()
        self.clear_filter()
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