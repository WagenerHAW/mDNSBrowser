"""
mDNS Service Browser

This application browses and queries mDNS services on the local network.
It allows users to view discovered services and perform manual queries.

NEEDED REWRITE TO ASYNC - FEEL FREE TO CONTRIBUTE

Author: Thorsten Wagener
Date: 20.09.2024
License: free
"""
import sys
import time
import socket
import psutil

from PyQt6.QtWidgets import (QApplication, QMainWindow, QListWidget, QListWidgetItem,
                             QVBoxLayout, QHBoxLayout, QGridLayout, QWidget, QDialog, QPushButton,
                             QLabel, QLineEdit, QComboBox)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QIcon

from zeroconf import ServiceBrowser, Zeroconf, ServiceStateChange, InterfaceChoice
from typing import cast

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

from os_qt_tools import *

class MDNSBrowser(QMainWindow):
    def __init__(self):
        super().__init__()

        self.interface_ip = None
        self.zeroconf = None
        self.browser = None
        self.service_browsers = {}
        self.service_browsers_added = {}
        self.services = {}
        self.service_types = set()
        self._filter = ""

        self.setWindowTitle('mDNS Service Browser')
        self.setGeometry(100, 100, 1024, 768)

        # Interface selection
        self.interface_selector = QComboBox()
        self.interface_selector.addItem("All interfaces", userData=None)
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    self.interface_selector.addItem(f"{iface} ({addr.address})", userData=addr.address)
        self.interface_selector.currentIndexChanged.connect(self.interface_changed)

        # UI Setup
        self.service_type_widget_hl = QLabel('Query results:')
        self.service_type_widget = QListWidget()
        self.service_filter = QLabel("Filter:")
        self.service_list_widget_hl = QLabel('Services:')
        self.service_list_widget = QListWidget()

        self.service_type_widget.itemClicked.connect(self.set_service_filter)
        self.service_list_widget.itemClicked.connect(self.show_info)

        self.layout = QHBoxLayout()
        self.service_type_layout = QVBoxLayout()
        self.service_type_layout.addWidget(self.service_type_widget_hl)
        self.service_type_layout.addWidget(self.service_type_widget)
        self.layout.addLayout(self.service_type_layout)

        self.service_list_layout = QVBoxLayout()
        self.service_list_layout.addWidget(self.service_list_widget_hl)
        self.service_list_layout.addWidget(self.service_list_widget)
        self.layout.addLayout(self.service_list_layout)

        self.main_layout = QVBoxLayout()
        self.main_layout.addWidget(QLabel("Select Network Interface:"))
        self.main_layout.addWidget(self.interface_selector)
        self.main_layout.addLayout(self.layout)
        self.main_layout.addWidget(self.service_filter)

        self.rescan_button = QPushButton("Clear and rescan Network")
        self.rescan_button.clicked.connect(self.rescan_network)
        self.main_layout.addWidget(self.rescan_button)

        self.auto_discovery_checkbox = QPushButton("Discovery for _services._dns-sd._udp.local. running...")
        self.auto_discovery_checkbox.setCheckable(True)
        self.auto_discovery_checkbox.setChecked(True)
        self.auto_discovery_checkbox.toggled.connect(self.toggle_auto_discovery)
        self.main_layout.addWidget(self.auto_discovery_checkbox)

        self.manual_query_label = QLabel("Manual Service Query:")
        self.manual_query_input = QLineEdit()
        self.manual_query_button = QPushButton("Query Service")
        self.manual_query_button.clicked.connect(self.query_service)
        self.main_layout.addWidget(self.manual_query_label)
        self.main_layout.addWidget(self.manual_query_input)
        self.main_layout.addWidget(self.manual_query_button)

        container = QWidget()
        container.setLayout(self.main_layout)
        self.setCentralWidget(container)

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_list)
        self.timer.start(1000)

        self.init_zeroconf()

    def init_zeroconf(self):
        if self.zeroconf:
            self.zeroconf.close()

        self.zeroconf = Zeroconf(interfaces=[self.interface_ip] if self.interface_ip else InterfaceChoice.All)

        if self.auto_discovery_checkbox.isChecked():
            self.browser = ServiceBrowser(self.zeroconf, "_services._dns-sd._udp.local.",
                                          handlers=[self.on_service_discovered])

    def interface_changed(self):
        self.interface_ip = self.interface_selector.currentData()
        logger.info(f"Selected interface: {self.interface_ip or 'All interfaces'}")
        self.rescan_network()

    def toggle_auto_discovery(self, checked):
        if checked:
            self.browser = ServiceBrowser(self.zeroconf, "_services._dns-sd._udp.local.",
                                          handlers=[self.on_service_discovered])
            self.auto_discovery_checkbox.setText("Discovery for _services._dns-sd._udp.local. running...")
        else:
            self.stop_browsers()
            self.auto_discovery_checkbox.setText("Discovery for _services._dns-sd._udp.local. stopped!")

    def set_service_filter(self, list_item):
        selected_text = list_item.text()
        if self._filter == selected_text:
            self._filter = ""
            list_item.setSelected(False)
            self.service_list_widget.clear()
        else:
            self._filter = selected_text
        self.service_filter.setText(f"Filter: {self._filter}")

    def query_service(self):
        service_query = self.manual_query_input.text().strip()
        if service_query == "_services._dns-sd._udp.local.":
            self.auto_discovery_checkbox.setChecked(True)
            return
        try:
            browser = ServiceBrowser(self.zeroconf, service_query, handlers=[self.on_service_discovered])
            self.service_browsers_added[service_query] = browser
            self.service_types.add(service_query)
            self.service_browsers[service_query] = browser
        except Exception as e:
            self.show_error_dialog(str(e))

    def show_error_dialog(self, message):
        dialog = QDialog(self)
        dialog.setWindowTitle("Error")
        layout = QVBoxLayout()
        layout.addWidget(QLabel(message, alignment=Qt.AlignmentFlag.AlignCenter))
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.accept)
        layout.addWidget(ok_button)
        dialog.setLayout(layout)
        dialog.exec()

    def show_info(self, list_item):
        info = list_item.data(Qt.ItemDataRole.UserRole)
        if info:
            name = f"{info.name}"
            addresses = [f"{addr}:{cast(int, info.port)}" for addr in info.parsed_scoped_addresses()]
            weight = info.weight
            priority = info.priority
            server = info.server
            properties = info.properties

            # Create a dialog to display the information
            dialog = QDialog(self)
            dialog.setWindowTitle("Service Information")

            layout = QGridLayout()
            layout.setSpacing(0)
            layout.setColumnStretch(0, 0)
            layout.setColumnStretch(1, 1)

            value_css = "background-color: rgba(0, 0, 0, 200); color: #ccc; padding: 10px; padding-top: 3px;"
            label_css = "padding-top: 3px;"
            head_css = "font-size: 20pt; padding: 7px;"
            content = {}
            # Add information to the grid layout
            server_value = QLabel(server)
            content[QLabel("Server:")] = server_value
            # Address Label
            address_label = QLabel("\n".join(addresses))

            content[QLabel("IP address:")] = address_label
            # Weight

            loadbalance_label = QLabel(f"Priority: {priority} weight: {weight}")
            content[QLabel("Loadbalance:")] = loadbalance_label

            row = 0
            prop_head = QLabel(name)
            prop_head.setStyleSheet(head_css)
            layout.addWidget(prop_head, row, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
            row += 1
            for label, value in content.items():
                value.setStyleSheet(value_css)
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                label.setStyleSheet(label_css)

                layout.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
                layout.addWidget(value, row, 1, Qt.AlignmentFlag.AlignTop)
                row += 1

            if properties and len(properties) > 0:
                row_helper = row
                row += 1
                for label, value in properties.items():
                    key_str = label.decode('utf-8', errors='replace')
                    try:
                        if value is None:
                            # if there is a prop b'': None only the headline should be omitted
                            continue
                        value_str = value.decode('utf-8')
                    except UnicodeDecodeError:
                        value_str = value.hex()  # Use hex representation for binary data
                    except AttributeError:
                        continue


                    key_label = QLabel(f"{key_str}:")
                    key_label.setStyleSheet(label_css)
                    # key_label.setStyleSheet("padding-left:5px; padding-bottom: 5px; padding-top:2px; margin: 0px; ")

                    value_label = QLabel(f"{value_str}")
                    value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    value_label.setStyleSheet(value_css)
                    layout.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignTop)
                    layout.addWidget(value_label, row, 1, Qt.AlignmentFlag.AlignTop)
                    row += 1

                # if there is a prop b'': None only the headline should be omitted
                if row_helper + 1 < row:
                    prop_head = QLabel("Properties")
                    prop_head.setStyleSheet(head_css)
                    layout.addWidget(prop_head, row_helper, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)

            # Add OK button to close the dialog
            button_box = QHBoxLayout()
            ok_button = QPushButton("OK")
            ok_button.clicked.connect(dialog.accept)
            button_box.addWidget(ok_button, alignment=Qt.AlignmentFlag.AlignCenter)

            # Add layouts to the dialog
            main_layout = QVBoxLayout()
            main_layout.addLayout(layout)
            main_layout.addLayout(button_box)
            dialog.setLayout(main_layout)

            # Show the dialog
            dialog.exec()

    def new_show_info(self, list_item):
        info = list_item.data(Qt.ItemDataRole.UserRole)
        if info:
            name = f"{info.name}"
            addresses = [f"{addr}:{cast(int, info.port)}" for addr in info.parsed_scoped_addresses()]
            weight = info.weight
            priority = info.priority
            server = info.server
            properties = info.properties

            dialog = QDialog(self)
            dialog.setWindowTitle("Service Information")
            layout = QGridLayout()
            value_css = "background-color: rgba(0, 0, 0, 200); color: #ccc; padding: 10px; padding-top: 3px;"
            label_css = "padding-top: 3px;"
            head_css = "font-size: 20pt; padding: 7px;"

            content = {
                QLabel("Server:"): QLabel(server),
                QLabel("IP address:"): QLabel("\n".join(addresses)),
                QLabel("Loadbalance:"): QLabel(f"Priority: {priority} weight: {weight}")
            }

            row = 0
            prop_head = QLabel(name)
            prop_head.setStyleSheet(head_css)
            layout.addWidget(prop_head, row, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
            row += 1
            for label, value in content.items():
                value.setStyleSheet(value_css)
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                label.setStyleSheet(label_css)
                layout.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
                layout.addWidget(value, row, 1, Qt.AlignmentFlag.AlignTop)
                row += 1

            if properties:
                row_helper = row
                row += 1
                for label, value in properties.items():
                    key_str = label.decode('utf-8', errors='replace')
                    try:
                        value_str = value.decode('utf-8') if value else ""
                    except Exception:
                        value_str = value.hex() if value else ""
                    key_label = QLabel(f"{key_str}:")
                    value_label = QLabel(value_str)
                    key_label.setStyleSheet(label_css)
                    value_label.setStyleSheet(value_css)
                    value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    layout.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignTop)
                    layout.addWidget(value_label, row, 1, Qt.AlignmentFlag.AlignTop)
                    row += 1

                if row > row_helper + 1:
                    prop_head = QLabel("Properties")
                    prop_head.setStyleSheet(head_css)
                    layout.addWidget(prop_head, row_helper, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)

            ok_button = QPushButton("OK")
            ok_button.clicked.connect(dialog.accept)
            dialog.setLayout(QVBoxLayout())
            dialog.layout().addLayout(layout)
            dialog.layout().addWidget(ok_button)
            dialog.exec()

    def on_service_discovered(self, zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Added:
            if len(name.split(".")) > 4:
                name = '.'.join(name.split(".")[-4:])
            self.service_types.add(name)
            self.service_browsers[name] = ServiceBrowser(zeroconf, name,
                                                         handlers=[self.on_service_state_change])
        elif state_change is ServiceStateChange.Removed:
            self.service_browsers.pop(name, None)

    def on_service_state_change(self, zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Added:
            info = zeroconf.get_service_info(service_type, name)
            if info:
                self.services[name] = info
        elif state_change is ServiceStateChange.Removed:
            self.services.pop(name, None)

    def clean_added_browser(self):
        mark_delete = []
        for browser in self.service_browsers_added:
            if browser not in self.service_browsers:
                try:
                    self.service_browsers_added[browser].cancel()
                    mark_delete.append(browser)
                except KeyError:
                    logger.error("Could not delete browser %s", browser)
        for browser in mark_delete:
            del self.service_browsers_added[browser]

    def update_list(self):
        # Update service types
        current_service_types = {self.service_type_widget.item(i).text() for i in
                                 range(self.service_type_widget.count())}
        new_service_types = self.service_types - current_service_types
        for service_type in new_service_types:
            self.service_type_widget.addItem(service_type)
        self.service_type_widget.sortItems()

        # Handle deselected service types
        for service_type in current_service_types - self.service_types:
            items = self.service_type_widget.findItems(service_type, Qt.MatchFlag.MatchExactly)
            for item in items:
                self.service_type_widget.takeItem(self.service_type_widget.row(item))

        # Select the filter item if still present
        selected_items = self.service_type_widget.findItems(self._filter, Qt.MatchFlag.MatchExactly)
        if selected_items:
            selected_items[0].setSelected(True)

        # === Optimized service list update ===
        filtered_services = {
            name: info for name, info in self.services.items()
            if self._filter in info.type
        }

        existing_items = {}
        for i in range(self.service_list_widget.count()):
            item = self.service_list_widget.item(i)
            name = item.text()
            existing_items[name] = item

        # Add or update entries
        for name, info in filtered_services.items():
            if name not in existing_items:
                new_item = QListWidgetItem(name)
                new_item.setData(Qt.ItemDataRole.UserRole, info)
                self.service_list_widget.addItem(new_item)
            else:
                item = existing_items[name]
                if item.data(Qt.ItemDataRole.UserRole) != info:
                    item.setData(Qt.ItemDataRole.UserRole, info)

        # Remove stale entries
        for name in set(existing_items) - set(filtered_services):
            item = existing_items[name]
            row = self.service_list_widget.row(item)
            self.service_list_widget.takeItem(row)

        self.service_list_widget.sortItems()
        self.clean_added_browser()


    def stop_browsers(self):
        try:
            mark_delete = []
            for browser in self.service_browsers:
                self.service_browsers[browser].cancel()
                mark_delete.append(browser)
            for browser in mark_delete:
                del self.service_browsers[browser]
        except KeyError:
            pass

    def rescan_network(self):
        self.zeroconf.close()
        self.stop_browsers()
        self.services.clear()
        self.service_types.clear()
        self.service_type_widget.clear()
        self.service_list_widget.clear()
        self._filter = ""
        self.service_filter.setText("Filter:")
        self.init_zeroconf()

    def closeEvent(self, event):
        self.timer.stop()
        self.zeroconf.close()
        self.stop_browsers()
        if self.browser:
            self.browser.cancel()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    logo = resource_path(get_os_logo())
    app.setWindowIcon(QIcon(logo))
    window = MDNSBrowser()
    window.show()
    sys.exit(app.exec())
