import json
import logging
import threading
from pathlib import Path


logger = logging.getLogger("TCP_PROXY")


class PolicyEngine:

    def __init__(self, config_path):
        self.config_path = Path(config_path)
        self.lock = threading.RLock()
        self.policies = None
        self.last_mtime = None

        self.reload(force=True)

    def validate(self, policies):
        if not isinstance(policies, dict):
            raise ValueError("Policy configuration must be an object")

        required_lists = (
            "blocked_clients",
            "blocked_resources",
            "blocked_services",
        )

        for key in required_lists:
            value = policies.get(key)

            if not isinstance(value, list):
                raise ValueError(f"{key} must be a list")

            if not all(isinstance(item, str) for item in value):
                raise ValueError(f"{key} must contain strings")

        if policies.get("default_action") not in ("allow", "deny"):
            raise ValueError("Invalid default_action")

    def reload(self, force=False):
        with self.lock:
            mtime = self.config_path.stat().st_mtime_ns

            if not force and mtime == self.last_mtime:
                return

            with self.config_path.open("r") as file:
                policies = json.load(file)

            self.validate(policies)

            self.policies = policies
            self.last_mtime = mtime

            logger.info("POLICY_UPDATED | configuration reloaded")

    def evaluate(self, client_ip, service, resource):
        with self.lock:
            try:
                self.reload()
            except (OSError, ValueError) as error:
                logger.error("POLICY_RELOAD_FAILED | %s", error)

                # Never approve a request using an invalid policy update.
                return False, "Policy configuration unavailable"

            policies = self.policies

            if client_ip in policies["blocked_clients"]:
                return False, "Client IP blocked"

            if service in policies["blocked_services"]:
                return False, "Service blocked"

            if resource in policies["blocked_resources"]:
                return False, "Resource blocked"

            if policies["default_action"] == "deny":
                return False, "Default deny policy"

            return True, "Allowed by policy"
