"""SDN enforcement helper used by the Mininet TCP proxy.

Designed for the project's Ryu API:
  GET /policy/blocks
  POST /policy/block/{ip}?duration=30

The proxy reconciles its local ban tracking with the controller on the
next violation after a ban, so expired or manually removed bans permit
a new violation-counting cycle without restarting the proxy.
"""

import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

logger = logging.getLogger("TCP_PROXY")


class SDNEnforcer:
    def __init__(
        self,
        controller_url="http://192.168.100.1:8080",
        threshold=3,
        ban_duration=30,
    ):
        if not isinstance(threshold, int) or threshold < 1:
            raise ValueError("threshold must be a positive integer")
        if not isinstance(ban_duration, int) or not 1 <= ban_duration <= 3600:
            raise ValueError("ban_duration must be between 1 and 3600 seconds")

        self.controller_url = controller_url.rstrip("/")
        self.threshold = threshold
        self.ban_duration = ban_duration
        self.violations = defaultdict(int)
        self.blocked_clients = set()
        self.pending_clients = set()
        self.lock = threading.Lock()

    def _request_json(self, path, method="GET"):
        req = urllib.request.Request(
            self.controller_url + path,
            method=method,
            data=b"" if method == "POST" else None,
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            if response.status != 200:
                raise RuntimeError("Controller HTTP status: %s" % response.status)
            data = json.load(response)
            if not isinstance(data, dict):
                raise ValueError("Controller returned invalid JSON structure")
            return data

    def controller_has_block(self, client_ip):
        """Return True/False for known state, or None if controller unavailable."""
        try:
            state = self._request_json("/policy/blocks")
            blocked = state.get("blocked_clients")
            if not isinstance(blocked, list):
                raise ValueError("Missing blocked_clients in controller response")
            return client_ip in blocked
        except (urllib.error.URLError, OSError, TimeoutError, ValueError,
                RuntimeError) as exc:
            logger.error("SDN_SYNC_ERROR | client=%s | error=%s", client_ip, exc)
            return None

    def block_client(self, client_ip):
        ip = urllib.parse.quote(client_ip, safe="")
        path = "/policy/block/%s?duration=%d" % (ip, self.ban_duration)
        try:
            response = self._request_json(path, method="POST")
            return response.get("success") is True
        except (urllib.error.URLError, OSError, TimeoutError, ValueError,
                RuntimeError) as exc:
            logger.error("SDN_API_ERROR | client=%s | error=%s", client_ip, exc)
            return False

    def register_violation(self, client_ip, reason):
        """Count a denied request and submit one timed ban at the threshold."""
        with self.lock:
            if client_ip in self.pending_clients:
                logger.info("SDN_ACTION_PENDING | client=%s", client_ip)
                return
            if client_ip in self.blocked_clients:
                self.pending_clients.add(client_ip)
                reconcile = True
            else:
                self.violations[client_ip] += 1
                count = self.violations[client_ip]
                logger.warning(
                    "VIOLATION | client=%s | count=%d | reason=%s",
                    client_ip, count, reason,
                )
                if count < self.threshold:
                    return
                self.pending_clients.add(client_ip)
                reconcile = False

        if reconcile:
            state = self.controller_has_block(client_ip)
            with self.lock:
                self.pending_clients.discard(client_ip)
                if state is False:
                    self.blocked_clients.discard(client_ip)
                    self.violations[client_ip] = 1
                    logger.info("BAN_CLEARED | client=%s | counter_reset=1", client_ip)
                    logger.warning(
                        "VIOLATION | client=%s | count=1 | reason=%s",
                        client_ip, reason,
                    )
                elif state is True:
                    logger.info("BAN_STILL_ACTIVE | client=%s", client_ip)
                # Unknown state: keep local ban and do not resubmit blindly.
            return

        success = False
        try:
            success = self.block_client(client_ip)
        finally:
            with self.lock:
                self.pending_clients.discard(client_ip)
                if success:
                    self.blocked_clients.add(client_ip)
                    logger.warning(
                        "AUTO_BLOCK_SUCCESS | client=%s | duration=%ds",
                        client_ip, self.ban_duration,
                    )
                else:
                    logger.error("AUTO_BLOCK_FAILED | client=%s", client_ip)
