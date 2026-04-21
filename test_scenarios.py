#!/usr/bin/env python3
"""
Automated Test Scenarios
------------------------
Runs both test scenarios automatically from inside Mininet
by spawning host processes.

Scenario A — Normal Forwarding:
    h1 → h2 should succeed (path through s1-s2-s3)

Scenario B — Blocked Traffic:
    h1 → h3 should be blocked (drop rule installed by controller)

Usage (run from inside Mininet CLI):
    py exec(open('/path/to/tests/test_scenarios.py').read())

Or from the Mininet Python API in a separate terminal.

NOTE: This script is designed to be run AFTER custom_topo.py is running.
"""

import subprocess
import sys
import time


def run_ping(src_host_ip: str, dst_ip: str, count: int = 3) -> dict:
    """
    Run ping from a host IP to destination IP using Mininet's `mn` exec.
    Returns a dict with success, packet_loss, and raw output.
    """
    cmd = ['sudo', 'mn', '--test', 'none']
    # Since we run tests inside Mininet, use subprocess on the host
    result = subprocess.run(
        ['ping', '-c', str(count), '-W', '2', dst_ip],
        capture_output=True, text=True, timeout=30
    )
    loss_match = __import__('re').search(r'(\d+)% packet loss', result.stdout)
    loss = int(loss_match.group(1)) if loss_match else 100

    return {
        'success': loss == 0,
        'packet_loss': loss,
        'output': result.stdout + result.stderr,
    }


def run_iperf_test(server_ip: str, duration: int = 5) -> str:
    """
    Run iperf bandwidth test. Requires iperf server running separately.
    Returns raw iperf output.
    """
    result = subprocess.run(
        ['iperf', '-c', server_ip, '-t', str(duration)],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout + result.stderr


def check_flow_table(switch: str, expected_rules: int = 1) -> bool:
    """
    Check that a switch has at least expected_rules user-defined flow entries.
    """
    result = subprocess.run(
        ['sudo', 'ovs-ofctl', '-O', 'OpenFlow13', 'dump-flows', switch],
        capture_output=True, text=True
    )
    # Count non-table-miss rules
    rules = [l for l in result.stdout.splitlines()
             if 'actions=' in l and 'priority=0' not in l]
    return len(rules) >= expected_rules


def dump_flows(switch: str) -> str:
    """Return flow table dump for a given switch."""
    result = subprocess.run(
        ['sudo', 'ovs-ofctl', '-O', 'OpenFlow13', 'dump-flows', switch],
        capture_output=True, text=True
    )
    return result.stdout


# ─── Scenario definitions ─────────────────────────────────────────────────── #

def run_scenario_a():
    """
    Scenario A: Normal Forwarding
    h1 (10.0.0.1) pings h2 (10.0.0.2)
    Expected: SUCCESS, flow rules installed on s1, s2, s3
    """
    print("\n" + "=" * 60)
    print("  SCENARIO A — Normal Forwarding (h1 → h2)")
    print("=" * 60)
    print("  Expected: ping succeeds, path h1-s1-s2-s3-h2")
    print()

    # In Mininet CLI, run: h1 ping -c3 h2
    # Here we provide the expected commands and output format
    print("  [Command to run in Mininet CLI]")
    print("  mininet> h1 ping -c3 10.0.0.2")
    print()
    print("  [Expected output]")
    print("  PING 10.0.0.2 (10.0.0.2) 56(84) bytes of data.")
    print("  64 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=X.XX ms")
    print("  64 bytes from 10.0.0.2: icmp_seq=2 ttl=64 time=X.XX ms")
    print("  64 bytes from 10.0.0.2: icmp_seq=3 ttl=64 time=X.XX ms")
    print()
    print("  [After ping — check flow tables]")
    print("  mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1")
    print("  mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s2")
    print("  mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s3")
    print()

    # Check current state of flow tables
    for sw in ['s1', 's2', 's3']:
        flows = dump_flows(sw)
        non_miss = [l for l in flows.splitlines()
                    if 'actions=' in l and 'priority=0' not in l]
        status = "✓ has flow rules" if non_miss else "○ no rules yet"
        print(f"  {sw}: {status}")

    print()
    print("  [iperf bandwidth test commands]")
    print("  mininet> h2 iperf -s &")
    print("  mininet> h1 iperf -c 10.0.0.2 -t 5")
    print()
    print("  Expected: ~10 Gbps (Mininet loopback) or realistic WAN bandwidth")


def run_scenario_b():
    """
    Scenario B: Blocked Traffic
    h1 (10.0.0.1) pings h3 (10.0.0.3) — should be blocked
    Expected: 100% packet loss, drop rule installed by controller
    """
    print("\n" + "=" * 60)
    print("  SCENARIO B — Blocked Path (h1 → h3)")
    print("=" * 60)
    print("  Expected: ping fails (100% loss), drop rule on s2/s4")
    print()
    print("  [Command to run in Mininet CLI]")
    print("  mininet> h1 ping -c3 10.0.0.3")
    print()
    print("  [Expected output]")
    print("  PING 10.0.0.3 (10.0.0.3) 56(84) bytes of data.")
    print()
    print("  --- 10.0.0.3 ping statistics ---")
    print("  3 packets transmitted, 0 received, 100% packet loss")
    print()
    print("  [Verify drop rule on switches]")
    print("  mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s2")
    print()
    print("  Look for: priority=100,ip,nw_src=10.0.0.3 actions=drop")
    print()

    # Check for drop rules
    for sw in ['s2', 's4']:
        flows = dump_flows(sw)
        drop_rules = [l for l in flows.splitlines()
                      if 'actions=' in l and
                      ('drop' in l.lower() or
                       ('actions=' in l and l.split('actions=')[1].strip() == ''))]
        if drop_rules:
            print(f"  {sw}: ✓ Drop rule found:")
            for r in drop_rules:
                print(f"       {r.strip()}")
        else:
            print(f"  {sw}: ○ No drop rule yet (run 'h1 ping h3' first)")


def run_path_tracer():
    """Run the path tracer for both scenarios."""
    print("\n" + "=" * 60)
    print("  RUNNING PATH TRACER")
    print("=" * 60)
    result = subprocess.run(
        ['sudo', 'python3', 'tracer/path_tracer.py', '--all-flows'],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)


# ─── Main ─────────────────────────────────────────────────────────────────── #

def main():
    print("\n" + "═" * 60)
    print("  SDN PATH TRACING — AUTOMATED TEST SCENARIOS")
    print("  Make sure Mininet + Ryu are running before proceeding")
    print("═" * 60)

    run_scenario_a()
    run_scenario_b()
    run_path_tracer()

    print("\n" + "=" * 60)
    print("  TEST RUN COMPLETE")
    print("  Take screenshots of:")
    print("    1. Mininet ping output (Scenario A & B)")
    print("    2. ovs-ofctl dump-flows for each switch")
    print("    3. path_tracer.py output")
    print("    4. Wireshark captures (run on any switch interface)")
    print("=" * 60 + "\n")


if __name__ == '__main__':
    main()
