#!/usr/bin/env python3
"""
SDN Path Tracing Controller (Ryu)
----------------------------------
This Ryu application:
  1. Handles packet_in events from all switches
  2. Learns MAC-to-port mappings dynamically
  3. Installs explicit OpenFlow 1.3 match-action flow rules
  4. Logs every forwarding decision (which switch, which port)
  5. Blocks ALL traffic TO h3 (10.0.0.3) — Scenario B

  KEY FIX: Block rule matches on DESTINATION IP (nw_dst=10.0.0.3)
  and is pre-installed on every switch at startup, so the block
  takes effect BEFORE any packet_in ever reaches the controller.
  This guarantees 100% packet loss for Scenario B from the first ping.

Run with:
    ryu-manager controller/path_controller.py
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4, arp


# ── Block configuration (Scenario B) ────────────────────────────────────────
# Traffic TO this IP will be dropped by a proactive rule on every switch
BLOCKED_DST_IP  = '10.0.0.3'
BLOCKED_DST_MAC = '00:00:00:00:00:03'

# Flow rule priorities
PRIORITY_BLOCK = 200   # Pre-installed drop rule — highest priority
PRIORITY_LEARN = 10    # Reactively learned unicast forwarding
PRIORITY_MISS  = 0     # Table-miss — send unknown packets to controller

# Flow idle timeout (0 = permanent)
IDLE_TIMEOUT = 0


class PathController(app_manager.RyuApp):
    """
    Learning switch + proactive block rule controller.

    Scenario A (normal): h1 -> h2 — controller learns MACs, installs
    unicast forwarding rules. Path tracer shows h1-s1-s2-s3-h2.

    Scenario B (blocked): h1 -> h3 — dropped immediately at every switch
    because a priority=200 drop rule matching dst=10.0.0.3 is installed
    at switch-connect time, before any ping is attempted.
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(PathController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.logger.info("=" * 62)
        self.logger.info("  SDN Path Tracing Controller  —  started")
        self.logger.info("  Scenario B: ALL traffic to %s will be DROPPED", BLOCKED_DST_IP)
        self.logger.info("  Block rule pre-installed on every switch at connect time")
        self.logger.info("=" * 62)

    # ------------------------------------------------------------------ #
    #  Switch feature handshake                                           #
    #  Install BOTH the block rule AND the table-miss rule on connect     #
    # ------------------------------------------------------------------ #
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser

        # 1. Pre-install drop rule for traffic TO BLOCKED_DST_IP
        # priority=200 — beats every other rule on this switch
        block_match = parser.OFPMatch(
            eth_type=ether_types.ETH_TYPE_IP,
            ipv4_dst=BLOCKED_DST_IP
        )
        self._install_flow(datapath,
                           priority=PRIORITY_BLOCK,
                           match=block_match,
                           actions=[],
                           idle_timeout=0)

        # Also block ARP requests to h3 so ARP resolution fails too
        arp_block_match = parser.OFPMatch(
            eth_type=ether_types.ETH_TYPE_ARP,
            arp_tpa=BLOCKED_DST_IP
        )
        self._install_flow(datapath,
                           priority=PRIORITY_BLOCK,
                           match=arp_block_match,
                           actions=[],
                           idle_timeout=0)

        # 2. Table-miss rule — send unmatched packets to controller
        miss_match   = parser.OFPMatch()
        miss_actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                               ofproto.OFPCML_NO_BUFFER)]
        self._install_flow(datapath,
                           priority=PRIORITY_MISS,
                           match=miss_match,
                           actions=miss_actions,
                           idle_timeout=0)

        self.logger.info(
            "[Switch %016x] Connected — drop(dst=%s) + table-miss installed",
            datapath.id, BLOCKED_DST_IP
        )

    # ------------------------------------------------------------------ #
    #  packet_in handler — reactive MAC learning & unicast forwarding     #
    # ------------------------------------------------------------------ #
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg      = ev.msg
        datapath = msg.datapath
        dpid     = datapath.id
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser

        pkt     = packet.Packet(msg.data)
        eth_pkt = pkt.get_protocol(ethernet.ethernet)
        if eth_pkt is None:
            return

        src_mac = eth_pkt.src
        dst_mac = eth_pkt.dst
        in_port = msg.match['in_port']

        if eth_pkt.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        # Safety net — drop anything to blocked MAC at software level too
        if dst_mac == BLOCKED_DST_MAC:
            self.logger.warning(
                "[Switch %016x] SAFETY DROP — dst=%s hit controller "
                "(flow rule may not be active yet)", dpid, dst_mac
            )
            return

        # MAC learning
        self.mac_to_port.setdefault(dpid, {})
        if self.mac_to_port[dpid].get(src_mac) != in_port:
            self.mac_to_port[dpid][src_mac] = in_port
            self.logger.info(
                "[Switch %016x] Learned  MAC=%-18s  port=%d",
                dpid, src_mac, in_port
            )

        # Forwarding decision
        if dst_mac in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst_mac]
            match    = parser.OFPMatch(in_port=in_port, eth_dst=dst_mac)
            actions  = [parser.OFPActionOutput(out_port)]
            self._install_flow(datapath,
                               priority=PRIORITY_LEARN,
                               match=match,
                               actions=actions,
                               idle_timeout=30)
            self.logger.info(
                "[Switch %016x] FORWARD  dst=%-18s  in=%d → out=%d",
                dpid, dst_mac, in_port, out_port
            )
        else:
            out_port = ofproto.OFPP_FLOOD
            self.logger.info(
                "[Switch %016x] FLOOD    dst=%-18s  (unknown)",
                dpid, dst_mac
            )

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
    #  Helper: install a flow rule                                        #
    # ------------------------------------------------------------------ #
    def _install_flow(self, datapath, priority, match, actions, idle_timeout=0):
        """Send OFPFlowMod. Empty actions list = DROP."""
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod  = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            idle_timeout=idle_timeout,
            match=match,
            instructions=inst
        )
        datapath.send_msg(mod)
