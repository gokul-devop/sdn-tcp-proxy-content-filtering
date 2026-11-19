# SDN-Based TCP Proxy and Dynamic Content Filtering

A Python socket-programming and Software-Defined Networking (SDN) project integrating a concurrent TCP proxy, JSON request filtering, and Ryu/OpenFlow rules to control access to simulated services.

## Features

- Length-prefixed JSON messages over TCP (4-byte size header).
- Concurrent proxy handling multiple client connections.
- Two backend services accessed through a centralized proxy.
- Configurable policies for client, service, and resource restrictions.
- Live policy-file reload and allow/deny logging.
- Automatic temporary SDN blocking after repeated denied requests.
- Ryu REST API for inspecting, applying, and removing client blocks.
- OpenFlow rules preventing the three client hosts from connecting directly to the backend TCP service ports.
- Controller-outage handling: local filtering continues for reachable clients; SDN API failures are logged. Background reconciliation after an outage is **not** implemented.

## Architecture

```text
h1 10.0.0.1 ─┐
h2 10.0.0.2 ─┼── s1 (OVS, OpenFlow 1.3) ── h4 10.0.0.4:9000 (TCP proxy)
h3 10.0.0.3 ─┘                    │                  │
                                     ├── h5 10.0.0.5:9001 (Service A)
                                     └── h6 10.0.0.6:9002 (Service B)

Ryu controller: 127.0.0.1:6653 (OpenFlow), 127.0.0.1:8080 (REST)
Proxy-to-controller management link: 192.168.100.2 ↔ 192.168.100.1
```

Clients send structured requests to the proxy. The proxy evaluates local policies before contacting a backend. After three denied requests from the same client, the proxy requests a timed SDN rule; the Ryu controller installs a high-priority drop flow matching that client's TCP connection to proxy port 9000. Separate backend-protection flows prevent clients h1–h3 from connecting directly to service ports 9001 and 9002.

## Project layout

```text
clients/
  client.py                 # CLI client
  concurrency_test.py      # Simultaneous-request test
  performance_test.py      # Direct-vs-proxy benchmark
common/
  __init__.py
  protocol.py               # JSON-over-TCP framing
config/
  policies.json             # Runtime filtering rules
controller/
  sdn_controller.py         # Ryu REST API and OpenFlow rules
proxy/
  policy_engine.py          # Local filtering decisions
  proxy_server.py           # Concurrent TCP proxy
  sdn_enforcer.py           # Violation tracking and SDN API calls
services/
  service_a.py
  service_b.py
topology/
  network.py               # Six-host Mininet network
logs/
  performance_results.csv  # Recorded benchmark output
```

## Environment

Tested on Ubuntu 24.04 with Python 3.12 for the application and a separate Python 3.10 virtual environment for Ryu 4.34. Mininet 2.3.0, Open vSwitch 3.3.9, and OpenFlow 1.3 were used. Ryu installation required compatibility adjustments on this machine; those environment-specific steps are **not** automated in this repository. Install Mininet/OVS and provision a compatible Ryu environment before running the commands below.

**Run commands from the repository root.** The topology needs `sudo` and creates network namespaces/interfaces; use a dedicated Linux testing environment. Run only one copy of each listener.

## Run the project

**Terminal 1 — Ryu controller** (using the previously configured Python 3.10 environment):

```bash
source ~/ryu310-venv/bin/activate
cd ~/sdn-tcp-proxy
ryu-manager --ofp-tcp-listen-port 6653 --wsapi-port 8080 controller/sdn_controller.py
```

**Terminal 2 — Mininet topology:**

```bash
cd ~/sdn-tcp-proxy
sudo python3 topology/network.py
```

At the `mininet>` prompt, check connectivity and start the application listeners if they are not already running:

```text
sh ovs-vsctl --columns=target,is_connected list Controller
h5 ss -ltn | grep 9001
h6 ss -ltn | grep 9002
h4 ss -ltn | grep 9000
```

Launch missing listeners, one command at a time in Mininet:

```text
h5 bash -c 'cd /home/gokul/sdn-tcp-proxy && nohup python3 -u -m services.service_a --host 10.0.0.5 --port 9001 > logs/service_a.log 2>&1 < /dev/null &'
h6 bash -c 'cd /home/gokul/sdn-tcp-proxy && nohup python3 -u -m services.service_b --host 10.0.0.6 --port 9002 > logs/service_b.log 2>&1 < /dev/null &'
h4 bash -c 'cd /home/gokul/sdn-tcp-proxy && nohup python3 -u -m proxy.proxy_server --host 10.0.0.4 --port 9000 > logs/proxy.log 2>&1 < /dev/null &'
```

**Note:** Replace `/home/gokul/sdn-tcp-proxy` with your actual checkout path if different. Some paths may also be hardcoded in `topology/network.py` or other modules; inspect them when cloning on another machine. The proxy is configured to use the controller management IP `192.168.100.1:8080`.

## Quick demo

Run these from the **Mininet prompt**, not the Ubuntu shell:

### 1. Permitted request

```text
h1 bash -c 'cd /home/gokul/sdn-tcp-proxy && python3 -m clients.client --service service_A --action READ --resource public_data'
```

Expected application response: `SUCCESS`.

### 2. Restricted request

```text
h2 bash -c 'cd /home/gokul/sdn-tcp-proxy && python3 -m clients.client --service service_A --action READ --resource student_records'
```

Expected application response: `ACCESS_DENIED`. Three denied requests from the same client trigger a temporary network-level block (default: 30 seconds).

### 3. Inspect SDN blocks

```text
sh curl -sS http://127.0.0.1:8080/policy/blocks
sh ovs-ofctl -O OpenFlow13 dump-flows s1
```

Look for a priority-100 TCP DROP flow for an automatically blocked client. The rule may disappear quickly when the 30-second ban expires.

### 4. Check backend protection

```text
sh ovs-ofctl -O OpenFlow13 dump-flows s1 | grep 'priority=90'
```

Six priority-90 rules should restrict h1–h3 from directly connecting to the two backend TCP ports. Confirm the rules' packet counters increase during direct-access attempts. The proxy h4 must remain able to contact both services.

### 5. Change policy at runtime

Edit `config/policies.json` to adjust blocked clients, resources, or services. The policy engine reloads the configuration when the file changes. Validate the JSON before testing.

## Testing and measured results

### Concurrency tests

```text
h1 bash -c 'cd /home/gokul/sdn-tcp-proxy && python3 -m clients.concurrency_test --clients 10'
h1 bash -c 'cd /home/gokul/sdn-tcp-proxy && python3 -m clients.concurrency_test --clients 20'
h1 bash -c 'cd /home/gokul/sdn-tcp-proxy && python3 -m clients.concurrency_test --clients 40'
```

| Connections | Successful | Failed | Elapsed time (s) | Mean latency (ms) |
|---:|---:|---:|---:|---:|
| 10 | 10 | 0 | 0.040 | 36.108 |
| 20 | 20 | 0 | 0.046 | 37.921 |
| 40 | 40 | 0 | 0.070 | 48.118 |

These are single-run results from a single Mininet load-generating host, not a statistical scalability study.

### Direct versus proxy benchmark

```text
h4 bash -c 'cd /home/gokul/sdn-tcp-proxy && python3 -m clients.performance_test --requests 100'
```

| Scenario | Successful | Failed | Mean latency (ms) | Median latency (ms) |
|---|---:|---:|---:|---:|
| Direct h4 → Service B | 100 | 0 | 16.508 | 16.478 |
| h4 → Local proxy on h4 → Service B | 100 | 0 | 17.255 | 17.145 |

Difference in mean latency: **0.747 ms**, approximately **4.53%** relative to the direct mean. This comparison deliberately runs from h4 so that backend-protection rules remain enabled. It measures local proxy-processing/forwarding overhead and **does not** include a remote client-to-proxy network hop. No bulk TCP throughput measurement was collected in this experiment.

Measured CSV: `logs/performance_results.csv`. Graphs can be placed under `docs/`.

## Limitations

- Controller outage: policy decisions can continue locally for already-routable traffic, but new SDN rules cannot be guaranteed while Ryu is unavailable.
- Controller-state reconciliation is request-triggered; there is no continuous background recovery worker.
- Bypass protection applies to the configured client IPs and backend TCP ports in this specific six-host topology, not to every conceivable path or protocol.
- The latency figures are single-run measurements with a small JSON payload and should not be presented as bulk bandwidth throughput.
- The controller REST API is intended only for the local/isolated lab management network; it does not provide general-purpose authentication.

## Project demonstration checklist

1. Show Mininet hosts and connected Ryu switch.
2. Show allowed request and denied request through the proxy.
3. Trigger three denials and show priority-100 OpenFlow DROP rule with packet counters.
4. Show automatic ban expiration and subsequent successful request.
5. Show priority-90 backend-protection flows.
6. Show 10/20/40-client concurrency results and direct-vs-proxy latency comparison.

## Technology

Python, TCP sockets, Mininet, Open vSwitch, OpenFlow 1.3, Ryu SDN controller, REST API, JSON.
