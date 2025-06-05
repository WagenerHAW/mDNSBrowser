from zeroconf import ServiceInfo, Zeroconf
import socket

# Define the service info
service_type = "_http._tcp.local."
service_name = "Demo Web Server._http._tcp.local."
service_port = 8080
service_desc = {'path': '/'}

# Get the local IP address
hostname = socket.gethostname()
local_ip = socket.gethostbyname(hostname)

# Create the service info object
info = ServiceInfo(
    service_type,
    service_name,
    addresses=[socket.inet_aton(local_ip)],
    port=service_port,
    properties=service_desc,
    server=f"{hostname}.local."
)

# Register the service
zeroconf = Zeroconf()
zeroconf.register_service(info)

try:
    print(f"Service {service_name} registered on {local_ip}:{service_port}")
    input("Press enter to exit...\n\n")
finally:
    zeroconf.unregister_service(info)
    zeroconf.close()

