"""Parse the privileged ``ss`` output.

Mirrors the collector's own rules (the Rust ``ports.rs`` it replaced) so a port is
classified and named exactly the same way here and in the Tauri app: address
family decides the access badge, and the named table wins over the process name.
"""

import re
from dataclasses import dataclass
from ipaddress import ip_address

LOCAL = "🔒 Local"
LAN = "🏠 LAN"
TAILSCALE = "🔷 Tailscale"
ALL = "🌐 Todas"
IFACE = "📡 Interfaz"

# Ports worth naming. Anything else falls back to the process name.
NAMED_PORTS = {
    22: "SSH",
    53: "DNS",
    80: "Nginx / HTTP",
    631: "CUPS",
    3000: "NutriDuo / Next.js",
    3306: "MariaDB",
    3389: "Remote Desktop",
    3390: "Remote Desktop",
    4000: "LiteLLM Proxy",
    4171: "Abito CV agent-1",
    4172: "Abito CV agent-2",
    4174: "Abito CV agent-4",
    5432: "PostgreSQL",
    7437: "Engram",
    8081: "PHP",
    8787: "Caveman Proxy",
    9000: "Lemmond",
    10100: "Magnitude",
    11002: "Abito-gpt",
    11003: "Second Brain",
    11004: "Personal-gpt",
    11434: "Ollama",
    18080: "Engram / Tailscale",
    24543: "Moshi",
}

PROCESS_RE = re.compile(r'\("([^"]+)",pid=(\d+)')
PROCESS_NAMES = (
    ("hermes", "Hermes"),
    ("llama-server", "llama.cpp"),
    ("ollama", "Ollama"),
    ("tailscale", "Tailscale"),
)


@dataclass(frozen=True)
class PortRow:
    proto: str
    state: str
    local: str
    port: int
    access: str
    service: str
    process: str
    pid: int

    @property
    def exposed(self) -> bool:
        """Listening on every interface: the only rows worth warning about."""
        return self.access == ALL


def _as_int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        return 0


def host_of(local: str) -> str:
    """Bare host from a local address: no brackets, no zone, no port."""
    text = local
    if text.startswith("["):
        text = text.split("]", 1)[0][1:]
    text = text.split("%", 1)[0]
    if ":" in text:
        head, _, tail = text.rpartition(":")
        if tail.isdigit() and head and not head.endswith(":"):
            text = head
    return text


def port_of(local: str) -> int:
    tail = local.rsplit(":", 1)[-1].rstrip("]")
    return _as_int(tail)


def is_wildcard(host: str) -> bool:
    """True when the host is the unspecified address `ss` prints for "everywhere".

    Asking the standard library keeps this precise (it covers IPv4 and IPv6) and
    avoids hard-coding a bind-all literal in a module that only reads text.
    """
    if host == "*":
        return True
    try:
        return ip_address(host).is_unspecified
    except ValueError:
        return False


def classify(local: str) -> str:
    host = host_of(local)
    if host.startswith("127.") or host == "::1":
        return LOCAL
    if host.startswith(("192.168.", "10.", "172.")):
        return LAN
    if host.startswith(("100.", "fd7a:")):
        return TAILSCALE
    if is_wildcard(host):
        return ALL
    return IFACE


def service_name(port: int, process: str) -> str:
    named = NAMED_PORTS.get(port)
    if named is not None:
        return named
    lowered = process.lower()
    for needle, name in PROCESS_NAMES:
        if needle in lowered:
            return name
    return process


def parse_ss(text: str) -> list[PortRow]:
    """Rows sorted by protocol and port, skipping anything unparsable."""
    rows: list[PortRow] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6:
            continue

        local = fields[4]
        port = port_of(local)
        process, pid = "-", 0
        found = PROCESS_RE.search(line)
        if found is not None:
            process, pid = found.group(1), _as_int(found.group(2))

        rows.append(
            PortRow(
                proto=fields[0],
                state=fields[1],
                local=local,
                port=port,
                access=classify(local),
                service=service_name(port, process),
                process=process,
                pid=pid,
            )
        )

    rows.sort(key=lambda row: (row.proto, row.port))
    return rows


def counts(rows: list[PortRow]) -> tuple[int, int, int]:
    """(tcp, udp, exposed) — the three headline numbers of the tab."""
    tcp = sum(1 for row in rows if row.proto == "tcp")
    udp = sum(1 for row in rows if row.proto == "udp")
    exposed = sum(1 for row in rows if row.exposed)
    return tcp, udp, exposed
