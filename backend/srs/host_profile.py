"""Read-only hardware inventory captured on first successful observation."""
from pydantic import Field

from srs.schemas import StrictModel

# Appended to MONITOR_PROBE; reuses its memory/GPU readings and imports.
PROFILE_PROBE = r'''
import os, platform, socket
cpu_info = pathlib.Path('/proc/cpuinfo').read_text()
model = None
cores = set()
for block in cpu_info.split('\n\n'):
    fields = dict(line.split(':', 1) for line in block.splitlines() if ':' in line)
    fields = {k.strip(): v.strip() for k, v in fields.items()}
    model = model or fields.get('model name') or fields.get('Processor')
    if 'physical id' in fields and 'core id' in fields:
        cores.add((fields['physical id'], fields['core id']))
os_name = platform.system()
try:
    for line in pathlib.Path('/etc/os-release').read_text().splitlines():
        if line.startswith('PRETTY_NAME='):
            os_name = line.split('=', 1)[1].strip('"')
except OSError:
    pass
disks, disk_error = [], None
try:
    r = subprocess.run(['lsblk', '--json', '--bytes', '--output', 'NAME,MODEL,SIZE,TYPE,ROTA,TRAN,MOUNTPOINT'], capture_output=True, text=True, timeout=4)
    if r.returncode:
        disk_error = 'lsblk 無法取得磁碟清單'
    else:
        def walk(items):
            for item in items:
                if item.get('type') == 'disk':
                    mounts = []
                    def mountpoints(part):
                        if part.get('mountpoint'):
                            mounts.append(part['mountpoint'])
                        for child in part.get('children', []):
                            mountpoints(child)
                    mountpoints(item)
                    disks.append(dict(name=item['name'], model=(item.get('model') or '').strip(), size_bytes=int(item.get('size') or 0), rotational=bool(item.get('rota')), transport=item.get('tran'), mountpoints=mounts[:64]))
                walk(item.get('children', []))
        walk(json.loads(r.stdout).get('blockdevices', []))
except (OSError, ValueError, subprocess.TimeoutExpired):
    disk_error = 'lsblk 不可用或逾時'
interfaces = []
try:
    r = subprocess.run(['ip', '-j', 'address', 'show'], capture_output=True, text=True, timeout=3)
    if r.returncode == 0:
        for item in json.loads(r.stdout):
            if item.get('ifname') == 'lo':
                continue
            interfaces.append(dict(name=item['ifname'], mac=item.get('address'), mtu=item.get('mtu'), addresses=[str(a.get('local')) + '/' + str(a.get('prefixlen')) for a in item.get('addr_info', [])]))
except (OSError, ValueError, subprocess.TimeoutExpired):
    pass
print(json.dumps(dict(hostname=socket.gethostname(), os=os_name, kernel=platform.release(), architecture=platform.machine(), cpu_model=model or 'Unknown', logical_cpus=os.cpu_count() or 1, physical_cores=len(cores) or None, memory_total_mb=mem['MemTotal'], gpus=gpus, disks=disks[:128], interfaces=interfaces[:128], disk_error=disk_error, gpu_error=gpu_error)))
'''


class DiskProfile(StrictModel):
    name: str = Field(max_length=128)
    model: str = Field(max_length=256)
    size_bytes: int = Field(ge=0)
    rotational: bool
    transport: str | None = Field(default=None, max_length=64)
    mountpoints: list[str] = Field(max_length=64)


# GPU profile schema is attached in monitor.py to avoid a circular import.
class InterfaceProfile(StrictModel):
    name: str = Field(max_length=128)
    mac: str | None = Field(default=None, max_length=128)
    mtu: int | None = Field(default=None, ge=0)
    addresses: list[str] = Field(default_factory=list, max_length=64)


class HardwareProfile(StrictModel):
    hostname: str = Field(max_length=255)
    os: str = Field(max_length=255)
    kernel: str = Field(max_length=255)
    architecture: str = Field(max_length=128)
    cpu_model: str = Field(max_length=256)
    logical_cpus: int = Field(ge=1)
    physical_cores: int | None = Field(default=None, ge=1)
    memory_total_mb: float = Field(gt=0, allow_inf_nan=False)
    gpus: list[dict] = Field(max_length=128)
    interfaces: list[InterfaceProfile] = Field(default_factory=list, max_length=128)
    disks: list[DiskProfile] = Field(max_length=128)
    disk_error: str | None = Field(default=None, max_length=200)
    gpu_error: str | None = Field(default=None, max_length=200)
