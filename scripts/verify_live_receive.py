"""
Live receive verification -- proves the running simulator is NOT deaf.

Run this from a SECOND process while the simulator (`python -m app.main`)
is already running. It reads config/network.json to find the simulator's
bind address/port, then:

  1. snapshots messages_in / messages_blocked via the REST API
     (http://127.0.0.1:8000/api/status),
  2. sends several real BACnet/IP ReadProperty requests from its own
     bacpypes3 client socket (a genuinely separate UDP endpoint, same
     approach as tests/test_bacnet_integration.py),
  3. re-snapshots the counters and reports whether messages_in climbed.

Why this exists: the startup duplicate-instance check broadcasts a Who-Is,
and on Windows a loopback broadcast is known to kill the asyncio UDP
transport (socket stays bound but deaf -- HANDOFF.md section 0/6). The
check is skipped on loopback binds, but its behavior on a REAL NIC bind
was unverified. Rerun this script on the bench after the wired NIC has
link and the simulator is bound to it:

    $env:PYTHONPATH='.'
    venv\Scripts\python scripts\verify_live_receive.py

PASS  -> messages_in climbed and reads returned values: transport alive.
FAIL  -> reads time out / messages_in flat: the device is deaf. Set
         "startup_duplicate_instance_check": false in config/network.json,
         restart the simulator, and rerun this script.

Exit code 0 on PASS, 1 on FAIL.
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from pathlib import Path

from bacpypes3.app import Application
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.networkport import NetworkPortObject
from bacpypes3.pdu import IPv4Address

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "network.json"
API_STATUS_URL = "http://127.0.0.1:8000/api/status"
CLIENT_PORT_OFFSET = 101  # client binds bind_address:(udp_port + 101)

# VAV-1 offset 11000; discharge_temp local instance 1 -> analog-input,11001.
# The device object itself is read too (objectName) for a second APDU shape.
READS = [
    ("analog-input,11001", "presentValue"),
    ("analog-input,11001", "objectName"),
    ("device,242000", "objectName"),
]


def api_counters() -> tuple[int, int]:
    with urllib.request.urlopen(API_STATUS_URL, timeout=5) as resp:
        data = json.loads(resp.read())
    bacnet = data.get("bacnet", data.get("network", {}))
    return int(bacnet.get("messages_in", -1)), int(bacnet.get("messages_blocked", -1))


async def run() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    server_ip, server_port = cfg["bind_address"], cfg["udp_port"]
    subnet_bits = cfg.get("subnet_bits", 24)
    client_port = server_port + CLIENT_PORT_OFFSET
    server_addr = f"{server_ip}:{server_port}"

    print(f"Simulator target : {server_addr}")
    print(f"Client endpoint  : {server_ip}:{client_port}")

    before_in, before_blocked = api_counters()
    print(f"Before: messages_in={before_in} messages_blocked={before_blocked}")

    dev = DeviceObject(objectIdentifier=("device", 599998), objectName="VerifyClient", vendorIdentifier=999)
    netport = NetworkPortObject(
        IPv4Address(f"{server_ip}/{subnet_bits}:{client_port}"),
        objectIdentifier=("network-port", 1),
        objectName="NetworkPort-1",
        networkNumber=0,
        networkNumberQuality="unknown",
    )
    client = Application.from_object_list([dev, netport])

    ok = 0
    try:
        for obj, prop in READS:
            try:
                value = await asyncio.wait_for(client.read_property(server_addr, obj, prop), timeout=5.0)
                print(f"  READ {obj} {prop} -> {value!r}")
                ok += 1
            except (asyncio.TimeoutError, TimeoutError):
                print(f"  READ {obj} {prop} -> TIMEOUT (no response)")
    finally:
        client.close()

    await asyncio.sleep(0.5)
    after_in, after_blocked = api_counters()
    print(f"After : messages_in={after_in} messages_blocked={after_blocked}")

    climbed = after_in > before_in
    print()
    if climbed and ok == len(READS):
        print(f"PASS: {ok}/{len(READS)} reads answered, messages_in climbed {before_in} -> {after_in}. Transport is alive.")
        return 0
    if climbed:
        print(f"PARTIAL: messages_in climbed {before_in} -> {after_in} but only {ok}/{len(READS)} reads answered.")
        return 1
    print("FAIL: messages_in did not climb -- the device looks DEAF. "
          "Set startup_duplicate_instance_check=false in config/network.json, restart, rerun.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
