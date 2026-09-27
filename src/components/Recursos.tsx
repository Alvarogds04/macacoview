import type { FullSnapshot, Group, HistorySample, Memory, Model } from "../types";
import { GROUP_KEYS, GROUP_LABELS } from "../types";
import { Bar, Meter, pctOf } from "./Meter";
import { ErrorIndicator, LoadingState, Section } from "./Section";

// ---------------------------------------------------------------------------
// Palette — shared by bars, stacked segments and sparklines
// ---------------------------------------------------------------------------

const SERIES_COLORS: Record<string, string> = {
  ram: "#4a90d9",
  swap: "#7b6ee6",
  gtt: "#4a90d9",
  rss: "#9a7bd9",
  cpu: "#3cc97a",
  used: "#4a90d9",
  avail: "#3cc97a",
  pi: "#e0a040",
  hermes: "#d95080",
  firefox: "#7b6ee6",
  system: "#6cc4d9",
  other: "#9a9a9a",
};

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
// Memory — bars scaled against the real total, so the fill is a true ratio
// ---------------------------------------------------------------------------

function MemoryPanel({ memory }: { memory: Memory }) {
  const total = safe(memory.total_gib);
  const used = safe(memory.used_gib);
  const avail = safe(memory.available_gib);
  const swapTotal = safe(memory.swap_total_gib);
  const swapUsed = safe(memory.swap_used_gib);

  const rows = [
    {
      key: "ram",
      label: "RAM",
      value: used,
      max: total,
      unit: "GiB",
      color: SERIES_COLORS.ram,
    },
    {
      key: "swap",
      label: "SWAP",
      value: swapUsed,
      max: swapTotal,
      unit: "GiB",
      color: SERIES_COLORS.swap,
    },
  ];

  return (
    <div className="mem-bars">
      {rows.map((row) => (
        <div className="mem-bar-row" key={row.key}>
          <span className="mem-bar-label">{row.label}</span>
          <Bar share={pctOf(row.value, row.max)} color={row.color} />
          <span className="mem-bar-value">
            {fmtOneDecimal(row.value)}
            <small>
              {" / "}
              {fmtOneDecimal(row.max)} {row.unit}
            </small>
          </span>
          <span className="mem-bar-pct">
            {Math.round(pctOf(row.value, row.max))}%
          </span>
        </div>
      ))}
      <p className="mem-foot">
        disponible {fmtOneDecimal(avail)} GiB de {fmtOneDecimal(total)} GiB
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Groups — one stacked bar of monitored RSS plus a legend with exact values
// ---------------------------------------------------------------------------

function GroupPanel({ groups }: { groups: Record<string, Group> }) {
  // "Not measured" (the key is absent from the map, or the map is empty — the
  // macOS collector reports no groups at all) is NOT the same as "measured
  // zero": on Linux a 0 is a real measurement and keeps rendering as 0.0G.
  // An absent key gets "—", never a fabricated zero.
  const measured = GROUP_KEYS.map((key) => groups[key] !== undefined);

  if (!measured.some(Boolean)) {
    return (
      <div className="group-panel">
        <p className="model-empty">Sin grupos medidos en esta máquina.</p>
      </div>
    );
  }

  // A key that IS present contributes a real measurement, zero included.
  const values = GROUP_KEYS.map((key) => safe(groups[key]?.rss_gib ?? 0));
  const total = values.reduce((acc, v) => acc + v, 0);

  return (
    <div className="group-panel">
      <div
        className="group-stack"
        role="img"
        aria-label={`RSS por grupo, total ${fmtTwoDecimals(total)} GiB`}
      >
        {GROUP_KEYS.map((key, i) => {
          const share = pctOf(values[i], total);
          if (share <= 0) return null;
          return (
            <div
              key={key}
              className="group-seg"
              style={{ width: `${share}%`, background: SERIES_COLORS[key] }}
              title={`${GROUP_LABELS[key]}: ${fmtTwoDecimals(values[i])}G`}
            />
          );
        })}
      </div>

      <ul className="legend">
        {GROUP_KEYS.map((key, i) => (
          <li className="legend-item" key={key}>
            <span
              className="legend-swatch"
              style={{ background: SERIES_COLORS[key] }}
              aria-hidden="true"
            />
            <span className="legend-label">{GROUP_LABELS[key]}</span>
            <span className="legend-value">
              {measured[i] ? `${fmtTwoDecimals(values[i])}G` : "—"}
            </span>
            <span className="legend-pct">
              {measured[i] ? `${Math.round(pctOf(values[i], total))}%` : "—"}
            </span>
            <span className="legend-procs">
              {measured[i] ? `${groups[key]?.pids.length ?? 0} proc` : "—"}
            </span>
          </li>
        ))}
      </ul>

      {measured.some((isMeasured) => !isMeasured) && (
        <p className="scale-note">
          «—» = grupo no medido en esta máquina; un 0.0G sí es una medición real.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Models — one card per loaded model, with GTT/RSS/CPU meters
// ---------------------------------------------------------------------------

/// GTT meter for one model. On macOS the value is `null` ("not measurable"),
/// which is rendered as "—" with NO bar at all — never as `0.0G`, which would
/// falsely claim the model uses no GPU memory. A `Some(0.0)` from Linux is a
/// real measurement and keeps the normal `0.0G` + empty-bar rendering.

function ModelCard({
  model,
  gttMax,
  rssMax,
}: {
  model: Model;
  gttMax: number;
  rssMax: number;
}) {
  const gtt = model.gtt_gib;
  const rss = safe(model.rss_gib);
  const cpu = safe(model.cpu);
  const icon = modelIcon(model.alias);
  const port = model.port === null ? "sin puerto" : `:${model.port}`;

  return (
    <article className="model-card">
      <header className="model-card-head">
        <span className="model-alias">
          {icon} {model.alias}
        </span>
        <span className="model-port">{port}</span>
      </header>

      <p className="model-file" title={model.model}>
        {model.model}
      </p>

      <div className="model-meters">
        {gtt === null ? (
          // "No measurable" (macOS): "—" and no bar, not a zero bar. The
          // empty middle cell keeps the 3-column meter grid aligned with the
          // measurable rows (label / bar / value) without drawing a track.
          <div className="meter" role="img" aria-label="GTT: no medible">
            <span className="meter-label">GTT</span>
            <span aria-hidden="true" />
            <span className="meter-value">—</span>
          </div>
        ) : (
          // Real measurement from the Linux collector: 0.0G is legitimate.
          <Meter
            label="GTT"
            display={`${fmtOneDecimal(safe(gtt))}G`}
            share={pctOf(safe(gtt), gttMax)}
            color={SERIES_COLORS.gtt}
          />
        )}
        <Meter
          label="RSS"
          display={`${fmtOneDecimal(rss)}G`}
          share={pctOf(rss, rssMax)}
          color={SERIES_COLORS.rss}
        />
        <Meter
          label="CPU"
          display={`${fmtOneDecimal(cpu)}%`}
          share={cpu}
          color={SERIES_COLORS.cpu}
        />
      </div>

      <footer className="model-foot">PID {model.pid}</footer>
    </article>
  );
}

// ---------------------------------------------------------------------------
// Sparkline — inline SVG area chart
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
      <polygon points={`0,28 ${points} 200,28`} fill={color} opacity="0.18" />
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

const MAX_SAMPLES = 300;

function HistorySection({ history }: { history: HistorySample[] }) {
  const sampleCount = history.length;

  if (sampleCount === 0) {
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

  const rows: Array<{ key: string; label: string; value: string; series: number[] }> =
    [
      {
        key: "used",
        label: "RAM usada",
        value: fmtOneDecimal(latest.used_gib),
        series: history.map((s) => s.used_gib),
      },
      {
        key: "avail",
        label: "RAM disponible",
        value: fmtOneDecimal(latest.available_gib),
        series: history.map((s) => s.available_gib),
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
            color={SERIES_COLORS[row.key] ?? SERIES_COLORS.other}
            value={row.value}
            series={row.series}
          />
        ))}
      </div>
    </Section>
  );
}

// ---------------------------------------------------------------------------
// Main Recursos component
// ---------------------------------------------------------------------------

export function Recursos({
  snapshot,
  error,
  history,
}: {
  snapshot: FullSnapshot | null;
  error: boolean;
  history: HistorySample[];
}) {
  if (!snapshot) {
    return (
      <>
        {error && <ErrorIndicator message="Error de conexión con el backend." />}
        <LoadingState />
      </>
    );
  }

  const { memory, models, groups } = snapshot;

  // Shared scales so meters are comparable inside their own metric.
  // "Not measurable" models (gtt_gib === null) are excluded from the scale:
  // they contribute no measurement, so the max is taken over the measured
  // values only, exactly as if those models were absent. `?? 0` makes that
  // explicit — a bare Math.max over the raw array would coerce null to 0
  // (same numeric result today, but silent and fragile).
  const gttMax = Math.max(0, ...models.map((m) => m.gtt_gib ?? 0));
  const rssMax = Math.max(0, ...models.map((m) => safe(m.rss_gib)));

  return (
    <div className="recursos-content">
      {/* A failed poll keeps the last good snapshot on screen: the banner
          warns without throwing the data away. */}
      {error && (
        <ErrorIndicator message="Error de conexión con el backend (mostrando los últimos datos válidos)." />
      )}

      {/* Memory */}
      <Section title="MEMORIA DEL SISTEMA">
        <MemoryPanel memory={memory} />
      </Section>

      {/* Models */}
      <Section title="MODELOS LOCALES">
        {models.length === 0 ? (
          <p className="model-empty">Sin modelos locales cargados.</p>
        ) : (
          <>
            <p className="scale-note">
              Las barras comparan cada modelo contra el mayor del conjunto; el valor
              exacto está a la derecha.
            </p>
            <div className="model-grid">
              {models.map((m) => (
                <ModelCard key={m.pid} model={m} gttMax={gttMax} rssMax={rssMax} />
              ))}
            </div>
          </>
        )}
      </Section>

      {/* Groups */}
      <Section title="APLICACIONES / GRUPOS">
        <GroupPanel groups={groups} />
      </Section>

      {/* History sparklines — always present, fills as samples accumulate */}
      <HistorySection history={history} />

      <p className="footnote">
        Nota: los modelos usan GTT/AMDGPU; Pi, Hermes y Firefox muestran RSS. RAM y SWAP
        usan su total real como referencia.
      </p>
    </div>
  );
}
