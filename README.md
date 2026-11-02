# mDNSBrowser

A desktop application for discovering and inspecting **mDNS / DNS-SD services** and
**UPnP / SSDP devices** on local networks. Browse services, identify devices, and
inspect discovery metadata from a single interface, with built-in queries for
Dante audio networks.

Built with Python, PyQt6, `python-zeroconf`, and `async-upnp-client` for macOS,
Windows, and Linux.

## Features

### mDNS / DNS-SD

- Discover advertised service types automatically and filter services by type.
- Inspect hostnames, addresses, ports, TXT records, priority, and weight.
- Query service types manually, with automatic completion of the `.local.` suffix.
- Start browsing common Dante service types with one click.

### UPnP / SSDP

- Discover devices through recurring SSDP searches, including routers, media
  renderers, and other UPnP devices.
- Identify devices by friendly name, manufacturer, model, device type, and address.
- Search by name, model, manufacturer, or address; combine search with readable
  device and service type filters, or browse **All devices**.
- Group advertisements by device ID while retaining each advertisement's SSDP
  headers for inspection. Embedded devices keep their own identities and names.
- Inspect available model details, serial numbers, device IDs, and presentation
  URLs in a scrollable details dialog with selectable text.

### Network selection

Browse mDNS and UPnP results in separate tabs. Select a specific network interface
or use **All interfaces**, then clear results and restart discovery at any time.
Discovery runs in the background to keep the interface responsive.

Network interfaces are checked in the background every two seconds. When an
adapter appears, disappears, goes up or down, or changes its addresses or network
configuration, the interface list updates and mDNS and UPnP discovery restart with
cleared results. The selected adapter stays selected through an address change;
if it becomes unavailable, selection falls back to **All interfaces**. Only active
adapters with an IPv4 address appear as individual choices.

For UPnP, **All interfaces** searches active IPv4 multicast interfaces concurrently,
including Wi-Fi and Ethernet. A failed search on one adapter does not prevent
discovery on the others. UPnP discovery currently uses IPv4.

## Getting started

Use **Python 3.11 or later** and a desktop environment. Runtime dependencies are
listed in [requirements.txt](requirements.txt).

Run the following commands from the project root.

### macOS and Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python src/app.py
```

### Windows (PowerShell)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe src\app.py
```

### Poetry

With Poetry 2.0 or later, you can install the locked runtime dependencies instead:

```bash
poetry install --only main
poetry run python src/app.py
```

Poetry manages dependencies only; the application runs directly from `src/app.py`.
Commit `poetry.lock` when changing dependencies. Keep the runtime version ranges
in `requirements.txt` aligned with `pyproject.toml`.

## Usage

Discovery starts when the application opens.

1. Select **All interfaces** or a specific adapter and IPv4 address.
2. Open the **mDNS** or **UPnP** tab.
3. Select a type in the left panel to filter the results on the right. In the
   **UPnP** tab, use the search field to narrow the list or choose **All devices**.
   Click the selected mDNS type again to clear its filter.
4. Click a service or device to inspect its details.
5. Use **Clear and rescan Network** to discard results and restart discovery.
   Changing the selected interface also clears results and restarts discovery.

### Manual mDNS queries and Dante discovery

In the **mDNS** tab, enter a service type under **Manual mDNS Service Query** and
click **Query mDNS Service**. For example, `_http._tcp` is normalized to
`_http._tcp.local.`. This also lets you browse service types that were not returned
by automatic service type enumeration.

Use **Add Dante mDNS Services** to browse these built-in service types:

```text
_dante-safe._udp.local.
_dante-upgr._udp.local.
_netaudio-arc._udp.local.
_netaudio-chan._udp.local.
_netaudio-cmc._udp.local.
_netaudio-dbc._udp.local.
_dante-ddm-d._udp.local.
_dante-ddm-c._tcp.local.
```

### UPnP device details

Devices appear as SSDP responses arrive. Names and model information are loaded
automatically from device descriptions; devices with unavailable descriptions
remain visible using their address. The details dialog shows available metadata
and the original headers for each advertisement associated with the device.

UPnP searches repeat automatically after a 30-second pause between search rounds.
Use **Clear and rescan Network** to start a fresh search immediately.

## Building the macOS application

After installing runtime dependencies in the project's `.venv`, install
PyInstaller and run the build script from the project root:

```bash
.venv/bin/python -m pip install pyinstaller
bash spec/build_macos.sh
```

For a Poetry setup, use Python 3.11–3.15, create `.venv` as shown above, and install
the optional build group before running the same script:

```bash
poetry install --only main,build
bash spec/build_macos.sh
```

The application bundle is written to `dist/mDNSBrowser.app`. The script bundles
the application modules and assets and can also be run from inside `spec` with
`bash build_macos.sh`.

To inspect startup errors, launch the bundled executable from Terminal:

```bash
./dist/mDNSBrowser.app/Contents/MacOS/mDNSBrowser
```

## Troubleshooting

### No services or devices appear

Confirm that the selected interface is connected to the intended network and that
devices are advertising services. Local multicast traffic must be allowed by the
host firewall and network. Try selecting the adapter explicitly, then rescan.

### macOS local network access

If discovery stays empty or multicast sends report `[Errno 65] No route to host`,
check **System Settings → Privacy & Security → Local Network**. Enable access for
**mDNSBrowser** when running the packaged app, or for the responsible IDE, such as
**PyCharm**, when launching from there. Fully quit and reopen that app, then rescan.

To compare behavior outside the IDE, run this command from the project root in
Terminal:

```bash
.venv/bin/python src/app.py
```

macOS tracks local network access for the responsible application. A successful
Terminal run does not establish that an IDE has permission. See
[Apple's local network privacy documentation](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy).

## Development

The discovery worker runs an `asyncio` event loop in a background thread and sends
results to the PyQt6 interface through Qt signals. mDNS browsing uses
`AsyncZeroconf`; UPnP discovery uses `async-upnp-client`. UPnP description requests
share in-flight work and cached results, with a four-second request timeout.

`poetry install` also installs the development tools. Run the tests with pytest:

```bash
QT_QPA_PLATFORM=offscreen poetry run pytest
```

Run the discovery and UI regression tests on macOS or Linux without network access
or a running desktop:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s test -p 'test_*.py'
```

### Project structure

```text
src/
  app.py                 # PyQt6 interface and asynchronous discovery worker
  upnp.py                # UPnP description parsing and device labels
  os_qt_tools.py         # Platform icons and resource paths
spec/
  build_macos.sh         # macOS application build script
  mDNSBrowser_macos.spec # macOS PyInstaller specification
  mDNSBrowser_win.spec   # Windows PyInstaller specification
test/
  test_upnp.py           # Discovery, device metadata, and UI regression tests
  service.py             # Manual mDNS test service publisher
assets/                  # Application icons and screenshots
requirements.txt         # Runtime dependencies
```

## Screenshots

These screenshots show the earlier mDNS interface. The current application adds
separate mDNS and UPnP tabs and the device browsing features described above.

![mDNS service browsing in an earlier interface version](assets/demo.png)

![Dante mDNS service details in an earlier interface version](assets/specs.png)

## License

MIT © 2026 Thorsten Wagener
