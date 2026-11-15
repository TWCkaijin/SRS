"""Bounded ICMP echo from the monitoring server, never from a remote shell."""
import asyncio
import os
import re
import shutil
import sys


async def ping_host(hostname):
    # No shell or user-supplied options. Only registered host destinations are used.
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:%_-]*', hostname):
        return {'status': 'unavailable', 'ms': None}
    command = shutil.which('ping6' if sys.platform == 'darwin' and ':' in hostname else 'ping')
    if not command:
        return {'status': 'unavailable', 'ms': None}
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            command, '-n', '-c', '1', hostname,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, 'LC_ALL': 'C'},
        )
        async with asyncio.timeout(2):
            stdout, _ = await process.communicate()
        match = re.search(rb'time[=<]\s*([0-9]+(?:\.[0-9]+)?)\s*ms', stdout)
        if process.returncode == 0 and match:
            return {'status': 'ok', 'ms': float(match.group(1))}
        return {'status': 'timeout' if process.returncode in (1, 2) else 'unavailable', 'ms': None}
    except TimeoutError:
        return {'status': 'timeout', 'ms': None}
    except OSError:
        return {'status': 'unavailable', 'ms': None}
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
