import os
import platform
import psutil
import time

start_time = time.time()

def get_system_info():
    uptime = time.time() - start_time
    return {
        "os": platform.system(),
        "release": platform.release(),
        "python": platform.python_version(),
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "memory": psutil.virtual_memory().percent,
        "uptime_seconds": int(uptime),
        "hostname": platform.node(),
    }
