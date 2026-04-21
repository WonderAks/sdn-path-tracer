#!/usr/bin/env python3
"""
SDN Path Tracer
---------------
Queries OpenFlow flow tables from every switch using `ovs-ofctl`
and reconstructs the actual forwarding path a packet would take
from a source host to a destination host.

Usage:
    sudo python3 tracer/path_tracer.py

    # Or with arguments:
    sudo python3 tracer/path_tracer.py --src h1 --dst h2

The script reads flow tables AFTER traffic has been generated
(e.g., h1 ping h2 inside Mininet) so that flow rules are installed.
"""

import subprocess
import re
import argparse
import sys
from dataclasses import dataclass, field
from typing import Optional


# ─── Configuration ─────────────────────────────────────────────────────────── #

# Switches in the topology (must match Mininet switch names)
ALL_SWITCHES = ['s1', 's2', 's3', 's4']

# Known host MAC addresses (match custom_topo.py)
HOST_MACS = {
    'h1': '00:00:00:00:00:01',
    'h2': '00:00:00:00:00:02',
    'h3': '00:00:00:00:00:03',
}

# Adjacency map: switch → list of (neighbour, port_on_this_switch)
# Used to resolve "port N on switch X" to the next device name
TOPOLOGY = {
    's1': [('h1', 1), ('s2', 2)],
    's2': [('s1', 1), ('s3', 2), ('s4', 3)],
    's3': [('s2', 1), ('h2', 2)],
    's4': [('s2', 1), ('h3', 2)],
}

# ─── Data classes ───────────────────────────────────────────────────────────── #

@dataclass
class FlowEntry:
    """Represents a single OpenFlow flow rule."""
    switch: str
    priority: int
    match: str
    actions: str
    out_port: Optional[int] = None

@dataclass
class PathHop:
    """One hop in the traced path."""
    device: str
    port_in:  Optional[int] = None
    port_out: Optional[int] = None
    next_device: Optional[str] = None

# ─── Flow table reader ──────────────────────────────────────────────────────── #

def get_flow_table(switch: str) -> list[FlowEntry]:
    """
    Query the OpenFlow flow table of a switch using ovs-ofctl.
    Returns a list of FlowEntry objects.
    """
    try:
        result = subprocess.run(
            ['sudo', 'ovs-ofctl', '-O', 'OpenFlow13', 'dump-flows', switch],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            print(f"[!] Could not query {switch}: {result.stderr.strip()}")
            return []
    except subprocess.TimeoutExpired:
        print(f"[!] Timeout querying {switch} — is Mininet running?")
        return []
    except FileNotFoundError:
        print("[!] ovs-ofctl not found. Run this script on the Mininet VM.")
        sys.exit(1)

    entries = []
    for line in result.stdout.splitlines():
        if 'actions=' not in line:
            continue

        # Extract priority
        pri_match = re.search(r'priority=(\d+)', line)
        priority = int(pri_match.group(1)) if pri_match else 0

        # Skip table-miss entry (priority=0, match-all)
        if priority == 0:
            continue

        # Extract match fields (everything before 'actions=')
        match_part = line.split('actions=')[0].strip()

        # Extract actions
        action_part = line.split('actions=')[1].strip()

        # Extract output port number (if any)
        out_port = None
        port_match = re.search(r'output:(\d+)', action_part)
        if port_match:
            out_port = int(port_match.group(1))

        entries.append(FlowEntry(
            switch=switch,
            priority=priority,
            match=match_part,
            actions=action_part,
            out_port=out_port,
        ))

    return entries


def find_matching_flow(flows: list[FlowEntry], dst_mac: str) -> Optional[FlowEntry]:
    """
    Find the best matching flow entry for a destination MAC address.
    Returns the highest-priority matching entry, or None if not found.
    """
    matches = []
    for entry in flows:
        # Match on eth_dst (unicast forwarding rule)
        if dst_mac.lower() in entry.match.lower():
            matches.append(entry)
        # Match on drop rule (empty actions = blocked)
        elif 'drop' in entry.actions.lower() or entry.actions.strip() == '':
            # Only flag as blocked if it's a high-priority rule
            if entry.priority >= 50:
                matches.append(entry)

    if not matches:
        return None
    # Return highest-priority match
    return sorted(matches, key=lambda e: e.priority, reverse=True)[0]


def port_to_neighbour(switch: str, port: int) -> Optional[str]:
    """
    Given a switch and an output port number, return the name
    of the connected device (next switch or host).
    """
    for (neighbour, p) in TOPOLOGY.get(switch, []):
        if p == port:
            return neighbour
    return None

# ─── Path tracing logic ─────────────────────────────────────────────────────── #

def trace_path(src_host: str, dst_host: str) -> list[PathHop]:
    """
    Trace the forwarding path from src_host to dst_host.

    Algorithm:
        1. Start at src_host's directly-connected switch
        2. On each switch, query the flow table for dst_mac
        3. Follow the output port to the next device
        4. Stop when we reach the dst_host or hit a dead end / drop
    """
    if dst_host not in HOST_MACS:
        print(f"[!] Unknown host: {dst_host}. Known hosts: {list(HOST_MACS)}")
        return []

    dst_mac = HOST_MACS[dst_host]

    # Find the switch directly connected to src_host
    start_switch = None
    for sw, neighbours in TOPOLOGY.items():
        for (dev, _) in neighbours:
            if dev == src_host:
                start_switch = sw
                break

    if start_switch is None:
        print(f"[!] Could not find switch connected to {src_host}")
        return []

    path      = []
    current   = start_switch
    visited   = set()
    max_hops  = 10   # Guard against loops

    while current in TOPOLOGY and current not in visited and max_hops > 0:
        visited.add(current)
        max_hops -= 1

        flows = get_flow_table(current)
        flow  = find_matching_flow(flows, dst_mac)

        if flow is None:
            # No rule installed yet — traffic hasn't been generated
            hop = PathHop(device=current, port_out=None, next_device=None)
            path.append(hop)
            break

        # Check for DROP rule (Scenario B)
        if flow.actions.strip() == '' or 'drop' in flow.actions.lower():
            hop = PathHop(device=current, port_out=None, next_device='[DROPPED]')
            path.append(hop)
            break

        next_dev = port_to_neighbour(current, flow.out_port) if flow.out_port else None
        hop = PathHop(device=current, port_out=flow.out_port, next_device=next_dev)
        path.append(hop)

        if next_dev == dst_host:
            # Destination reached
            break
        elif next_dev and next_dev in TOPOLOGY:
            current = next_dev
        else:
            break

    return path

# ─── Pretty printer ─────────────────────────────────────────────────────────── #

def print_path(src_host: str, dst_host: str, path: list[PathHop]):
    """Print the traced path in a human-readable format."""
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  PATH TRACE:  {src_host}  →  {dst_host}")
    print(f"  Destination MAC: {HOST_MACS.get(dst_host, '?')}")
    print(sep)

    if not path:
        print("  [!] No path found. Have you run traffic yet?")
        print(f"      In Mininet: {src_host} ping -c3 {dst_host}")
        print(sep)
        return

    # Build one-line summary
    hops_str = f"  {src_host}"
    for hop in path:
        if hop.next_device == '[DROPPED]':
            hops_str += f"  →  {hop.device} [DROPPED]"
        else:
            port_label = f"(port {hop.port_out})" if hop.port_out else "(no rule)"
            hops_str += f"  →  {hop.device} {port_label}"

    if path and path[-1].next_device == dst_host:
        hops_str += f"  →  {dst_host}"
    elif path and path[-1].next_device == '[DROPPED]':
        hops_str += "  ✗ BLOCKED"
    else:
        hops_str += "  → ?"

    print(hops_str)
    print()

    # Per-hop detail
    print("  Hop-by-hop detail:")
    for i, hop in enumerate(path, 1):
        if hop.next_device == '[DROPPED]':
            print(f"    Hop {i}: {hop.device}  →  DROP  (block rule matched)")
        elif hop.port_out:
            next_label = hop.next_device or "?"
            print(f"    Hop {i}: {hop.device}  →  port {hop.port_out}  →  {next_label}")
        else:
            print(f"    Hop {i}: {hop.device}  →  no matching flow rule installed")

    # Result
    print()
    if path and path[-1].next_device == dst_host:
        print(f"  ✓ RESULT: Packet REACHES {dst_host}")
    elif path and path[-1].next_device == '[DROPPED]':
        print(f"  ✗ RESULT: Packet DROPPED at {path[-1].device} (Scenario B)")
    else:
        print(f"  ? RESULT: Path incomplete — flow rules may not be installed yet")
    print(sep + "\n")


def print_all_flow_tables():
    """Dump all flow tables for debugging / proof of execution."""
    print("\n" + "═" * 60)
    print("  COMPLETE FLOW TABLES (all switches)")
    print("═" * 60)
    for sw in ALL_SWITCHES:
        print(f"\n  ── {sw} ──")
        result = subprocess.run(
            ['sudo', 'ovs-ofctl', '-O', 'OpenFlow13', 'dump-flows', sw],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            lines = [l for l in result.stdout.splitlines() if 'actions=' in l]
            if lines:
                for line in lines:
                    print(f"    {line.strip()}")
            else:
                print("    (no user flows installed — run traffic first)")
        else:
            print(f"    [error] {result.stderr.strip()}")
    print()


# ─── CLI entry point ────────────────────────────────────────────────────────── #

def main():
    parser = argparse.ArgumentParser(
        description='SDN Path Tracer — traces forwarding path through OpenFlow switches'
    )
    parser.add_argument('--src', default='h1',
                        help='Source host name (default: h1)')
    parser.add_argument('--dst', default='h2',
                        help='Destination host name (default: h2)')
    parser.add_argument('--all-flows', action='store_true',
                        help='Also print all raw flow tables')
    args = parser.parse_args()

    print("\n  SDN Path Tracing Tool")
    print("  Make sure Mininet is running and traffic has been generated.")
    print("  (run 'h1 ping -c3 h2' in the Mininet CLI first)\n")

    # Trace the requested path
    path = trace_path(args.src, args.dst)
    print_path(args.src, args.dst, path)

    # Also trace Scenario B (h1 → h3, which should be blocked)
    if args.src == 'h1' and args.dst == 'h2':
        print("  Running Scenario B trace (h1 → h3, expected: BLOCKED)...")
        path_b = trace_path('h1', 'h3')
        print_path('h1', 'h3', path_b)

    if args.all_flows:
        print_all_flow_tables()


if __name__ == '__main__':
    main()
