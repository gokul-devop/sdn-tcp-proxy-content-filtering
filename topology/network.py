#!/usr/bin/env python3
import subprocess
from mininet.util import quietRun
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info


def create_network():

    net = Mininet(
        controller=None,
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=False
    )

    info("\n*** Adding Ryu SDN Controller\n")

    controller = net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6653
    )

    info("\n*** Creating OpenFlow Switch\n")

    s1 = net.addSwitch(
        "s1",
        protocols="OpenFlow13",
        failMode="secure"
    )

    info("\n*** Adding Clients\n")

    h1 = net.addHost("h1", ip="10.0.0.1/24")
    h2 = net.addHost("h2", ip="10.0.0.2/24")
    h3 = net.addHost("h3", ip="10.0.0.3/24")

    info("\n*** Adding TCP Proxy\n")

    h4 = net.addHost("h4", ip="10.0.0.4/24")

    info("\n*** Adding Backend Services\n")

    h5 = net.addHost("h5", ip="10.0.0.5/24")
    h6 = net.addHost("h6", ip="10.0.0.6/24")

    info("\n*** Connecting Hosts to Switch\n")

    hosts = [h1, h2, h3, h4, h5, h6]

    for host in hosts:
        net.addLink(
            host,
            s1,
            bw=100,
            delay="2ms"
        )

    info("\n*** Starting Network\n")

    net.start()
    info("\n*** Creating Proxy-to-Controller Management Link\n")

    # Remove any leftover management interface.
    subprocess.run(
        ["ip", "link", "del", "ryu-host"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    # Create a virtual Ethernet pair.
    subprocess.run(
        [
            "ip", "link", "add",
            "ryu-host",
            "type", "veth",
            "peer", "name", "ryu-proxy",
        ],
        check=True,
    )

    # Assign the proxy end to h4's network namespace.
    h4.cmd("true")

    proxy_pid = h4.pid

    subprocess.run(
        [
            "ip", "link", "set",
            "ryu-proxy",
            "netns", str(proxy_pid),
        ],
        check=True,
    )

    # Configure the Ubuntu side.
    subprocess.run(
        [
            "ip", "addr", "add",
            "192.168.100.1/24",
            "dev", "ryu-host",
        ],
        check=True,
    )

    subprocess.run(
        [
            "ip", "link", "set",
            "ryu-host",
            "up",
        ],
        check=True,
    )

    # Configure the Mininet proxy side.
    h4.cmd(
        "ip addr add 192.168.100.2/24 dev ryu-proxy"
    )

    h4.cmd(
        "ip link set ryu-proxy up"
    )

    info("*** Management Link Configured\n")
    info("\n*** Network Started Successfully\n")

    info("\n*** Network Topology\n")

    for host in hosts:
        info(f"{host.name}: {host.IP()}\n")

    info("\n*** Opening Mininet CLI\n")

    try:
        CLI(net)
    finally:
        info("\n*** Stopping Network\n")
        net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    create_network()
