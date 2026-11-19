"""Ryu OpenFlow 1.3 learning switch with REST-managed temporary TCP blocks.

Routes:
  GET    /policy/blocks
  POST   /policy/block/{ip}?duration=30
  DELETE /policy/block/{ip}

Run from project root:
  ryu-manager --ofp-tcp-listen-port 6653 --wsapi-port 8080 controller/sdn_controller.py
"""

import ipaddress
import json
import logging
import time

from webob import Response
from ryu.app import simple_switch_13
from ryu.app.wsgi import WSGIApplication, ControllerBase, route
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls,
)
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3


LOG = logging.getLogger("SDN_POLICY")

PROXY_IP = "10.0.0.4"
PROXY_PORT = 9000
BLOCK_PRIORITY = 100
BLOCK_COOKIE = 0xB10C
MAX_DURATION = 3600  # seconds; 0 means an indefinite block


class PolicyController(simple_switch_13.SimpleSwitch13):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths = {}
        self.blocked_clients = set()
        self.block_expirations = {}  # client_ip -> time.monotonic() deadline

        kwargs["wsgi"].register(PolicyAPI, {"policy_app": self})
        self.expiration_thread = hub.spawn(self.expiration_manager)
        LOG.info("Custom SDN Policy Controller initialized")

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        # Install the parent learning switch's table-miss controller rule.
        super().switch_features_handler(ev)
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        LOG.info("Switch connected: DPID=%s", dp.id)
        # Prevent clients from bypassing the TCP proxy.
        self.install_backend_protection(dp)
        # Reapply still-active restrictions after a switch reconnects.
        now = time.monotonic()
        for ip in list(self.blocked_clients):
            deadline = self.block_expirations.get(ip)
            if deadline is None or deadline > now:
                self.install_block(dp, ip)
            else:
                self.unblock_client(ip)

    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER],
    )
    def state_change_handler(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(dp.id, None)

    def install_backend_protection(self, dp):
        """Block direct client access to protected backend TCP services."""
        parser = dp.ofproto_parser

        clients = ("10.0.0.1", "10.0.0.2", "10.0.0.3")
        services = (
            ("10.0.0.5", 9001),
            ("10.0.0.6", 9002),
        )

        for client_ip in clients:
            for service_ip, service_port in services:
                match = parser.OFPMatch(
                    eth_type=0x0800,
                    ip_proto=6,
                    ipv4_src=client_ip,
                    ipv4_dst=service_ip,
                    tcp_dst=service_port,
                )

                mod = parser.OFPFlowMod(
                    datapath=dp,
                    priority=90,
                    match=match,
                    instructions=[],  # DROP
                )

                dp.send_msg(mod)

                LOG.info(
                    "BACKEND_PROTECTION | client=%s | backend=%s:%d",
                    client_ip,
                    service_ip,
                    service_port,
                )
    @staticmethod
    def make_block_match(dp, client_ip):
        return dp.ofproto_parser.OFPMatch(
            eth_type=0x0800,
            ip_proto=6,
            ipv4_src=client_ip,
            ipv4_dst=PROXY_IP,
            tcp_dst=PROXY_PORT,
        )

    def install_block(self, dp, client_ip):
        parser = dp.ofproto_parser
        mod = parser.OFPFlowMod(
            datapath=dp,
            cookie=BLOCK_COOKIE,
            table_id=0,
            priority=BLOCK_PRIORITY,
            match=self.make_block_match(dp, client_ip),
            instructions=[],  # Empty instruction list means DROP.
        )
        dp.send_msg(mod)
        LOG.warning("SDN_BLOCK_SENT | switch=%s | client=%s", dp.id, client_ip)

    def remove_block(self, dp, client_ip):
        ofproto = dp.ofproto
        parser = dp.ofproto_parser
        mod = parser.OFPFlowMod(
            datapath=dp,
            cookie=BLOCK_COOKIE,
            cookie_mask=0xFFFFFFFFFFFFFFFF,
            table_id=0,
            command=ofproto.OFPFC_DELETE_STRICT,
            priority=BLOCK_PRIORITY,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=self.make_block_match(dp, client_ip),
        )
        dp.send_msg(mod)
        LOG.info("SDN_UNBLOCK_SENT | switch=%s | client=%s", dp.id, client_ip)

    def block_client(self, client_ip, duration=0):
        if not self.datapaths:
            return False, "No connected switches"

        self.blocked_clients.add(client_ip)
        if duration > 0:
            self.block_expirations[client_ip] = time.monotonic() + duration
        else:
            self.block_expirations.pop(client_ip, None)

        for dp in list(self.datapaths.values()):
            self.install_block(dp, client_ip)

        LOG.warning("CLIENT_BLOCKED | client=%s | duration=%s", client_ip, duration)
        return True, "Block rule submitted"

    def unblock_client(self, client_ip):
        self.blocked_clients.discard(client_ip)
        self.block_expirations.pop(client_ip, None)
        for dp in list(self.datapaths.values()):
            self.remove_block(dp, client_ip)
        LOG.info("CLIENT_UNBLOCKED | client=%s", client_ip)
        return True, "Unblock rule submitted"

    def expiration_manager(self):
        while True:
            hub.sleep(1)
            now = time.monotonic()
            expired = [
                ip for ip, deadline in list(self.block_expirations.items())
                if deadline <= now
            ]
            for ip in expired:
                # Recheck in case a block was renewed while scanning.
                deadline = self.block_expirations.get(ip)
                if deadline is not None and deadline <= time.monotonic():
                    LOG.info("BAN_EXPIRED | client=%s", ip)
                    self.unblock_client(ip)
                    LOG.info("AUTO_UNBLOCK_SUBMITTED | client=%s", ip)


class PolicyAPI(ControllerBase):
    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.app = data["policy_app"]

    @staticmethod
    def json_response(payload, status=200):
        return Response(
            status=status,
            content_type="application/json",
            body=json.dumps(payload).encode("utf-8"),
        )

    @staticmethod
    def validate_ip(ip):
        try:
            address = ipaddress.ip_address(ip)
            return str(address) if address.version == 4 else None
        except ValueError:
            return None

    @route("block_client", "/policy/block/{ip}", methods=["POST"])
    def block(self, req, ip, **kwargs):
        client_ip = self.validate_ip(ip)
        if client_ip is None:
            return self.json_response({"error": "Invalid IPv4 address"}, 400)

        try:
            duration = int(req.GET.get("duration", "0"))
            if not 0 <= duration <= MAX_DURATION:
                raise ValueError("Duration out of range")
        except (TypeError, ValueError):
            return self.json_response(
                {"error": "Duration must be an integer between 0 and 3600 seconds"},
                400,
            )

        success, message = self.app.block_client(client_ip, duration)
        return self.json_response(
            {"success": success, "client": client_ip,
             "duration": duration, "message": message},
            200 if success else 503,
        )

    @route("unblock_client", "/policy/block/{ip}", methods=["DELETE"])
    def unblock(self, req, ip, **kwargs):
        client_ip = self.validate_ip(ip)
        if client_ip is None:
            return self.json_response({"error": "Invalid IPv4 address"}, 400)
        success, message = self.app.unblock_client(client_ip)
        return self.json_response(
            {"success": success, "client": client_ip, "message": message}
        )

    @route("list_blocks", "/policy/blocks", methods=["GET"])
    def list_blocks(self, req, **kwargs):
        now = time.monotonic()
        remaining = {
            ip: max(0, round(deadline - now, 1))
            for ip, deadline in self.app.block_expirations.items()
        }
        return self.json_response({
            "blocked_clients": sorted(self.app.blocked_clients),
            "connected_switches": sorted(self.app.datapaths.keys()),
            "remaining_seconds": remaining,
        })
