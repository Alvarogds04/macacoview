import type { FullSnapshot } from "../types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function safe(v: number): number {
  return Number.isFinite(v) ? v : 0;
}

function fmtRAM(rssKb: number): string {
  const kb = safe(rssKb);
  return `${Math.round(kb / 1024)}M`;
}

function fmtCPU(cpu: number): string {
  return `${safe(cpu).toFixed(1)}%`;
}

function truncArgs(args: string): string {
  return args.length > 75 ? args.slice(0, 72) + "..." : args;
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

export function Procesos({
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

  const { processes } = snapshot;

  // ── Empty state ──────────────────────────────────────────────────────────

  if (processes.length === 0) {
    return <EmptyState title="procesos" />;
  }

  // ── Top-60 by RSS (sorted copy, never mutates snapshot) ──────────────────

  const top60 = [...processes]
    .sort((a, b) => safe(b.rss_kb) - safe(a.rss_kb))
    .slice(0, 60);

  const rows = top60.map((p) => (
    <tr key={p.pid} className="process-row">
      <td className="col-num">{String(safe(p.pid))}</td>
      <td className="col-num">{fmtRAM(p.rss_kb)}</td>
      <td className="col-num">{fmtCPU(p.cpu)}</td>
      <td>{p.comm}</td>
      <td className="col-args">{truncArgs(p.args)}</td>
    </tr>
  ));

  return (
    <div className="procesos-content">
      {error && (
        <ErrorIndicator message="Error de conexión con el backend (mostrando los últimos datos válidos)." />
      )}

      <table className="table processes-table">
        <thead>
          <tr>
            <th>PID</th>
            <th>RAM</th>
            <th>CPU</th>
            <th>PROCESO</th>
            <th>COMANDO</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  );
}
