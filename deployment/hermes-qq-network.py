#!/usr/bin/python3 -I
"""Dedicated sandbox egress. Never flush or alter other applications' firewall rules."""
import fcntl
import json
import ipaddress
import subprocess
from pathlib import Path
NETWORK = "hermes-qq-sandbox"
BRIDGE = "hqq-sbx"
SUBNET = "172.30.253.0/24"
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}
BLOCKED = ["0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
           "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
           "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
           "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4"]
def run(argv, **kwargs):
    return subprocess.run(argv, env=ENV, check=True, capture_output=True, text=True, **kwargs)
def ensure():
    lock = Path("/run/hermes-qq-network.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    inspect = subprocess.run(["/usr/bin/docker", "network", "inspect", NETWORK],
                             env=ENV, capture_output=True, text=True)
    if inspect.returncode:
        run(["/usr/bin/docker", "network", "create", "--driver", "bridge", "--subnet", SUBNET,
             "--opt", "com.docker.network.bridge.name="+BRIDGE,
             "--opt", "com.docker.network.bridge.enable_icc=false",
             "--opt", "com.docker.network.bridge.host_binding_ipv4=127.0.0.1", NETWORK])
    data = json.loads(run(["/usr/bin/docker", "network", "inspect", NETWORK]).stdout)[0]
    if (data["Driver"] != "bridge" or data.get("EnableIPv6") or
        data["Options"].get("com.docker.network.bridge.name") != BRIDGE or
        data["Options"].get("com.docker.network.bridge.enable_icc") != "false" or
        data["IPAM"]["Config"][0]["Subnet"] != SUBNET):
        raise RuntimeError("Sandbox network does not match the isolated configuration")
    exists = subprocess.run(["/usr/sbin/nft", "list", "table", "inet", "hermes_qq_sandbox"],
                            env=ENV, capture_output=True).returncode == 0
    # Tencent maps the host public IPv4 to its private NIC outside this machine.
    # Such hairpin connections traverse FORWARD, not the host INPUT hook.
    host_public = [str(ipaddress.IPv4Address(x.strip())) + "/32"
                   for x in Path("/etc/hermes-qq-sandbox-host-ips").read_text().splitlines()
                   if x.strip()]
    addresses = json.loads(run(["/usr/sbin/ip", "-j", "-4", "address", "show"]).stdout)
    host_local = [info["local"] + "/32" for row in addresses for info in row.get("addr_info", [])
                  if info.get("family") == "inet" and ipaddress.IPv4Address(info["local"]).is_global]
    blocked = [str(net) for net in ipaddress.collapse_addresses(
        ipaddress.IPv4Network(x) for x in BLOCKED + host_public + host_local)]
    rules = ("delete table inet hermes_qq_sandbox\n" if exists else "") + """
table inet hermes_qq_sandbox {
    set blocked_ipv4 {
        type ipv4_addr
        flags interval
        elements = { """ + ", ".join(blocked) + """ }
    }
    chain host_input {
        type filter hook input priority -10; policy accept;
        iifname "hqq-sbx" counter drop
    }
    chain egress {
        type filter hook forward priority -10; policy accept;
        iifname "hqq-sbx" meta nfproto ipv6 counter drop
        iifname "hqq-sbx" ip daddr @blocked_ipv4 counter drop
        iifname "hqq-sbx" oifname "hqq-sbx" counter drop
        iifname "hqq-sbx" counter accept
    }
}
"""
    run(["/usr/sbin/nft", "--check", "-f", "-"], input=rules)
    run(["/usr/sbin/nft", "-f", "-"], input=rules)
if __name__ == "__main__":
    ensure()
