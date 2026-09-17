import type { FullSnapshot, Model, HistorySample } from "../types";
import { GROUP_KEYS, GROUP_LABELS } from "../types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function modelIcon(alias: string): string {
  const lower = alias.toLowerCase();
  if (lower.includes("abito")) return "🤖";
  if (lower.includes("personal")) return "🧠";
  return "🧩";
}

function fmtOneDecimal(v: number): string {
  return v.toFixed(1);
}

function fmtTwoDecimals(v: number): string {
  return v.toFixed(2);
}

function safe(v: number): number {
  return Number.isFinite(v) ? v : 0;
}

// ---------------------------------------------------------------------------
// Sparkline — reusable inline SVG sparkline
// ---------------------------------------------------------------------------

function Sparkline({
  data,
  color,
  label,
  value,
}: {
  data: readonly number[];
  color: string;
  label: string;
  value: string;
}) {
  const filtered = data.filter(Number.isFinite);

  let points: string | undefined;

  if (filtered.length === 0) {
    // No data — flat line at the vertical center
    points = "0,14 200,14";
  } else if (filtered.length === 1) {
    // A single sample has no shape to draw: render it flat, exactly like a
    // constant series, instead of deriving a meaningless position from the
    // magnitude of the only value.
    points = "0,27 200,27";
  } else {
    const min = Math.min(...filtered);
    const max = Math.max(...filtered);
    const range = max - min;
    const clampedRange = range < 0.01 ? 0.01 : range;

    points = filtered
      .map(
        (v, i) =>
          `${(i / (filtered.length - 1)) * 200},${27 - ((v - min) / clampedRange) * 27}`
      )
      .join(" ");
  }

  return (
    <svg
      viewBox="0 0 200 28"
      preserveAspectRatio="none"
      className="history-sparkline"
      role="img"
      aria-label={`${label}: ${value}`}
    >
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
        points={points}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// History row — one label + sparkline + value
// ---------------------------------------------------------------------------

const SPARKLINE_COLORS: Record<string, string> = {
  used: "#4a90d9",
  avail: "#3cc97a",
  pi: "#e0a040",
  hermes: "#d95080",
  firefox: "#7b6ee6",
  system: "#6cc4d9",
  other: "#9a9a9a",
};

function HistoryRow({
  label,
  color,
  value,
  series,
}: {
  label: string;
  color: string;
  value: string;
  series: readonly number[];
}) {
  return (
    <div className="history-row">
      <span className="history-label">{label}</span>
      <Sparkline data={series} color={color} label={label} value={value} />
      <span className="history-value">{value}G</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// History section (5-min sparkline chart)
// ---------------------------------------------------------------------------

const MAX_SAMPLES = 300;

function HistorySection({ history }: { history: HistorySample[] }) {
  const sampleCount = history.length;
  const isFilling = sampleCount === 0;

  if (isFilling) {
    return (
      <Section title="HISTORIAL (5 MIN)">
        <div className="history-section">
          <p className="history-placeholder">
            Acumulando datos… (0 / {MAX_SAMPLES} muestras)
          </p>
        </div>
      </Section>
    );
  }

  const latest = history[sampleCount - 1];
  const usedSeries = history.map((s) => s.used_gib);
  const availSeries = history.map((s) => s.available_gib);

  const rows: Array<{ key: string; label: string; value: string; series: number[] }> =
    [
      {
        key: "used",
        label: "RAM usada",
        value: fmtOneDecimal(latest.used_gib),
        series: usedSeries,
      },
      {
        key: "avail",
        label: "RAM disponible",
        value: fmtOneDecimal(latest.available_gib),
        series: availSeries,
      },
      ...GROUP_KEYS.map((key, idx) => ({
        key,
        label: GROUP_LABELS[key],
        value: fmtTwoDecimals(latest.group_rss_gib[idx]),
        series: history.map((s) => s.group_rss_gib[idx]),
      })),
    ];

  return (
    <Section title="HISTORIAL (5 MIN)">
      <div className="history-section">
        <p className="history-placeholder">
          Ventana: {sampleCount} / {MAX_SAMPLES} muestras
        </p>
        {rows.map((row) => (
          <HistoryRow
            key={row.key}
            label={row.label}
            color={SPARKLINE_COLORS[row.key] ?? SPARKLINE_COLORS.other}
            value={row.value}
            series={row.series}
          />
        ))}
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Model row
// ---------------------------------------------------------------------------

function ModelRow({ model }: { model: Model }) {
  const icon = modelIcon(model.alias);
  const alias = model.alias;
  const gtt = fmtOneDecimal(safe(model.gtt_gib));
  const rss = fmtOneDecimal(safe(model.rss_gib));
  const cpu = fmtOneDecimal(safe(model.cpu));
  const pid = String(model.pid);
  const puerto = model.port === null ? "-" : String(model.port);

  return (
    <tr className="model-row">
      <td className="col-model">
        {icon} {alias}
      </td>
      <td className="col-num">{gtt}G</td>
      <td className="col-num">{rss}G</td>
      <td className="col-num">{cpu}%</td>
      <td className="col-num">{pid}</td>
      <td className="col-num">{puerto}</td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Section component
// ---------------------------------------------------------------------------

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="section">
      <h2 className="section-title">{title}</h2>
      <div className="section-body">{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Main Recursos component
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

export function Recursos({
  snapshot,
  error,
  history,
}: {
  snapshot: FullSnapshot | null;
  error: boolean;
  history: HistorySample[];
}) {
  // ── Loading / error ──────────────────────────────────────────────────────

  if (!snapshot) {
    return (
      <>
        {error && <ErrorIndicator message="Error de conexión con el backend." />}
        <LoadingState />
      </>
    );
  }

  const { memory, models, groups } = snapshot;

  // ── Models table ─────────────────────────────────────────────────────────

  const modelRows = models.map((m) => <ModelRow key={m.pid} model={m} />);

  // ── Groups table ─────────────────────────────────────────────────────────

  const groupRows = GROUP_KEYS.map((key) => {
    const g = groups[key];
    const label = GROUP_LABELS[key];
    const rss = g ? fmtTwoDecimals(safe(g.rss_gib)) : "0.00";
    const cpu = g ? fmtOneDecimal(safe(g.cpu)) : "0.0";
    const pids = g ? g.pids.length : 0;

    return (
      <tr key={key} className="group-row">
        <td className="col-group">{label}</td>
        <td className="col-num">{rss}G</td>
        <td className="col-num">{cpu}%</td>
        <td className="col-num">{pids}</td>
      </tr>
    );
  });

  // ── Memory section ───────────────────────────────────────────────────────

  const memTotal = fmtOneDecimal(safe(memory.total_gib));
  const memUsed = fmtOneDecimal(safe(memory.used_gib));
  const memAvail = fmtOneDecimal(safe(memory.available_gib));
  const swapUsed = fmtOneDecimal(safe(memory.swap_used_gib));
  const swapTotal = fmtOneDecimal(safe(memory.swap_total_gib));

  return (
    <div className="recursos-content">
      {/* A failed poll keeps the last good snapshot on screen: the banner
          warns without throwing the data away. */}
      {error && (
        <ErrorIndicator message="Error de conexión con el backend (mostrando los últimos datos válidos)." />
      )}

      {/* Models */}
      <Section title="MODELOS LOCALES">
        <table className="table model-table">
          <thead>
            <tr>
              <th>MODELO</th>
              <th>GTT</th>
              <th>RSS</th>
              <th>CPU</th>
              <th>PID</th>
              <th>PUERTO</th>
            </tr>
          </thead>
          <tbody>{modelRows}</tbody>
        </table>
      </Section>

      {/* Groups */}
      <Section title="APLICACIONES / GRUPOS">
        <table className="table group-table">
          <thead>
            <tr>
              <th>GRUPO</th>
              <th>RAM RSS</th>
              <th>CPU</th>
              <th>PROCESOS</th>
            </tr>
          </thead>
          <tbody>{groupRows}</tbody>
        </table>
      </Section>

      {/* Memory */}
      <Section title="MEMORIA DEL SISTEMA">
        <div className="memory-summary">
          <div className="mem-row">
            <span className="mem-label">RAM total</span>
            <span className="mem-value">
              {memTotal} GiB
            </span>
          </div>
          <div className="mem-row">
            <span className="mem-label">RAM usada</span>
            <span className="mem-value">
              {memUsed} GiB
            </span>
          </div>
          <div className="mem-row">
            <span className="mem-label">RAM disponible</span>
            <span className="mem-value">
              {memAvail} GiB
            </span>
          </div>
          <div className="mem-row">
            <span className="mem-label">Swap</span>
            <span className="mem-value">
              {swapUsed} / {swapTotal} GiB
            </span>
          </div>
        </div>
      </Section>

      {/* History sparklines — always present, fills as samples accumulate */}
      <HistorySection history={history} />

      <p className="footnote">
        Nota: los modelos usan GTT/AMDGPU; Pi, Hermes y Firefox muestran RSS.
      </p>
    </div>
  );
}
