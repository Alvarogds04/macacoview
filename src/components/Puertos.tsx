import type { FullSnapshot } from "../types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function safe(v: number): number {
  return Number.isFinite(v) ? v : 0;
}

// ---------------------------------------------------------------------------
// Shared UI fragments (mirrors Recursos.tsx pattern)
// ---------------------------------------------------------------------------

function LoadingState() {
  return (
    <div className="loading-state">
      <span className="spinner" />
      <span>Esperando datos…</span>
    </div>
  );
}

function ErrorIndicator({ message }: { message: string }) {
  return <div className="error-banner">⚠️ {message}</div>;
}

function EmptyState({ title }: { title: string }) {
  return (
    <div className="placeholder">
      <p>No hay datos de {title} disponibles.</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function Puertos({
  snapshot,
  error,
}: {
  snapshot: FullSnapshot | null;
  error: boolean;
}) {
  // ── Loading / error ──────────────────────────────────────────────────────

  if (!snapshot) {
    return (
      <>
        {error && (
          <ErrorIndicator message="Error de conexión con el backend." />
        )}
        <LoadingState />
      </>
    );
  }

  const { ports } = snapshot;

  // ── Empty state ──────────────────────────────────────────────────────────

  if (ports.length === 0) {
    return <EmptyState title="puertos" />;
  }

  // ── Summary counts ───────────────────────────────────────────────────────

  let tcp = 0;
  let udp = 0;
  let exposed = 0;
  for (const row of ports) {
    const protoUpper = row.proto.toUpperCase();
    if (protoUpper === "TCP") tcp++;
    if (protoUpper === "UDP") udp++;
    if (row.classification === "🌐 Todas") exposed++;
  }

  // ── Table rows (Rust already sorted: proto then port ascending) ──────────

  const rows = ports.map((row) => {
    const isExposed = row.classification === "🌐 Todas";
    return (
      <tr
        key={`${row.proto}-${row.port}-${row.local_address}`}
        className={isExposed ? "port-row-exposed" : "port-row"}
      >
        <td>{row.proto.toUpperCase()}</td>
        <td className="col-num">{String(safe(row.port))}</td>
        <td>{row.classification}</td>
        <td>{row.service_name}</td>
        <td>{row.process_name}</td>
        <td className="col-num">{String(safe(row.pid))}</td>
        <td>{row.local_address}</td>
      </tr>
    );
  });

  return (
    <div className="puertos-content">
      {error && (
        <ErrorIndicator message="Error de conexión con el backend (mostrando los últimos datos válidos)." />
      )}

      <div className="ports-summary">
        TCP: {tcp} &nbsp;&nbsp; UDP: {udp} &nbsp;&nbsp; 🌐 Todas interfaces:{" "}
        {exposed}
      </div>

      <table className="table ports-table">
        <thead>
          <tr>
            <th>TIPO</th>
            <th>PUERTO</th>
            <th>ACCESO</th>
            <th>SERVICIO</th>
            <th>PROCESO</th>
            <th>PID</th>
            <th>LOCAL</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  );
}
