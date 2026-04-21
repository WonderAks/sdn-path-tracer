# SDN Path Tracing Tool

**Course Project — Computer Networks | SDN Mininet Simulation (Orange Problem)**

An SDN-based path tracing tool that tracks OpenFlow forwarding rules across a custom Mininet topology and visually reconstructs the exact route packets take from source to destination.

---

## Table of Contents

- [Problem Statement](#problem-statement)
- [Topology](#topology)
- [Architecture](#architecture)
- [Setup & Installation](#setup--installation)
- [Running the Project](#running-the-project)
- [Test Scenarios](#test-scenarios)
- [Expected Output](#expected-output)
- [Proof of Execution](#proof-of-execution)
- [File Structure](#file-structure)

---

## Problem Statement

In traditional networks, there is no way to programmatically inspect the exact forwarding path a packet takes. In Software-Defined Networking (SDN), the controller has global visibility — every flow rule installed on every switch is queryable.

**This project implements a path tracing tool that:**
1. Runs a custom Mininet topology with 4 OpenFlow switches and 3 hosts
2. Uses a Ryu controller to handle `packet_in` events and install explicit match-action flow rules
3. Queries each switch's flow table using `ovs-ofctl` to reconstruct the full forwarding path
4. Demonstrates two scenarios: normal forwarding (Scenario A) and blocked traffic (Scenario B)

---

## Topology

```
h1 (10.0.0.1)
     |
    s1
     |
    s2 ──── s4 ──── h3 (10.0.0.3)  [BLOCKED]
     |
    s3
     |
h2 (10.0.0.2)
```

| Device | Role | IP / MAC |
|--------|------|----------|
| h1 | Source host | 10.0.0.1 / 00:00:00:00:00:01 |
| h2 | Destination host | 10.0.0.2 / 00:00:00:00:00:02 |
| h3 | Blocked host | 10.0.0.3 / 00:00:00:00:00:03 |
| s1–s4 | OVS switches | OpenFlow 1.3 |

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Ryu Controller                     │
│  path_controller.py                                  │
│  ┌──────────────────┐  ┌──────────────────────────┐ │
│  │  packet_in       │  │  Flow Rule Manager       │ │
│  │  handler         │  │  - Learn MAC → port      │ │
│  │                  │  │  - Install unicast rules  │ │
│  │  - Parse pkt     │  │  - Install drop rules    │ │
│  │  - Check block   │  │  - Priority management   │ │
│  └──────────────────┘  └──────────────────────────┘ │
└────────────────────────┬────────────────────────────┘
                  OpenFlow 1.3 (port 6633)
         ┌────────────────┼────────────────┐
        s1               s2              s3/s4
         │                │                │
        h1          (backbone)           h2 / h3

┌─────────────────────────────────────────────────────┐
│                  Path Tracer                         │
│  path_tracer.py                                      │
│  - Calls ovs-ofctl dump-flows on each switch        │
│  - Parses match fields and output ports             │
│  - Follows port → next switch via topology map      │
│  - Prints full hop-by-hop path                      │
└─────────────────────────────────────────────────────┘
```

### Key OpenFlow Concepts Used

| Concept | Implementation |
|---------|---------------|
| `packet_in` event | Controller receives unmatched packets |
| Match fields | `eth_dst`, `in_port`, `eth_type`, `ipv4_src` |
| Actions | `output:port` (forward) or empty (drop) |
| Flow priority | 100 = block, 10 = learned unicast, 0 = table-miss |
| `OFPFlowMod` | Installs rules into the switch flow table |
| `ovs-ofctl` | Used by path tracer to read installed rules |

---

## Setup & Installation

### Prerequisites

- Ubuntu 20.04 / 22.04 (VM or native)
- Python 3.8+

### Step 1 — Install Mininet

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch
sudo mn --test pingall   # verify installation
```

### Step 2 — Install Ryu

```bash
pip install ryu
# or
pip install -r requirements.txt
```

### Step 3 — Install Testing Tools

```bash
sudo apt install -y wireshark iperf tcpdump
```

### Step 4 — Clone This Repository

```bash
git clone https://github.com/YOUR_USERNAME/sdn-path-tracer.git
cd sdn-path-tracer
```

---

## Running the Project

Run each step in a **separate terminal**.

### Terminal 1 — Start the Ryu Controller

```bash
cd sdn-path-tracer
ryu-manager controller/path_controller.py
```

Expected output:
```
loading app controller/path_controller.py
============================================================
  SDN Path Tracing Controller started
  Blocking traffic from: 10.0.0.3 (Scenario B)
============================================================
```

### Terminal 2 — Start Mininet Topology

```bash
cd sdn-path-tracer
sudo python3 topology/custom_topo.py
```

You should see the Mininet CLI prompt:
```
mininet>
```

### Terminal 3 — Generate Traffic (install flow rules)

```bash
# Inside Mininet CLI (Terminal 2):

# Scenario A — normal ping (installs forwarding rules)
mininet> h1 ping -c3 10.0.0.2

# Scenario B — blocked ping
mininet> h1 ping -c3 10.0.0.3

# Optional: iperf bandwidth test
mininet> h2 iperf -s &
mininet> h1 iperf -c 10.0.0.2 -t 5
```

### Terminal 3 — Run the Path Tracer

```bash
# After traffic has been generated:
sudo python3 tracer/path_tracer.py

# With full flow table dump:
sudo python3 tracer/path_tracer.py --all-flows

# Trace a specific pair:
sudo python3 tracer/path_tracer.py --src h1 --dst h2
```

### Verify Flow Tables Manually

```bash
# Check what rules are installed on each switch:
sudo ovs-ofctl -O OpenFlow13 dump-flows s1
sudo ovs-ofctl -O OpenFlow13 dump-flows s2
sudo ovs-ofctl -O OpenFlow13 dump-flows s3
sudo ovs-ofctl -O OpenFlow13 dump-flows s4
```

---

## Test Scenarios

### Scenario A — Normal Forwarding (h1 → h2)

**What happens:**
1. `h1 ping h2` sends an ICMP packet
2. `s1` has no matching rule → sends `packet_in` to controller
3. Controller learns MAC addresses, installs forwarding rules on `s1`, `s2`, `s3`
4. Subsequent packets follow installed rules without hitting the controller
5. Path tracer reads flow tables and shows: `h1 → s1(port2) → s2(port2) → s3(port2) → h2`

**Expected ping result:**
```
3 packets transmitted, 3 received, 0% packet loss
```

---

### Scenario B — Blocked Traffic (h1 → h3)

**What happens:**
1. `h1 ping h3` — packet arrives at `s2`
2. `s2` sends `packet_in` to controller
3. Controller detects source IP `10.0.0.3` in the packet
4. Controller installs a **drop rule** with `priority=100` on the switch
5. All future packets from `10.0.0.3` are dropped at the switch (no controller involvement)

**Expected ping result:**
```
3 packets transmitted, 0 received, 100% packet loss
```

**Drop rule installed (verify with ovs-ofctl):**
```
priority=100,ip,nw_src=10.0.0.3 actions=drop
```

---

## Expected Output

### Path Tracer — Scenario A

```
────────────────────────────────────────────────────────────
  PATH TRACE:  h1  →  h2
  Destination MAC: 00:00:00:00:00:02
────────────────────────────────────────────────────────────
  h1  →  s1 (port 2)  →  s2 (port 2)  →  s3 (port 2)  →  h2

  Hop-by-hop detail:
    Hop 1: s1  →  port 2  →  s2
    Hop 2: s2  →  port 2  →  s3
    Hop 3: s3  →  port 2  →  h2

  ✓ RESULT: Packet REACHES h2
────────────────────────────────────────────────────────────
```

### Path Tracer — Scenario B

```
────────────────────────────────────────────────────────────
  PATH TRACE:  h1  →  h3
  Destination MAC: 00:00:00:00:00:03
────────────────────────────────────────────────────────────
  h1  →  s1 (port 2)  →  s2 [DROPPED]

  Hop-by-hop detail:
    Hop 1: s1  →  port 2  →  s2
    Hop 2: s2  →  DROP  (block rule matched)

  ✗ RESULT: Packet DROPPED at s2 (Scenario B)
────────────────────────────────────────────────────────────
```

### Controller Log (Ryu terminal)

```
[Switch 0000000000000001] Connected — table-miss rule installed
[Switch 0000000000000002] Connected — table-miss rule installed
[Switch 0000000000000001] Learned  MAC=00:00:00:00:00:01  port=1
[Switch 0000000000000001] FLOOD    dst=00:00:00:00:00:02  (unknown MAC, flooding)
[Switch 0000000000000001] FORWARD  dst=00:00:00:00:00:01  in_port=2 → out_port=1
[Switch 0000000000000001] FORWARD  dst=00:00:00:00:00:02  in_port=1 → out_port=2
[Switch 0000000000000002] BLOCKED  src=10.0.0.3 (Scenario B — drop rule installed)
```

---

## Proof of Execution

Screenshots to be added to the `screenshots/` folder:

| File | Contents |
|------|---------|
| `01_controller_startup.png` | Ryu controller started, switches connected |
| `02_mininet_topology.png` | Mininet CLI, topology running |
| `03_scenario_a_ping.png` | `h1 ping h2` — 0% packet loss |
| `04_scenario_b_ping.png` | `h1 ping h3` — 100% packet loss |
| `05_flow_tables_s1.png` | `ovs-ofctl dump-flows s1` |
| `06_flow_tables_s2.png` | `ovs-ofctl dump-flows s2` (includes drop rule) |
| `07_flow_tables_s3.png` | `ovs-ofctl dump-flows s3` |
| `08_path_tracer_output.png` | Full path tracer output for both scenarios |
| `09_iperf_result.png` | iperf bandwidth test result |
| `10_wireshark_capture.png` | Wireshark capture on s1-eth1 |

---

## File Structure

```
sdn-path-tracer/
├── topology/
│   └── custom_topo.py        # Mininet: 4 switches, 3 hosts
├── controller/
│   └── path_controller.py    # Ryu app: packet_in handler, flow rules
├── tracer/
│   └── path_tracer.py        # Reads flow tables, traces path
├── tests/
│   └── test_scenarios.py     # Scenario A & B test runner
├── screenshots/              # Proof of execution (add your screenshots here)
├── requirements.txt
└── README.md
```

---

## Notes

- The controller uses **OpenFlow 1.3** (`ofproto_v1_3`)
- Flow rules have an **idle timeout of 30 seconds** — regenerate traffic if rules expire
- The path tracer uses `sudo ovs-ofctl` — must be run with `sudo`
- Wireshark can be opened on any switch interface: `sudo wireshark &` then capture on `s1-eth1`

---

*Project by: [Your Name] | Roll No: [Your Roll No] | Computer Networks Lab*
"# sdn-path-tracer" 
