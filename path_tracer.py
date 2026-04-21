#!/usr/bin/env python3

import subprocess
import re
import argparse
import sys
from dataclasses import dataclass
from typing import Optional, List


ALL_SWITCHES = ['s1', 's2', 's3', 's4']

HOST_MACS = {
    'h1': '00:00:00:00:00:01',
    'h2': '00:00:00:00:00:02',
    'h3': '00:00:00:00:00:03',
}

TOPOLOGY = {
    's1': [('h1', 1), ('s2', 2)],
    's2': [('s1', 1), ('s3', 2), ('s4', 3)],
    's3': [('s2', 1), ('h2', 2)],
    's4': [('s2', 1), ('h3', 2)],
}


@dataclass
class FlowEntry:
    switch: str
    priority: int
    match: str
    actions: str
    out_port: Optional[int] = None


@dataclass
class PathHop:
    device: str
    port_out: Optional[int] = None
    next_device: Optional[str] = None


def get_flow_table(switch: str) -> List[FlowEntry]:
    try:
        result = subprocess.run(
            ['sudo', 'ovs-ofctl', '-O', 'OpenFlow13', 'dump-flows', switch],
            capture_output=True, text=True
        )
    except Exception:
        return []

    entries = []
    for line in result.stdout.splitlines():
        if 'actions=' not in line:
            continue

        pri = re.search(r'priority=(\d+)', line)
        priority = int(pri.group(1)) if pri else 0

        if priority == 0:
            continue

        match_part = line.split('actions=')[0]
        action_part = line.split('actions=')[1]

        out_port = None
        port_match = re.search(r'output:(\d+)', action_part)
        if port_match:
            out_port = int(port_match.group(1))

        entries.append(FlowEntry(
            switch=switch,
            priority=priority,
            match=match_part,
            actions=action_part,
            out_port=out_port
        ))

    return entries


def find_matching_flow(flows: List[FlowEntry], dst_mac: str) -> Optional[FlowEntry]:
    matches = []

    for entry in flows:
        match_str = entry.match.lower()

        # ✅ Forwarding rule match
        if dst_mac.lower() in match_str:
            matches.append(entry)

        # ✅ FIXED: Only consider DROP if it actually matches destination
        elif 'drop' in entry.actions.lower():
            if dst_mac.lower() in match_str:
                matches.append(entry)

    if not matches:
        return None

    return sorted(matches, key=lambda x: x.priority, reverse=True)[0]


def port_to_neighbour(switch: str, port: int) -> Optional[str]:
    for neigh, p in TOPOLOGY[switch]:
        if p == port:
            return neigh
    return None


def trace_path(src_host: str, dst_host: str) -> List[PathHop]:
    dst_mac = HOST_MACS[dst_host]

    start_switch = None
    for sw, neighs in TOPOLOGY.items():
        for dev, _ in neighs:
            if dev == src_host:
                start_switch = sw

    if not start_switch:
        return []

    path = []
    current = start_switch
    visited = set()

    while current not in visited:
        visited.add(current)

        flows = get_flow_table(current)
        flow = find_matching_flow(flows, dst_mac)

        if flow is None:
            path.append(PathHop(current, None, None))
            break

        # DROP case
        if 'drop' in flow.actions.lower():
            path.append(PathHop(current, None, '[DROPPED]'))
            break

        next_dev = port_to_neighbour(current, flow.out_port)

        path.append(PathHop(current, flow.out_port, next_dev))

        if next_dev == dst_host:
            break

        if next_dev not in TOPOLOGY:
            break

        current = next_dev

    return path


def print_path(src: str, dst: str, path: List[PathHop]):
    print(f"\nPATH TRACE: {src} → {dst}")
    print(f"Destination MAC: {HOST_MACS[dst]}\n")

    line = src
    for hop in path:
        if hop.next_device == '[DROPPED]':
            line += f" → {hop.device} [DROPPED]"
            print(line + " ✗ BLOCKED")
            return
        else:
            line += f" → {hop.device}"

    if path and path[-1].next_device == dst:
        line += f" → {dst}"
        print(line + " ✓ REACHES")
    else:
        print(line + " → ?")


def print_all_flow_tables():
    print("\nCOMPLETE FLOW TABLES\n")
    for sw in ALL_SWITCHES:
        print(f"--- {sw} ---")
        subprocess.run(['sudo', 'ovs-ofctl', '-O', 'OpenFlow13', 'dump-flows', sw])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--src', default='h1')
    parser.add_argument('--dst', default='h2')
    parser.add_argument('--all-flows', action='store_true')
    args = parser.parse_args()

    path = trace_path(args.src, args.dst)
    print_path(args.src, args.dst, path)

    if args.src == 'h1' and args.dst == 'h2':
        print("\nScenario B (h1 → h3)")
        path_b = trace_path('h1', 'h3')
        print_path('h1', 'h3', path_b)

    if args.all_flows:
        print_all_flow_tables()


if __name__ == "__main__":
    main()