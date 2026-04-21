#!/usr/bin/env python3
"""
SDN Path Tracing Controller (Ryu)
----------------------------------
This Ryu application:
  1. Handles packet_in events from all switches
  2. Learns MAC-to-port mappings dynamically
  3. Installs explicit OpenFlow 1.3 match-action flow rules
  4. Logs every forwarding decision (which switch, which port)
  5. Blocks traffic from h3 (10.0.0.3) — Scenario B demonstration

Run with:
    ryu-manager controller/path_controller.py

The controller listens on port 6633 by default.
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4, arp
from ryu.lib import mac as mac_lib


# IP of the blocked host (Scenario B — blocked path)
BLOCKED_IP = '10.0.0.3'

# Flow rule priorities
PRIORITY_BLOCK  = 100   # Highest — drop rules for blocked hosts
PRIORITY_LEARN  = 10    # Learned unicast forwarding
PRIORITY_FLOOD  = 1     # Default flood (lowest)

# Flow idle timeout in seconds (0 = permanent)
IDLE_TIMEOUT = 30


class PathController(app_manager.RyuApp):
    """
    A learning switch controller that:
    - Logs the path packets take through the network
    - Installs explicit OpenFlow match-action rules
    - Drops traffic from BLOCKED_IP (Scenario B)
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(PathController, self).__init__(*args, **kwargs)
        # mac_to_port[datapath_id][mac_address] = port_number
        self.mac_to_port = {}
        self.logger.info("=" * 60)
        self.logger.info("  SDN Path Tracing Controller started")
        self.logger.info("  Blocking traffic from: %s (Scenario B)", BLOCKED_IP)
        self.logger.info("=" * 60)

    # ------------------------------------------------------------------ #
    #  Switch feature handshake — install a table-miss flow entry         #
    # ------------------------------------------------------------------ #
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """
        When a switch connects, install a table-miss rule so that
        unmatched packets are sent to the controller (packet_in).
        """
        datapath = ev.msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser

        # Match everything, lowest priority → send to controller
        match   = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self._install_flow(datapath,
                           priority=0,
                           match=match,
                           actions=actions,
                           idle_timeout=0)

        self.logger.info("[Switch %016x] Connected — table-miss rule installed",
                         datapath.id)

    # ------------------------------------------------------------------ #
    #  Main packet_in handler                                             #
    # ------------------------------------------------------------------ #
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """
        Called every time a switch sends a packet to the controller.
        We:
          1. Parse the packet to extract Ethernet src/dst
          2. Learn the src MAC → port mapping
          3. Check if the packet should be blocked (Scenario B)
          4. Install a unicast flow rule if the destination is known
          5. Forward or flood the current packet
        """
        msg      = ev.msg
        datapath = msg.datapath
        dpid     = datapath.id
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser

        # Parse the incoming packet
        pkt      = packet.Packet(msg.data)
        eth_pkt  = pkt.get_protocol(ethernet.ethernet)

        if eth_pkt is None:
            return  # Not an Ethernet packet — ignore

        src_mac  = eth_pkt.src
        dst_mac  = eth_pkt.dst
        in_port  = msg.match['in_port']

        # Ignore LLDP and IPv6 multicast to reduce noise
        if eth_pkt.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        if dst_mac.startswith('33:33'):
            return

        # ── Step 1: Learn source MAC → port ──────────────────────────── #
        self.mac_to_port.setdefault(dpid, {})
        if self.mac_to_port[dpid].get(src_mac) != in_port:
            self.mac_to_port[dpid][src_mac] = in_port
            self.logger.info(
                "[Switch %016x] Learned  MAC=%s  port=%d",
                dpid, src_mac, in_port
            )

        # ── Step 2: Check block rule (Scenario B) ────────────────────── #
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt and ip_pkt.src == BLOCKED_IP:
            # Install a high-priority drop rule so future packets are
            # dropped IN the switch without hitting the controller
            match = parser.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP,
                ipv4_src=BLOCKED_IP
            )
            self._install_flow(datapath,
                               priority=PRIORITY_BLOCK,
                               match=match,
                               actions=[],        # empty actions = DROP
                               idle_timeout=IDLE_TIMEOUT)
            self.logger.warning(
                "[Switch %016x] BLOCKED  src=%s (Scenario B — drop rule installed)",
                dpid, BLOCKED_IP
            )
            return   # Drop this packet now; don't forward

        # ── Step 3: Decide output port ───────────────────────────────── #
        if dst_mac in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst_mac]

            # Install a unicast flow rule for future packets
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst_mac)
            actions = [parser.OFPActionOutput(out_port)]
            self._install_flow(datapath,
                               priority=PRIORITY_LEARN,
                               match=match,
                               actions=actions,
                               idle_timeout=IDLE_TIMEOUT)

            self.logger.info(
                "[Switch %016x] FORWARD  dst=%s  in_port=%d → out_port=%d",
                dpid, dst_mac, in_port, out_port
            )
        else:
            # Destination unknown — flood
            out_port = ofproto.OFPP_FLOOD
            self.logger.info(
                "[Switch %016x] FLOOD    dst=%s  (unknown MAC, flooding)",
                dpid, dst_mac
            )

        # ── Step 4: Send the current packet out ──────────────────────── #
        actions = [parser.OFPActionOutput(out_port)]
        data    = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data
        )
        datapath.send_msg(out)

    # ------------------------------------------------------------------ #
    #  Helper: install a flow rule into a switch                          #
    # ------------------------------------------------------------------ #
    def _install_flow(self, datapath, priority, match, actions,
                      idle_timeout=IDLE_TIMEOUT):
        """
        Send an OFPFlowMod message to install a flow entry.

        Args:
            datapath     : the switch datapath object
            priority     : rule priority (higher wins on conflict)
            match        : OFPMatch specifying which packets to match
            actions      : list of OFPAction objects (empty = drop)
            idle_timeout : remove rule after N seconds of inactivity
        """
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser

        instructions = [
            parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)
        ]

        flow_mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            idle_timeout=idle_timeout,
            match=match,
            instructions=instructions
        )
        datapath.send_msg(flow_mod)
