#!/usr/bin/env python3
"""
Custom Mininet Topology for SDN Path Tracing Tool
--------------------------------------------------
Topology:
    h1 -- s1 -- s2 -- s3 -- h2
                 |
                 s4 -- h3

- 3 hosts, 4 switches
- Linear backbone (s1-s2-s3) with a branch (s2-s4)
- Long enough path for meaningful tracing
"""

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info


class SDNPathTopo(Topo):
    """
    Custom 4-switch, 3-host topology for path tracing demonstration.
    """

    def build(self):
        # Add hosts with static IP addresses
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
        h3 = self.addHost('h3', ip='10.0.0.3/24', mac='00:00:00:00:00:03')

        # Add switches (OpenFlow 1.3)
        s1 = self.addSwitch('s1', cls=OVSKernelSwitch, protocols='OpenFlow13')
        s2 = self.addSwitch('s2', cls=OVSKernelSwitch, protocols='OpenFlow13')
        s3 = self.addSwitch('s3', cls=OVSKernelSwitch, protocols='OpenFlow13')
        s4 = self.addSwitch('s4', cls=OVSKernelSwitch, protocols='OpenFlow13')

        # Add links: h1-s1-s2-s3-h2 backbone
        self.addLink(h1, s1)   # h1-eth0 <-> s1-eth1
        self.addLink(s1, s2)   # s1-eth2 <-> s2-eth1
        self.addLink(s2, s3)   # s2-eth2 <-> s3-eth1
        self.addLink(s3, h2)   # s3-eth2 <-> h2-eth0

        # Branch: s2-s4-h3
        self.addLink(s2, s4)   # s2-eth3 <-> s4-eth1
        self.addLink(s4, h3)   # s4-eth2 <-> h3-eth0


def run():
    """Run the topology with a remote Ryu controller."""
    setLogLevel('info')
    topo = SDNPathTopo()

    net = Mininet(
        topo=topo,
        controller=RemoteController('c0', ip='127.0.0.1', port=6633),
        switch=OVSKernelSwitch,
        autoSetMacs=False,
        autoStaticArp=False
    )

    net.start()

    info('\n*** Topology started successfully ***\n')
    info('Hosts: h1(10.0.0.1)  h2(10.0.0.2)  h3(10.0.0.3)\n')
    info('Switches: s1 -- s2 -- s3\n')
    info('                |\n')
    info('               s4\n')
    info('\n*** Run: h1 ping h2  or  h1 ping h3 to install flows ***\n')

    CLI(net)
    net.stop()


if __name__ == '__main__':
    run()
