"""Readable UPnP device descriptions and labels."""

import re


def upnp_type_label(device_type: str) -> str:
    """Keep the raw discovery target as data, but give it a readable label."""
    if device_type == "upnp:rootdevice":
        return "Root devices"
    parts = device_type.split(":", 4)
    if len(parts) != 5 or parts[0] != "urn":
        return device_type
    _, _, kind, name, version = parts
    if kind == "device" and name.endswith("Device"):
        name = name[:-6]
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    name = name.replace("-", " ").replace("_", " ")
    if name.islower():
        name = name.capitalize()
    suffix = f"service, v{version}" if kind == "service" else f"v{version}"
    return f"{name} ({suffix})"


def parse_upnp_description(xml: str) -> list[dict]:
    """Read each device's own metadata, including embedded devices."""
    # This parser is also a dependency of async-upnp-client. Import it only
    # when resolving UPnP, so mDNS remains usable without the optional client.
    from defusedxml.ElementTree import fromstring

    root = fromstring(xml)
    fields = {
        "friendlyName": "friendly_name",
        "manufacturer": "manufacturer",
        "modelName": "model_name",
        "modelNumber": "model_number",
        "modelDescription": "model_description",
        "serialNumber": "serial_number",
        "deviceType": "device_type",
        "UDN": "udn",
        "presentationURL": "presentation_url",
    }
    devices = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "device":
            continue
        metadata = {}
        for child in element:
            key = fields.get(child.tag.rsplit("}", 1)[-1])
            if key and child.text and child.text.strip():
                metadata[key] = child.text.strip()
        devices.append(metadata)
    return devices


def upnp_device_type(info: dict) -> str:
    """Prefer the description's device type over an arbitrary SSDP service."""
    if info.get("device_type"):
        return upnp_type_label(info["device_type"])
    types = info.get("types", [info.get("type", "")])
    for device_type in sorted(types):
        if ":device:" in device_type:
            return upnp_type_label(device_type)
    return "UPnP device"


def upnp_device_title(info: dict) -> str:
    return (
        info.get("friendly_name")
        or info.get("model_name")
        or info.get("host")
        or info.get("name")
        or "UPnP device"
    )


def upnp_device_summary(info: dict) -> str:
    model = " ".join(filter(None, (info.get("model_name"), info.get("model_number"))))
    manufacturer = info.get("manufacturer", "")
    if manufacturer and manufacturer.casefold() not in model.casefold():
        model = f"{manufacturer} {model}".strip()
    parts = [model, upnp_device_type(info), info.get("host", "")]
    return " · ".join(dict.fromkeys(part for part in parts if part))
