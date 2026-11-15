"""Fixed read-only cluster probe and paired evidence validation.

Building a script never connects. The executor must run each script only as an
approved step, and callers must bind results to that succeeded execution before
persisting them. The probe reads an existing NFS witness and opens a bounded TCP
connection to each selected peer's Slurm daemon; it never writes shared files.
"""

import hashlib
import ipaddress
import json
import re
from datetime import datetime


def validate_probe_spec(spec: dict) -> dict:
    if not isinstance(spec, dict) or spec.get("schema_version") != 1:
        raise ValueError("Cluster probe requires schema_version 1")
    probe_id = spec.get("probe_id", "")
    if not isinstance(probe_id, str) or not re.fullmatch(r"[a-f0-9]{32}", probe_id):
        raise ValueError("probe_id must be a fresh 32-hex challenge identifier")
    storage = spec.get("storage", {})
    expected_path = f"/srv/srs/.srs-probes/{probe_id}.json"
    if not (isinstance(storage, dict) and storage.get("root") == "/srv/srs"
            and storage.get("witness_path") == expected_path
            and isinstance(storage.get("witness_sha256"), str)
            and re.fullmatch(r"[a-f0-9]{64}", storage["witness_sha256"])):
        raise ValueError("A fixed /srv/srs witness path and trusted SHA256 are required")
    source = storage.get("source", "")
    if not isinstance(source, str) or not re.fullmatch(r"[0-9.]+:/[A-Za-z0-9_./-]+", source):
        raise ValueError("Shared source must identify an IPv4 NFS export, not a local directory")
    server, export = source.split(":", 1)
    _address(server)
    if ".." in export.split("/") or "//" in export:
        raise ValueError("Invalid NFS export path")
    peers = spec.get("peers", [])
    if not isinstance(peers, list) or len(peers) != 2:
        raise ValueError("The pilot requires exactly two selected peers")
    ids, addresses = [], []
    cleaned_peers = []
    for peer in peers:
        if not isinstance(peer, dict):
            raise ValueError("Invalid peer")
        node_id, interface = peer.get("id", ""), peer.get("interface", "")
        if not (isinstance(node_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", node_id)
                and isinstance(interface, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", interface)):
            raise ValueError("Peer id and interface must be exact reviewed values")
        address = _address(peer.get("address"))
        if peer.get("port", 6818) != 6818:
            raise ValueError("The read-only pilot probe only tests the Slurm daemon TCP port 6818")
        ids.append(node_id)
        addresses.append(address)
        cleaned_peers.append({"id": node_id, "address": address, "interface": interface, "port": 6818})
    if len(set(ids)) != 2 or len(set(addresses)) != 2:
        raise ValueError("Peer identities and addresses must be distinct")
    return {"schema_version": 1, "probe_id": probe_id,
            "storage": {"root": "/srv/srs", "source": source, "witness_path": expected_path,
                        "witness_sha256": storage["witness_sha256"]}, "peers": cleaned_peers}


def _address(value) -> str:
    try:
        address = ipaddress.ip_address(value)
    except (ValueError, TypeError) as error:
        raise ValueError("Peer and NFS addresses must be literal IPv4 addresses") from error
    if address.version != 4 or address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local:
        raise ValueError("Probe addresses must be usable non-loopback IPv4 addresses")
    return str(address)


def cluster_probe_script(spec: dict, node_id: str) -> str:
    cleaned = validate_probe_spec(spec)
    if node_id not in {peer["id"] for peer in cleaned["peers"]}:
        raise ValueError("Probe host must be one of the reviewed peers")
    # JSON is embedded as one quoted Python literal. No value becomes Python code.
    encoded = repr(json.dumps({"spec": cleaned, "node_id": node_id}, sort_keys=True))
    return "INPUT = " + encoded + "\n" + _SCRIPT


_SCRIPT = r'''import hashlib, json, os, pathlib, socket, stat, subprocess
request = json.loads(INPUT)
spec, node_id = request["spec"], request["node_id"]
mine = next(peer for peer in spec["peers"] if peer["id"] == node_id)
def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return {"exit_code": result.returncode, "stdout": result.stdout[:65536], "stderr": result.stderr[:4096]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"error": type(error).__name__}
result = {"kind": "cluster_probe", "schema_version": 1, "node_id": node_id, "probe_id": spec["probe_id"],
          "spec_sha256": hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
          "mount": command(["findmnt", "--json", "--target", "/srv/srs", "--output", "TARGET,SOURCE,FSTYPE,OPTIONS"]),
          "addresses": command(["ip", "-j", "address", "show", "dev", mine["interface"]]),
          "witness": {}, "connections": {}}
path = pathlib.Path(spec["storage"]["witness_path"])
try:
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise ValueError("symlink")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65536:
            raise ValueError("not a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("witness too large")
        result["witness"] = {"sha256": hashlib.sha256(raw).hexdigest(), "content": json.loads(raw)}
    finally:
        os.close(fd)
except (OSError, ValueError, UnicodeDecodeError) as error:
    result["witness"] = {"error": type(error).__name__}
for peer in spec["peers"]:
    if peer["id"] == node_id:
        continue
    entry = {"address": peer["address"], "port": peer["port"],
             "route": command(["ip", "-j", "route", "get", peer["address"], "from", mine["address"]])}
    try:
        with socket.create_connection((peer["address"], peer["port"]), timeout=3,
                                      source_address=(mine["address"], 0)) as connection:
            entry["connected"] = True
            entry["local_address"] = connection.getsockname()[0]
            entry["peer_address"] = connection.getpeername()[0]
    except OSError as error:
        entry.update(connected=False, error=type(error).__name__)
    result["connections"][peer["id"]] = entry
print(json.dumps(result, sort_keys=True))
'''


def _json_command(observation: dict, key: str):
    record = observation.get(key, {})
    if not isinstance(record, dict) or record.get("exit_code") != 0:
        raise ValueError("Cluster command failed: " + key)
    try:
        return json.loads(record["stdout"])
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError("Malformed cluster command output: " + key) from error


def parse_cluster_evidence(observations_by_node: dict, spec: dict, *, execution_id: str,
                           validated_at: str) -> dict[str, dict]:
    """Validate both actual probe outputs; no input 'verified' flag is trusted.

    The caller authenticates execution provenance before storing returned facts.
    This confirms the same mounted NFS export/witness and bilateral TCP/6818.
    NCCL collectives and checkpoint write visibility remain separate real tests.
    """
    cleaned = validate_probe_spec(spec)
    if not isinstance(execution_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", execution_id):
        raise ValueError("Evidence requires an actual execution id")
    try:
        timestamp = datetime.fromisoformat(validated_at.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Evidence requires an explicit UTC-aware validation timestamp") from error
    peer_ids = sorted(peer["id"] for peer in cleaned["peers"])
    if not isinstance(observations_by_node, dict) or set(observations_by_node) != set(peer_ids):
        raise ValueError("Both selected peers must finish the same approved probe")
    spec_sha = hashlib.sha256(json.dumps(cleaned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    facts = {}
    for mine in cleaned["peers"]:
        node_id = mine["id"]
        observation = observations_by_node[node_id]
        if not (isinstance(observation, dict) and observation.get("kind") == "cluster_probe"
                and observation.get("schema_version") == 1 and observation.get("node_id") == node_id
                and observation.get("probe_id") == cleaned["probe_id"] and observation.get("spec_sha256") == spec_sha):
            raise ValueError("Probe output is not bound to the exact approved peer/specification")
        mounts = _json_command(observation, "mount")
        mounts = mounts.get("filesystems", []) if isinstance(mounts, dict) else []
        if not (len(mounts) == 1 and isinstance(mounts[0], dict) and mounts[0].get("target") == "/srv/srs"
                and mounts[0].get("source") == cleaned["storage"]["source"]
                and mounts[0].get("fstype") in {"nfs", "nfs4"}
                and "rw" in str(mounts[0].get("options", "")).split(",")):
            raise ValueError("Selected peers must observe the same read/write NFS export at /srv/srs")
        witness = observation.get("witness", {})
        content = witness.get("content", {}) if isinstance(witness, dict) else {}
        if not (witness.get("sha256") == cleaned["storage"]["witness_sha256"] and isinstance(content, dict)
                and content.get("probe_id") == cleaned["probe_id"] and content.get("peer_ids") == peer_ids
                and content.get("source") == cleaned["storage"]["source"]
                and isinstance(content.get("nonce"), str) and re.fullmatch(r"[a-f0-9]{64}", content["nonce"])):
            raise ValueError("The pre-existing shared witness does not match the reviewed challenge")
        addresses = _json_command(observation, "addresses")
        if not isinstance(addresses, list) or not any(
            isinstance(item, dict) and item.get("ifname") == mine["interface"]
            and any(address.get("local") == mine["address"] and address.get("family") == "inet"
                    for address in item.get("addr_info", []) if isinstance(address, dict)) for item in addresses
        ):
            raise ValueError("Approved local address is absent from the approved interface")
        for peer in cleaned["peers"]:
            if peer["id"] == node_id:
                continue
            connection = observation.get("connections", {}).get(peer["id"], {})
            if not (isinstance(connection, dict) and connection.get("connected") is True
                    and connection.get("address") == peer["address"] and connection.get("peer_address") == peer["address"]
                    and connection.get("port") == 6818 and connection.get("local_address") == mine["address"]):
                raise ValueError("Bilateral approved-interface TCP/6818 connectivity was not observed")
            routes = _json_command(connection, "route")
            if not isinstance(routes, list) or not routes or not all(
                isinstance(route, dict) and route.get("dev") == mine["interface"]
                and route.get("prefsrc", route.get("src", mine["address"])) == mine["address"] for route in routes
            ):
                raise ValueError("Peer traffic does not use the approved interface and source address")
        facts[node_id] = {"execution_id": execution_id, "validated_at": validated_at,
                          "probe_spec_sha256": spec_sha,
                          "shared_storage": {"verified": True, "root": "/srv/srs", "source": cleaned["storage"]["source"],
                                             "peer_ids": peer_ids, "probe_id": cleaned["probe_id"],
                                             "scope": "same_nfs_export_and_preexisting_witness",
                                             "write_visibility_tested": False},
                          "network": {"verified": True, "address": mine["address"], "interface": mine["interface"],
                                      "peer_ids": peer_ids, "probe_id": cleaned["probe_id"],
                                      "tested_tcp_ports": [6818], "nccl_collective_tested": False}}
    return facts
