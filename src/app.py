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

from PyQt6.QtWidgets import (QApplication, QMainWindow, QListWidget, QListWidgetItem,
                             QVBoxLayout, QHBoxLayout, QGridLayout, QWidget, QDialog, QPushButton,
                             QLabel, QLineEdit)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QIcon

from zeroconf import ServiceBrowser, Zeroconf, ServiceStateChange
from typing import cast

import logging
logger = logging.getLogger(__name__)

from os_qt_tools import *

class MDNSBrowser(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle('mDNS Service Browser')
        self.setGeometry(100, 100, 600, 400)

        # UI Setup
        self.service_type_widget_hl = QLabel('Query _services._dns-sd._udp.local results:')
        self.service_type_widget = QListWidget()
        self.service_filter = QLabel()
        self.service_filter.setText("Filter:")
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
        self.main_layout.addLayout(self.layout)
        self.main_layout.addWidget(self.service_filter)

        # Add Rescan Button
        self.rescan_button = QPushButton("Rescan Network")
        self.rescan_button.clicked.connect(self.rescan_network)

        self.main_layout.addWidget(self.rescan_button)  # Add button to layout

        # Add Manual Query Section
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

        # Zeroconf Setup
        self.services = {}
        self.service_types = set()
        self._filter = ""
        self.zeroconf = Zeroconf()
        # Create main Browser for mDNS
        self.browser = ServiceBrowser(self.zeroconf, "_services._dns-sd._udp.local.",
                                      handlers=[self.on_service_discovered])
        # Dict for all service browsers
        self.service_browsers = {}
        self.service_browsers_added = {}

        # Timer for updating the list
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_list)
        self.timer.start(1000)

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
        """Handle manual service queries."""
        service_query = self.manual_query_input.text().strip()
        try:
            if service_query:
                self.service_browsers_added[service_query]=ServiceBrowser(self.zeroconf, f"{service_query}",
                               handlers=[self.on_service_discovered])

            else:
                raise ValueError("Service query cannot be empty.\nThe query should look something like:\n\n\t_netaudio-cmc._udp.local.")
        except Exception as e:
            self.show_error_dialog(f"{str(e) : ^10}")


    def show_error_dialog(self, message):
        """Display an error dialog with the specified message."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Error")
        layout = QVBoxLayout()
        error_label = QLabel(message)
        error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(error_label)
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

    def on_service_discovered(self, zeroconf, service_type, name, state_change):
        '''
        Checks for services on _services._dns-sd._udp
        :return
        '''
        if state_change is ServiceStateChange.Added:
            # Fix wrong ".local" base domain 'local. _homeconnect._tcp.' instead of '. _tcp.local. _homeconnect.'
            if len(name.split(".")) > 4:
                name = '.'.join(name.split(".")[-4:])
            # Add Service to Set
            self.service_types.add(name)
            # Create browser for service type
            self.service_browsers[name] = ServiceBrowser(zeroconf, name,
                                                         handlers=[self.on_service_state_change])
        elif state_change is ServiceStateChange.Removed:
            self.service_browsers.pop(name, None)

    def on_service_state_change(self, zeroconf, service_type, name, state_change):
        '''
        Adds discovered services to self.services[]
        '''
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
                    print("Oops")
        for browser in mark_delete:
            del self.service_browsers_added[browser]


        print(self.service_browsers_added)
        print(self.service_browsers)



    def update_list(self):
        # Update service_type_widget

        current_service_types = {self.service_type_widget.item(i).text() for i in
                                 range(self.service_type_widget.count())}

        check = self.service_types - current_service_types
        if len(check) > 0:
            # Add new service types
            for service_type in check:
                self.service_type_widget.addItem(service_type)
            else:
                self.service_type_widget.sortItems()

        # Remove old service types
        for service_type in current_service_types - self.service_types:
            items = self.service_type_widget.findItems(service_type, Qt.MatchFlag.MatchExactly)
            for item in items:
                row = self.service_type_widget.row(item)
                self.service_type_widget.takeItem(row)

        # Update the filter selection
        selected_items = self.service_type_widget.findItems(self._filter, Qt.MatchFlag.MatchContains)
        if len(selected_items) == 1:
            selected_items[0].setSelected(True)

        # Update service_list_widget
        # Create a set of current services
        current_services = {self.service_list_widget.item(i).text() for i in range(self.service_list_widget.count())}
        # Create a set of old and new services
        #new_services = {name.rstrip(info.type) for name, info in self.services.items() if self._filter in info.type}
        # Create a filtered list of all services with the selected service filter
        #filtered_list = {name.rstrip(info.type): info for name, info in self.services.items() if
        #                 self._filter in info.type}

        new_services = {name for name, info in self.services.items() if self._filter in info.type}
        # Create a filtered list of all services with the selected service filter
        filtered_list = {name: info for name, info in self.services.items() if
                         self._filter in info.type}
        # Add new services
        check = new_services - current_services
        if len(check) > 0:  # check if new services need to be added
            for service in check:
                # Create empty item
                item = QListWidgetItem(service)
                # Get extra data from service
                data = filtered_list.get(service)
                # Add extra data to item
                item.setData(Qt.ItemDataRole.UserRole, data)
                # Add the item to the list
                self.service_list_widget.addItem(item)
            else:
                # When done sort the list
                self.service_list_widget.sortItems()

        # Remove old services
        for service in current_services - new_services:
            items = self.service_list_widget.findItems(service, Qt.MatchFlag.MatchExactly)
            for item in items:
                row = self.service_list_widget.row(item)
                self.service_list_widget.takeItem(row)

        self.clean_added_browser()

    def stop_browsers(self):
        mark_delete = []
        for browser in self.service_browsers:
            self.service_browsers[browser].cancel()
            mark_delete.append(browser)
        for browser in mark_delete:
            del self.service_browsers[browser]

    def stop_added_browser(self):
        for browser in self.service_browsers_added:
            self.service_browsers_added[browser].cancel()
            del self.service_browsers[browser]

    def rescan_network(self):
        """Rescan the network by restarting the Zeroconf browser."""
        # Stop the current service browsers
        self.stop_browsers()
        self.stop_added_browser()
        # Stop main browser
        self.browser.cancel()
        self.zeroconf.close()

        # Clear current data
        self.services.clear()
        self.service_types.clear()
        self.service_type_widget.clear()
        self.service_list_widget.clear()
        self._filter = ""
        self.service_filter.setText(f"Filter: {self._filter}")

        # Restart Zeroconf and the main service browser
        self.zeroconf = Zeroconf()
        self.browser = ServiceBrowser(self.zeroconf, "_services._dns-sd._udp.local.",
                                      handlers=[self.on_service_discovered])

    def closeEvent(self, event):
        self.timer.stop()
        self.browser.cancel()
        self.zeroconf.close()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    logo = resource_path(get_os_logo())
    app.setWindowIcon(QIcon(logo))
    window = MDNSBrowser()
    window.show()
    sys.exit(app.exec())
