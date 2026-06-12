# mDNS and UPnP Service Browser (Async, PyQt6)

A cross-platform **mDNS / DNS-SD and UPnP / SSDP service discovery tool** built with **PyQt6**, Python’s **asyncio Zeroconf API**, and `async-upnp-client`.
It allows you to browse, filter, and inspect network services and UPnP devices (including **Dante audio devices**) in real time.

![screenshot](assets/demo.png)
![details-view](assets/specs.png)

---

## ✨ Features

- 🔍 **Automatic discovery** of all mDNS service types (`_services._dns-sd._udp.local.`)  
- 📡 **UPnP / SSDP discovery** for devices such as media renderers, routers, and smart devices
- 🎚️ **Filter and browse** mDNS services and UPnP devices in separate tabs
- 📜 **Detailed service info** including addresses, TXT properties, SSDP headers, priority, weight
- 🌐 **Interface selection** – scan on a specific interface or all interfaces  
- 🔄 **Rescan & refresh** with one click  
- 📝 **Manual service query** with auto-correction for `.local.` suffix  
- 🎵 **Quick Dante queries** for `_netaudio._udp.local.` etc.  
- ✅ Cross-platform (Linux, macOS, Windows)

---

## 🚀 Installation

### Requirements
- Python **3.9+**
- [python-zeroconf](https://github.com/jstasiak/python-zeroconf) (newer versions with `AsyncZeroconf`)
- [async-upnp-client](https://github.com/StevenLooman/async_upnp_client)
- [PyQt6](https://pypi.org/project/PyQt6/)
- [psutil](https://pypi.org/project/psutil/)

### Install dependencies
```bash
pip install -r requirements
```

---

## ▶️ Usage

Run the app:

```bash
python src/app.py
```

or build an executable with the spec files 

### Basic workflow
1. Select an interface (`All interfaces` or a specific adapter/IP).
2. Use the **mDNS** and **UPnP** tabs to switch between discovery methods.
3. Select a type to filter and view discovered **services or devices** on the right panel.
4. Click a service to open a detailed info dialog with TXT records or SSDP headers.
5. In the **mDNS** tab, use **Manual mDNS Service Query** to search for a service type (input auto-corrects `.local.`).
6. In the **mDNS** tab, use **Add Dante mDNS Services** to query common Dante services.

---

## 🛠️ Development Notes

- Uses `AsyncZeroconf` for async mDNS service browsing.
- Uses `async-upnp-client` to run recurring `ssdp:all` searches for UPnP devices.
- Runs an `asyncio` event loop in a background thread.  
- Communicates with the PyQt6 main thread via Qt signals for **thread-safe UI updates**.  
- Service resolution uses `async_get_service_info()` with a timeout of **3s**, retriable if needed.  
- Input normalization ensures user queries work even if `.local.` is missing.  

---

## 📦 Project Structure
```
src/
  app.py         # Main PyQt6 app and AsyncZCWorker
README.md
```

---

## 🔮 Roadmap
- [ ] Add **collapsible properties section** in service info dialog  
- [ ] Retry resolution for slow-responding devices  
- [ ] Configurable list of **favorite service types** (like Dante)  
- [ ] Dark/light theme toggle  

---

## 📜 License
MIT © 2025 Thorsten Wagener 
