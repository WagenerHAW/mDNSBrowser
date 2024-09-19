import os
import platform
import sys

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

try:
    from ctypes import windll  # Only exists on Windows.
    myappid = 'haw.mcastmanager.gui.alpha'
    windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except ImportError:
    pass


def check_os():
    # Get the OS name using platform.system()
    os_name = platform.system()

    if os_name == "Linux":
        return "Linux"
    elif os_name == "Darwin":
        return "macOS"
    elif os_name == "Windows":
        return "Windows"
    else:
        return "Unknown OS"

def get_os_logo():
    logo_os = check_os()
    if logo_os == "macOS":
        return "assets/default_icon.icns"
    elif logo_os == "Windows":
        return "assets\\default_icon.ico"
    else:
        return "assets/default_icon.png"