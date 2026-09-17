use serde::Serialize;
use std::sync::LazyLock;

// ---------------------------------------------------------------------------
// Data returned from ss line parsing
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct PortRow {
    pub proto: String,
    pub state: String,
    pub local_address: String,
    pub peer_address: String,
    pub port: u16,
    pub classification: Classification,
    pub service_name: String,
    pub process_name: String,
    pub pid: u32,
}

/// Classification of a listening address.
///
/// The `serde(rename)` values are literal emoji+text that the frontend
/// expects to match against.
#[derive(Debug, Clone, Serialize)]
pub enum Classification {
    #[serde(rename = "\u{1f512} Local")]
    Local,
    #[serde(rename = "\u{1f3e0} LAN")]
    Lan,
    #[serde(rename = "\u{1f537} Tailscale")]
    Tailscale,
    #[serde(rename = "\u{1f310} Todas")]
    Todas,
    #[serde(rename = "\u{1f4e1} Interfaz")]
    Interfaz,
}

// ---------------------------------------------------------------------------
// Named service table
// ---------------------------------------------------------------------------

static NAMED_PORTS: &[(u16, &str)] = &[
    (22, "SSH"),
    (53, "DNS"),
    (80, "Nginx / HTTP"),
    (631, "CUPS"),
    (3000, "NutriDuo / Next.js"),
    (3306, "MariaDB"),
    (3389, "Remote Desktop"),
    (3390, "Remote Desktop"),
    (4171, "Abito CV agent-1"),
    (4172, "Abito CV agent-2"),
    (4174, "Abito CV agent-4"),
    (5432, "PostgreSQL"),
    (7437, "Engram"),
    (8081, "PHP"),
    (8787, "Caveman Proxy"),
    (9000, "Lemmond"),
    (10100, "Magnitude"),
    (11002, "Abito-gpt"),
    (11003, "Second Brain"),
    (11004, "Personal-gpt"),
    (11434, "Ollama"),
    (18080, "Engram / Tailscale"),
    (24543, "Moshi"),
];

// ---------------------------------------------------------------------------
// Regex for process extraction from ss `users:(...)` field
//
// Matches: ("process_name",pid=12345,...)
// Groups: 1 = process_name, 2 = pid
// ---------------------------------------------------------------------------

static PROCESS_RE: LazyLock<regex::Regex> = LazyLock::new(|| {
    regex::Regex::new(r#"\("([^"]+)",pid=(\d+)"#).unwrap()
});

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Parse a single `ss` output line into a `PortRow`.
/// Returns `None` for lines with fewer than 6 whitespace-separated fields.
pub fn parse_ss_line(line: &str) -> Option<PortRow> {
    // Use a simple state-machine split that collapses consecutive whitespace
    // and respects the field limit.
    let mut fields: Vec<&str> = Vec::with_capacity(7);
    let mut start: Option<usize> = None;
    let bytes = line.as_bytes();
    let mut field_count = 0;
    let mut i = 0;
    while i < bytes.len() && field_count < 7 {
        if bytes[i].is_ascii_whitespace() {
            if let Some(s) = start.take() {
                fields.push(&line[s..i]);
                field_count += 1;
            }
        } else if start.is_none() {
            start = Some(i);
        }
        i += 1;
    }
    // Capture last field if we haven't hit the limit
    if let Some(s) = start {
        fields.push(&line[s..]);
    }

    if fields.len() < 6 {
        return None;
    }

    let proto = fields[0].to_string();
    let state = fields[1].to_string();
    let local_address = fields[4].to_string();
    let peer_address = fields[5].to_string();

    // Extract port from local address
    let port = extract_port(&local_address);

    // Extract process name and pid from the process field (field 6, if present).
    // Regex: \("([^"]+)",pid=(\d+)
    //   Group 1 = process name, Group 2 = pid
    let (process_name, pid) = if fields.len() > 6 {
        let proc_field = fields[6];
        if let Some(caps) = PROCESS_RE.captures(proc_field) {
            let name = caps.get(1).map(|m| m.as_str()).unwrap_or("-").to_string();
            let pid_val = caps
                .get(2)
                .and_then(|m| m.as_str().parse().ok())
                .unwrap_or(0);
            (name, pid_val)
        } else {
            ("-".to_string(), 0u32)
        }
    } else {
        ("-".to_string(), 0u32)
    };

    let classification = classify_local_address(&local_address);
    let service_name = name_service(port, &process_name);

    Some(PortRow {
        proto,
        state,
        local_address,
        peer_address,
        port,
        classification,
        service_name,
        process_name,
        pid,
    })
}

/// Parse multiple ss lines, returning sorted `PortRow` slice.
/// Rows are sorted by protocol, then by port.
pub fn parse_ss_output(input: &str) -> Vec<PortRow> {
    let mut rows: Vec<PortRow> = input
        .lines()
        .filter_map(|line| parse_ss_line(line.trim()))
        .collect();
    rows.sort_by(|a, b| {
        a.proto
            .cmp(&b.proto)
            .then_with(|| a.port.cmp(&b.port))
    });
    rows
}

/// Extract the port number from a local address string.
/// Strips trailing `:port`; returns 0 when it cannot be parsed.
///
/// Handles IPv4 `addr:port`, IPv6 `[addr]:port`, bare `*:port`, and bare `addr`.
pub fn extract_port(local_address: &str) -> u16 {
    // Strip surrounding brackets for IPv6
    let stripped = local_address.strip_prefix('[').unwrap_or(local_address);
    if let Some(inner) = stripped.strip_suffix(']') {
        // IPv6: [addr]:port
        if let Some(colon) = inner.rfind(':') {
            let port_str = &inner[colon + 1..];
            return port_str.parse().unwrap_or(0);
        }
        return 0;
    }
    // IPv4 or bare: strip last :port
    if let Some(colon) = local_address.rfind(':') {
        let port_str = &local_address[colon + 1..];
        return port_str.parse().unwrap_or(0);
    }
    0
}

/// Classify a local address into one of five categories.
///
/// Before matching, extract the host from the local address:
/// strip surrounding `[...]` to get the host and remove any
/// `%lo` (or `%<iface>`) suffix.  Also strip the trailing `:port`
/// so only the host is matched against the classification patterns.
///
/// Classification order (first match wins):
/// 1. `🔒 Local` for `127.*` or `::1`
/// 2. `🏠 LAN` for `192.168.*`, `10.*`, or `172.*`
/// 3. `🔷 Tailscale` for `100.*` or `fd7a:*`
/// 4. `🌐 Todas` for `0.0.0.0`, `*`, `::`, `[::]`
/// 5. `📡 Interfaz` otherwise
pub fn classify_local_address(local_address: &str) -> Classification {
    // Step 1: Extract the host, stripping brackets and port.
    // Formats:
    //   IPv4:    192.168.1.1:443
    //   IPv4*:   *:443
    //   IPv6:    [2001:db8::1]:443
    //   IPv6*:   [::]:443, [::1]:8080, [fd7a::1]:443
    //   IPv6lo:  127.0.0.53%lo:53
    //   IPv6lo*: [fe80::1]%wlp195s0:546
    let host = extract_host(local_address);

    // Classification in exact order
    if host == "127.0.0.1" || host.starts_with("127.") {
        return Classification::Local;
    }
    if host == "::1" {
        return Classification::Local;
    }

    if host.starts_with("192.168.") {
        return Classification::Lan;
    }
    if host.starts_with("10.") {
        return Classification::Lan;
    }
    if host.starts_with("172.") {
        return Classification::Lan;
    }

    if host.starts_with("100.") {
        return Classification::Tailscale;
    }
    if host.starts_with("fd7a:") {
        return Classification::Tailscale;
    }

    if host == "0.0.0.0" || host == "*" || host == "::" {
        return Classification::Todas;
    }

    Classification::Interfaz
}

/// Extract the bare host from a local address string, stripping brackets,
/// zone ids, and trailing `:port`.
pub fn extract_host(local_address: &str) -> &str {
    // Step 1: Strip brackets
    let after_brackets = if let Some(inner) = local_address.strip_prefix('[') {
        // Remove the closing ] that precedes :port
        inner.split(']').next().unwrap_or(inner)
    } else {
        local_address
    };

    // Step 2: Remove zone id (e.g. %lo, %wlp195s0)
    let after_zone = if let Some(percent) = after_brackets.find('%') {
        &after_brackets[..percent]
    } else {
        after_brackets
    };

    // Step 3: Strip trailing :port
    // After bracket/zone removal, the string is either:
    //   - bare IPv4 like "192.168.1.1" (no colon → no port)
    //   - bare IPv4 with port like "192.168.1.1:443"
    //   - bare IPv6 like "::1" (has colons but no port)
    //   - bare IPv6 with port like "::1:443" (ambiguous!)
    //   - bare * with port like "*:443"
    // We handle the ambiguous case: if the part after the LAST colon
    // parses as a port AND the string contains a colon that's not
    // part of the IPv6 prefix, we strip it.
    // For IPv6 like "::1", the last colon is at position 1, and
    // "1" parses as port 1. But "::1" is the loopback, not "::" with port 1.
    //
    // Heuristic: if the address contains a colon and the part after
    // the last colon is a valid port, and the remaining string
    // is NOT a valid IPv6 address pattern, strip the port.
    // For simplicity, we check if stripping the last colon-terminated
    // segment yields something that doesn't end with a colon (which
    // would indicate it's part of the IPv6 address).
    if let Some(last_colon) = after_zone.rfind(':') {
        let after = &after_zone[last_colon + 1..];
        if after.parse::<u16>().is_ok() {
            // Check if the remaining part doesn't end with ':'
            // (which would mean the colon was part of the IPv6 address)
            let remaining = &after_zone[..last_colon];
            if !remaining.ends_with(':') {
                return remaining;
            }
        }
    }
    after_zone
}

/// Name the service based on port lookup table first, then process-name fallback.
///
/// Named table takes priority. When the port is not in the table, fall back
/// on the process name: contains `hermes` → `Hermes`, contains `llama-server`
/// → `llama.cpp`, contains `ollama` → `Ollama`, contains `tailscale` →
/// `Tailscale`, otherwise return the process name unchanged.
pub fn name_service(port: u16, process_name: &str) -> String {
    // Check named table first
    if let Some(&(_, name)) = NAMED_PORTS.iter().find(|(p, _)| *p == port) {
        return name.to_string();
    }

    // Fallback to process name
    if process_name.contains("hermes") {
        "Hermes".to_string()
    } else if process_name.contains("llama-server") {
        "llama.cpp".to_string()
    } else if process_name.contains("ollama") {
        "Ollama".to_string()
    } else if process_name.contains("tailscale") {
        "Tailscale".to_string()
    } else {
        process_name.to_string()
    }
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // ---- Port extraction ----

    #[test]
    fn test_extract_port_ipv4() {
        assert_eq!(extract_port("127.0.0.1:8080"), 8080);
        assert_eq!(extract_port("0.0.0.0:22"), 22);
        assert_eq!(extract_port("192.168.1.1:443"), 443);
    }

    #[test]
    fn test_extract_port_ipv6() {
        assert_eq!(extract_port("[::1]:8080"), 8080);
        assert_eq!(extract_port("[2001:db8::1]:443"), 443);
    }

    #[test]
    fn test_extract_port_wildcards() {
        assert_eq!(extract_port("*:80"), 80);
        assert_eq!(extract_port("[::]:443"), 443);
    }

    #[test]
    fn test_extract_port_no_port() {
        // Edge: no colon at all
        assert_eq!(extract_port("127.0.0.1"), 0);
    }

    // ---- Address classification ----

    #[test]
    fn test_classify_local_127() {
        assert!(matches!(
            classify_local_address("127.0.0.1:3306"),
            Classification::Local
        ));
        assert!(matches!(
            classify_local_address("127.0.0.53:53"),
            Classification::Local
        ));
        assert!(matches!(
            classify_local_address("127.0.0.54:53"),
            Classification::Local
        ));
    }

    #[test]
    fn test_classify_local_ipv6_loopback() {
        assert!(matches!(
            classify_local_address("[::1]:8080"),
            Classification::Local
        ));
    }

    #[test]
    fn test_classify_lan_192_168() {
        assert!(matches!(
            classify_local_address("192.168.1.10:49431"),
            Classification::Lan
        ));
    }

    #[test]
    fn test_classify_lan_10() {
        assert!(matches!(
            classify_local_address("10.0.0.1:22"),
            Classification::Lan
        ));
    }

    #[test]
    fn test_classify_lan_172() {
        assert!(matches!(
            classify_local_address("172.16.0.1:8080"),
            Classification::Lan
        ));
    }

    #[test]
    fn test_classify_tailscale_100() {
        assert!(matches!(
            classify_local_address("100.64.0.1:443"),
            Classification::Tailscale
        ));
    }

    #[test]
    fn test_classify_tailscale_fd7a() {
        assert!(matches!(
            classify_local_address("[fd7a:115c:a1e0::1]:443"),
            Classification::Tailscale
        ));
    }

    #[test]
    fn test_classify_todas_0() {
        assert!(matches!(
            classify_local_address("0.0.0.0:80"),
            Classification::Todas
        ));
    }

    #[test]
    fn test_classify_todas_star() {
        assert!(matches!(
            classify_local_address("*:3390"),
            Classification::Todas
        ));
    }

    #[test]
    fn test_classify_todas_ipv6_any() {
        assert!(matches!(
            classify_local_address("[::]:80"),
            Classification::Todas
        ));
    }

    // ---- Link-local suffix stripping ----

    #[test]
    fn test_classify_with_lo_suffix() {
        // 127.0.0.53%lo:53 → host "127.0.0.53" → Local
        assert!(matches!(
            classify_local_address("127.0.0.53%lo:53"),
            Classification::Local
        ));
    }

    #[test]
    fn test_classify_with_iface_suffix() {
        // [fe80::5025:13a6:ef29:6f9f]%wlp195s0:546 → host "fe80::..." → Interfaz
        assert!(matches!(
            classify_local_address("[fe80::5025:13a6:ef29:6f9f]%wlp195s0:546"),
            Classification::Interfaz
        ));
    }

    // ---- Service naming ----

    #[test]
    fn test_named_port_ssh() {
        assert_eq!(name_service(22, "sshd"), "SSH");
    }

    #[test]
    fn test_named_port_nginx() {
        assert_eq!(name_service(80, "nginx"), "Nginx / HTTP");
    }

    #[test]
    fn test_named_port_11002() {
        // Port 11002 is "Abito-gpt" in the named table
        assert_eq!(name_service(11002, "llama-server"), "Abito-gpt");
    }

    #[test]
    fn test_named_port_11003() {
        assert_eq!(name_service(11003, "sb-bridge"), "Second Brain");
    }

    #[test]
    fn test_named_port_11004() {
        assert_eq!(name_service(11004, "llama-server"), "Personal-gpt");
    }

    #[test]
    fn test_named_port_11434() {
        assert_eq!(name_service(11434, "ollama"), "Ollama");
    }

    #[test]
    fn test_named_port_24543() {
        assert_eq!(name_service(24543, "moshi-hook"), "Moshi");
    }

    #[test]
    fn test_named_port_7437() {
        assert_eq!(name_service(7437, "engram"), "Engram");
    }

    #[test]
    fn test_named_port_8081() {
        assert_eq!(name_service(8081, "php-fpm"), "PHP");
    }

    // ---- Fallback service naming ----

    #[test]
    fn test_fallback_hermes() {
        assert_eq!(name_service(u16::MAX, "hermes"), "Hermes");
        assert_eq!(name_service(u16::MAX, "my-hermes-app"), "Hermes");
    }

    #[test]
    fn test_fallback_llama_server() {
        assert_eq!(name_service(u16::MAX, "llama-server"), "llama.cpp");
    }

    #[test]
    fn test_fallback_ollama() {
        assert_eq!(name_service(u16::MAX, "ollama"), "Ollama");
    }

    #[test]
    fn test_fallback_tailscale() {
        assert_eq!(name_service(u16::MAX, "tailscaled"), "Tailscale");
    }

    #[test]
    fn test_fallback_unknown() {
        assert_eq!(name_service(u16::MAX, "custom-daemon"), "custom-daemon");
    }

    // ---- Full line parsing ----

    #[test]
    fn test_parse_tcp_127_0_0_1() {
        let line = r#"tcp LISTEN 0 511                                    127.0.0.1:80    0.0.0.0:* users:(("nginx",pid=9869,fd=5))"#;
        let row = parse_ss_line(line).expect("should parse");
        assert_eq!(row.proto, "tcp");
        assert_eq!(row.state, "LISTEN");
        assert_eq!(row.port, 80);
        assert_eq!(row.process_name, "nginx");
        assert_eq!(row.pid, 9869);
        assert!(matches!(row.classification, Classification::Local));
        assert_eq!(row.service_name, "Nginx / HTTP");
    }

    #[test]
    fn test_parse_tcp_11002_abito_gpt() {
        // This is the key test: port 11002 should resolve to "Abito-gpt"
        // classified as "🔒 Local"
        let line = r#"tcp LISTEN 0 512                                    127.0.0.1:11002 0.0.0.0:* users:(("llama-server",pid=767149,fd=6))"#;
        let row = parse_ss_line(line).expect("should parse");
        assert_eq!(row.proto, "tcp");
        assert_eq!(row.port, 11002);
        assert_eq!(row.process_name, "llama-server");
        assert_eq!(row.pid, 767149);
        assert!(
            matches!(row.classification, Classification::Local),
            "port 11002 on 127.0.0.1 should be Local"
        );
        assert_eq!(row.service_name, "Abito-gpt");
    }

    #[test]
    fn test_parse_udp() {
        let line = r#"udp UNCONN 0      0                                     127.0.0.54%lo:53    0.0.0.0:* users:(("systemd-resolve",pid=699,fd=18))"#;
        let row = parse_ss_line(line).expect("should parse");
        assert_eq!(row.proto, "udp");
        assert_eq!(row.state, "UNCONN");
        assert_eq!(row.port, 53);
        assert_eq!(row.process_name, "systemd-resolve");
        assert_eq!(row.pid, 699);
        assert!(
            matches!(row.classification, Classification::Local),
            "127.0.0.53%lo:53 should be Local after stripping %lo"
        );
        assert_eq!(row.service_name, "DNS");
    }

    #[test]
    fn test_parse_tailscale_fd7a() {
        let line = r#"tcp LISTEN 0 4096                 [fd7a:115c:a1e0::1]:443      [::]:* users:(("tailscaled",pid=2724,fd=21))"#;
        let row = parse_ss_line(line).expect("should parse");
        assert_eq!(row.port, 443);
        assert_eq!(row.process_name, "tailscaled");
        assert_eq!(row.pid, 2724);
        assert!(
            matches!(row.classification, Classification::Tailscale),
            "fd7a:* should be Tailscale"
        );
        assert_eq!(row.service_name, "Tailscale");
    }

    #[test]
    fn test_parse_star_local() {
        let line = r#"tcp LISTEN 0      5                                              *:3390        *:* users:(("gnome-remote-de",pid=14761,fd=18))"#;
        let row = parse_ss_line(line).expect("should parse");
        assert_eq!(row.port, 3390);
        assert!(
            matches!(row.classification, Classification::Todas),
            "*:3390 should be Todas"
        );
        assert_eq!(row.service_name, "Remote Desktop");
    }

    #[test]
    fn test_parse_short_line() {
        // Lines with fewer than 6 fields should return None
        assert!(parse_ss_line("tcp").is_none());
        assert!(parse_ss_line("tcp LISTEN 0 4096").is_none());
    }

    #[test]
    fn test_parse_no_process_info() {
        // A line with only 5 fields (no process info)
        let line = "tcp LISTEN 0 4096 127.0.0.1:80 0.0.0.0:*";
        let row = parse_ss_line(line).expect("should parse with 5+ fields");
        assert_eq!(row.process_name, "-");
        assert_eq!(row.pid, 0);
    }

    // ---- Sorting ----

    #[test]
    fn test_parse_output_sorted_by_proto_then_port() {
        let input = r#"tcp LISTEN 0 5 *:3390 *:* users:(("gnome-remote-de",pid=14761,fd=18))
udp UNCONN 0      0                                     127.0.0.54:53    0.0.0.0:* users:(("systemd-resolve",pid=699,fd=18))
tcp LISTEN 0 511                                      0.0.0.0:80    0.0.0.0:* users:(("nginx",pid=9869,fd=5))
udp UNCONN 0      0                                        0.0.0.0:5353  0.0.0.0:* users:(("avahi-daemon",pid=2000,fd=12))"#;
        let rows = parse_ss_output(input);
        // Ascending order by protocol string, then by port: "tcp" sorts before
        // "udp", which is the order the GTK app produced as well.
        assert_eq!(rows[0].port, 80);
        assert_eq!(rows[0].proto, "tcp");
        assert_eq!(rows[1].port, 3390);
        assert_eq!(rows[1].proto, "tcp");
        assert_eq!(rows[2].port, 53);
        assert_eq!(rows[2].proto, "udp");
        assert_eq!(rows[3].port, 5353);
        assert_eq!(rows[3].proto, "udp");
    }

    // ---- Full baseline integration test ----

    #[test]
    fn test_parse_baseline_ports_file() {
        let input = std::fs::read_to_string(
            "/home/alvaro/.local/state/pc-ai-monitor/backups/20260917T125433Z-pre-tauri/baseline/ports-ss.txt",
        )
        .expect("baseline file should be readable");
        let rows = parse_ss_output(&input);

        // Find port 11002 row
        let abito_gpt = rows.iter().find(|r| r.port == 11002);
        assert!(
            abito_gpt.is_some(),
            "port 11002 should be present in baseline"
        );
        let abito_gpt = abito_gpt.unwrap();
        assert_eq!(
            abito_gpt.service_name, "Abito-gpt",
            "port 11002 should resolve to Abito-gpt"
        );
        assert!(
            matches!(abito_gpt.classification, Classification::Local),
            "port 11002 (127.0.0.1) should be Local"
        );

        // Count exposed-to-all-interfaces (Classification::Todas).
        // The baseline holds 12 such listeners: tcp *:11434, *:3389, *:3390,
        // 0.0.0.0:22, 0.0.0.0:80, [::]:22, [::]:80, plus udp 0.0.0.0:34530,
        // 0.0.0.0:41641, 0.0.0.0:5353, [::]:41641 and [::]:5353.
        let exposed_count = rows
            .iter()
            .filter(|r| matches!(r.classification, Classification::Todas))
            .count();
        assert_eq!(exposed_count, 12, "exposed-to-all count should be 12");
    }
}
