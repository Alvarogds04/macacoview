import { useState } from "react";

import type {
  CodexTokens,
  FullSnapshot,
  LocalTokens,
  PiTokens,
  RemoteModel,
  RemoteTokens,
  TranscriptTokens,
} from "../types";
import { ChartLegend, Columns, Donut, Treemap, type Slice } from "./Charts";
import { fmtTokens } from "./Meter";
import { ErrorIndicator, LoadingState, MissingChip, Section } from "./Section";

// ---------------------------------------------------------------------------
// Palette
// ---------------------------------------------------------------------------

const SERIES = [
  "#4a90d9",
  "#3cc97a",
  "#e0a040",
  "#9a7bd9",
  "#d95080",
  "#6cc4d9",
  "#7fb3ff",
  "#b0c4de",
];

const REST_COLOR = "#5a6b7d";
const TRACK_COLOR = "#2c3a52";
const MAX_SLICES = 8;

const TOKEN_COLORS = {
  input: "#4a90d9",
  output: "#3cc97a",
  cacheRead: "#e0a040",
  cacheWrite: "#9a7bd9",
  reasoning: "#d95080",
};

/// Providers that reach a model running on this machine. Anything else counts
/// as a remote API, so a provider that is not listed here fails safe.
const LOCAL_PROVIDERS = new Set(["llamacpp", "abito-direct", "ollama", "magnitude"]);

type GroupKey = "modelo" | "proveedor" | "servicio";
type SortKey = "tokens" | "costo" | "turnos";
type OriginFilter = "todos" | "local" | "remoto";
type Origin = "local" | "remoto";

interface Consumer {
  name: string;
  provider: string;
  service: string;
  origin: Origin;
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  reasoning: number;
  total: number;
  cost: number;
  turns: number;
}

interface Group {
  label: string;
  total: number;
  cost: number;
  turns: number;
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  reasoning: number;
  models: number;
  origin: Origin | "mixto";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function safe(v: number): number {
  return Number.isFinite(v) ? v : 0;
}

function fmtMoney(v: number): string {
  return `$${safe(v).toFixed(2)}`;
}

function colorAt(index: number): string {
  return SERIES[index % SERIES.length];
}

/// Contract timestamps are ISO strings; show them short and local.
function fmtReset(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "—";
  return date.toLocaleString("es-AR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtCount(value: number): string {
  return safe(value).toLocaleString("es-AR");
}

function serviceOf(provider: string): string {
  if (provider === "openai-codex") return "Codex (suscripción)";
  if (LOCAL_PROVIDERS.has(provider)) return "Local";
  return "API remota";
}

function originOf(provider: string): Origin {
  return LOCAL_PROVIDERS.has(provider) ? "local" : "remoto";
}

/// Ranked rows become the slices that get drawn plus one aggregated remainder,
/// so a long tail never turns a donut into confetti.
function toSlices<T>(
  rows: T[],
  totalOf: (row: T) => number,
  labelOf: (row: T) => string
): { slices: Slice[]; rest: T[] } {
  const rest = rows.slice(MAX_SLICES);
  const slices: Slice[] = rows.slice(0, MAX_SLICES).map((row, index) => ({
    label: labelOf(row),
    value: totalOf(row),
    color: colorAt(index),
  }));

  if (rest.length > 0) {
    slices.push({
      label: `resto (${rest.length})`,
      value: rest.reduce((acc, row) => acc + totalOf(row), 0),
      color: REST_COLOR,
    });
  }

  return { slices, rest };
}

/// The token composition of one model. `input` excludes the cached reads, so the
/// parts are disjoint and the donut reflects the real split.
function compositionSlices(consumer: Consumer): Slice[] {
  return [
    { label: "Caché leída", value: consumer.cacheRead, color: TOKEN_COLORS.cacheRead },
    { label: "Entrada", value: consumer.input, color: TOKEN_COLORS.input },
    { label: "Salida", value: consumer.output, color: TOKEN_COLORS.output },
    { label: "Razonamiento", value: consumer.reasoning, color: TOKEN_COLORS.reasoning },
    { label: "Caché escrita", value: consumer.cacheWrite, color: TOKEN_COLORS.cacheWrite },
  ].filter((slice) => slice.value > 0);
}

// ---------------------------------------------------------------------------
// Small UI pieces
// ---------------------------------------------------------------------------

function KpiStrip({ items }: { items: { label: string; value: string }[] }) {
  return (
    <div className="kpi-strip">
      {items.map((item) => (
        <div className="kpi" key={item.label}>
          <span className="kpi-value">{item.value}</span>
          <span className="kpi-label">{item.label}</span>
        </div>
      ))}
    </div>
  );
}

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { key: T; label: string }[];
  onChange: (key: T) => void;
}) {
  return (
    <div className="segmented-wrap">
      <span className="segmented-label">{label}</span>
      <div className="segmented" role="group" aria-label={label}>
        {options.map((option) => (
          <button
            key={option.key}
            type="button"
            className={`segmented-item${option.key === value ? " active" : ""}`}
            aria-pressed={option.key === value}
            onClick={() => onChange(option.key)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Consumers — what Pi spent, filterable and drillable
// ---------------------------------------------------------------------------

function toConsumers(pi: PiTokens): Consumer[] {
  return pi.models.map((model) => ({
    name: model.name,
    provider: model.provider || "sin proveedor",
    service: serviceOf(model.provider),
    origin: originOf(model.provider),
    input: safe(model.input),
    output: safe(model.output),
    cacheRead: safe(model.cache_read),
    cacheWrite: safe(model.cache_write),
    reasoning: safe(model.reasoning),
    total: safe(model.total),
    cost: safe(model.cost_usd),
    turns: safe(model.turns),
  }));
}

function groupConsumers(consumers: Consumer[], key: GroupKey): Group[] {
  if (key === "modelo") {
    return consumers.map((consumer) => ({
      label: consumer.name,
      total: consumer.total,
      cost: consumer.cost,
      turns: consumer.turns,
      input: consumer.input,
      output: consumer.output,
      cacheRead: consumer.cacheRead,
      cacheWrite: consumer.cacheWrite,
      reasoning: consumer.reasoning,
      models: 1,
      origin: consumer.origin,
    }));
  }

  const groups = new Map<string, Group>();
  for (const consumer of consumers) {
    const label = key === "proveedor" ? consumer.provider : consumer.service;
    const group = groups.get(label) ?? {
      label,
      total: 0,
      cost: 0,
      turns: 0,
      input: 0,
      output: 0,
      cacheRead: 0,
      cacheWrite: 0,
      reasoning: 0,
      models: 0,
      origin: consumer.origin,
    };

    group.total += consumer.total;
    group.cost += consumer.cost;
    group.turns += consumer.turns;
    group.input += consumer.input;
    group.output += consumer.output;
    group.cacheRead += consumer.cacheRead;
    group.cacheWrite += consumer.cacheWrite;
    group.reasoning += consumer.reasoning;
    group.models += 1;
    if (group.origin !== consumer.origin) group.origin = "mixto";

    groups.set(label, group);
  }

  return [...groups.values()];
}

function sortGroups(groups: Group[], key: SortKey): Group[] {
  const weight = (group: Group) =>
    key === "costo" ? group.cost : key === "turnos" ? group.turns : group.total;
  return [...groups].sort((a, b) => weight(b) - weight(a));
}

function matchesFilter(consumer: Consumer, filter: { kind: GroupKey; value: string }): boolean {
  if (filter.kind === "modelo") return consumer.name === filter.value;
  if (filter.kind === "proveedor") return consumer.provider === filter.value;
  return consumer.service === filter.value;
}

function ConsumersPanel({ pi }: { pi: PiTokens }) {
  const [groupBy, setGroupBy] = useState<GroupKey>("modelo");
  const [sortBy, setSortBy] = useState<SortKey>("tokens");
  const [origin, setOrigin] = useState<OriginFilter>("todos");
  const [showUncounted, setShowUncounted] = useState(true);
  const [filter, setFilter] = useState<{ kind: GroupKey; value: string } | null>(null);

  const consumers = toConsumers(pi);
  const visible = consumers.filter(
    (consumer) =>
      (!filter || matchesFilter(consumer, filter)) &&
      (origin === "todos" || consumer.origin === origin) &&
      (showUncounted || consumer.total > 0)
  );

  // Drill-down: a provider or service filter re-groups by model, because the
  // interesting next question is which models are inside it. Filtering a single
  // model instead swaps the charts for that model's token composition.
  const chartGroup: GroupKey = filter && filter.kind !== "modelo" ? "modelo" : groupBy;
  const focused = filter?.kind === "modelo" ? visible[0] : undefined;

  const groups = sortGroups(groupConsumers(visible, chartGroup), sortBy);
  const { slices, rest } = toSlices(
    groups,
    (group) => group.total,
    (group) => group.label
  );

  const totals = visible.reduce(
    (acc, consumer) => ({
      total: acc.total + consumer.total,
      cost: acc.cost + consumer.cost,
      turns: acc.turns + consumer.turns,
      cacheRead: acc.cacheRead + consumer.cacheRead,
    }),
    { total: 0, cost: 0, turns: 0, cacheRead: 0 }
  );

  const select = (kind: GroupKey, value: string) => {
    setFilter((current) =>
      current && current.kind === kind && current.value === value ? null : { kind, value }
    );
  };

  const hasFilter = filter !== null || origin !== "todos";

  return (
    <div className="consumer-panel">
      <KpiStrip
        items={[
          { label: filter ? "tokens (filtrado)" : "tokens en Pi", value: fmtTokens(totals.total) },
          { label: "modelos", value: fmtCount(visible.length) },
          { label: "turnos", value: fmtCount(totals.turns) },
          { label: "costo", value: fmtMoney(totals.cost) },
          { label: "leídos de caché", value: fmtTokens(totals.cacheRead) },
        ]}
      />

      <div className="controls">
        <Segmented
          label="Ver por"
          value={groupBy}
          onChange={setGroupBy}
          options={[
            { key: "modelo", label: "Modelo" },
            { key: "proveedor", label: "Proveedor" },
            { key: "servicio", label: "Servicio" },
          ]}
        />
        <Segmented
          label="Ordenar"
          value={sortBy}
          onChange={setSortBy}
          options={[
            { key: "tokens", label: "Tokens" },
            { key: "costo", label: "Costo" },
            { key: "turnos", label: "Turnos" },
          ]}
        />
        <Segmented
          label="Origen"
          value={origin}
          onChange={setOrigin}
          options={[
            { key: "todos", label: "Todos" },
            { key: "local", label: "Local" },
            { key: "remoto", label: "Remoto" },
          ]}
        />
        <label className="toggle">
          <input
            type="checkbox"
            checked={showUncounted}
            onChange={(event) => setShowUncounted(event.target.checked)}
          />
          mostrar sin conteo
        </label>
      </div>

      {hasFilter && (
        <div className="filter-bar">
          <span className="segmented-label">Filtros</span>
          {filter && (
            <button type="button" className="filter-chip" onClick={() => setFilter(null)}>
              {filter.kind}: {filter.value} ✕
            </button>
          )}
          {origin !== "todos" && (
            <button type="button" className="filter-chip" onClick={() => setOrigin("todos")}>
              origen: {origin} ✕
            </button>
          )}
          <span className="filter-count">
            {fmtCount(visible.length)} de {fmtCount(consumers.length)} modelos
          </span>
        </div>
      )}

      {visible.length === 0 ? (
        <p className="model-empty">Ningún modelo coincide con los filtros.</p>
      ) : focused ? (
        <>
          <div className="chart-row">
            <Donut
              slices={compositionSlices(focused)}
              centerValue={fmtTokens(focused.total)}
              centerLabel={focused.name}
            />
            <ChartLegend slices={compositionSlices(focused)} />
          </div>
          <Columns slices={compositionSlices(focused)} height={130} />
          <div className="stat-chips">
            <span className="stat-chip">
              <b>{focused.provider}</b> {focused.service}
            </span>
            <span className="stat-chip">
              <b>{fmtCount(focused.turns)}</b> turnos
            </span>
            <span className="stat-chip">
              <b>{fmtMoney(focused.cost)}</b> costo
            </span>
            <span className="stat-chip">origen {focused.origin}</span>
          </div>
          <p className="scale-note">
            Composición del modelo: las porciones no se solapan (la entrada excluye la caché).
          </p>
        </>
      ) : (
        <>
          <div className="chart-row">
            <Donut
              slices={slices}
              centerValue={fmtTokens(totals.total)}
              centerLabel={`tokens por ${chartGroup}`}
            />
            <ChartLegend
              slices={slices}
              onSelect={(slice) => select(chartGroup, slice.label)}
              selected={filter?.value}
            />
          </div>

          <h3 className="sub-title">PARTICIPACIÓN (ÁREA PROPORCIONAL)</h3>
          <Treemap
            slices={slices}
            onSelect={(slice) => select(chartGroup, slice.label)}
            selected={filter?.value}
          />

          <h3 className="sub-title">TOKENS POR {chartGroup.toUpperCase()}</h3>
          <Columns slices={slices} />

          <h3 className="sub-title">DETALLE</h3>
          <ul className="stat-list">
            {groups.map((group, index) => (
              <li key={group.label}>
                <button
                  type="button"
                  className="row-button"
                  onClick={() => select(chartGroup, group.label)}
                >
                  <span
                    className="legend-swatch"
                    style={{ background: index < MAX_SLICES ? colorAt(index) : REST_COLOR }}
                    aria-hidden="true"
                  />
                  <span className="legend-label">
                    {group.label}
                    {group.models > 1 ? ` · ${group.models} modelos` : ""}
                    {group.origin === "mixto" ? "" : ` · ${group.origin}`}
                  </span>
                  <span className="stat-line">
                    {fmtTokens(group.total)} · {fmtCount(group.turns)} turnos
                    {group.cost > 0 ? ` · ${fmtMoney(group.cost)}` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      <p className="scale-note">
        Caché es el contexto que se vuelve a leer en cada turno, no contenido nuevo: se informa
        aparte para no inflar la entrada. Los modelos sin conteo de tokens (locales sobre todo)
        aportan turnos, no tokens.
        {rest.length > 0 &&
          ` Resto: ${rest
            .map((group) => `${group.label} ${fmtTokens(group.total)}`)
            .join(" · ")}.`}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CLI agents — Codex CLI and Claude Code transcripts (same payload as pi)
// ---------------------------------------------------------------------------

interface ProviderRollup {
  label: string;
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  reasoning: number;
  total: number;
  cost: number;
  turns: number;
  models: number;
}

/// The transcripts record the provider per model; an empty provider (the CLI
/// agents write "") still has to be legible in the per-provider list.
function providerLabel(provider: string): string {
  const trimmed = provider.trim();
  return trimmed === "" ? "sin proveedor" : trimmed;
}

function providerRollups(tokens: TranscriptTokens): ProviderRollup[] {
  const rollups = new Map<string, ProviderRollup>();
  for (const model of tokens.models) {
    const label = providerLabel(model.provider);
    const rollup = rollups.get(label) ?? {
      label,
      input: 0,
      output: 0,
      cacheRead: 0,
      cacheWrite: 0,
      reasoning: 0,
      total: 0,
      cost: 0,
      turns: 0,
      models: 0,
    };

    rollup.input += safe(model.input);
    rollup.output += safe(model.output);
    rollup.cacheRead += safe(model.cache_read);
    rollup.cacheWrite += safe(model.cache_write);
    rollup.reasoning += safe(model.reasoning);
    rollup.total += safe(model.total);
    rollup.cost += safe(model.cost_usd);
    rollup.turns += safe(model.turns);
    rollup.models += 1;
    rollups.set(label, rollup);
  }

  return [...rollups.values()].sort((a, b) => b.total - a.total);
}

/// KPI strip + per-provider list for one CLI agent. `reasoning` and
/// `cache_write` are the metrics these sources carry that the rest of the tab
/// does not surface yet, so they get their own KPIs here.
function TranscriptPanel({ tokens }: { tokens: TranscriptTokens }) {
  if (tokens.models.length === 0) {
    return <p className="model-empty">Sin turnos registrados todavía.</p>;
  }

  const rollups = providerRollups(tokens);

  return (
    <div className="server-panel">
      <KpiStrip
        items={[
          { label: "tokens", value: fmtTokens(safe(tokens.total)) },
          { label: "turnos", value: fmtCount(tokens.turns) },
          { label: "sesiones", value: fmtCount(tokens.sessions) },
          { label: "razonamiento", value: fmtTokens(safe(tokens.reasoning)) },
          { label: "caché escrita", value: fmtTokens(safe(tokens.cache_write)) },
          { label: "costo", value: fmtMoney(tokens.cost_usd) },
        ]}
      />

      <h3 className="sub-title">POR PROVEEDOR</h3>
      <ul className="stat-list">
        {rollups.map((rollup) => (
          <li key={rollup.label}>
            <span className="legend-label">
              {rollup.label}
              {rollup.models > 1 ? ` · ${rollup.models} modelos` : ""}
            </span>
            <span className="stat-line">
              ENT {fmtTokens(rollup.input)} · SAL {fmtTokens(rollup.output)} · CACHÉ LEÍDA{" "}
              {fmtTokens(rollup.cacheRead)} · CACHÉ ESCRITA {fmtTokens(rollup.cacheWrite)} · RAZ{" "}
              {fmtTokens(rollup.reasoning)}
              {rollup.cost > 0 ? ` · ${fmtMoney(rollup.cost)}` : ""}
            </span>
          </li>
        ))}
      </ul>

      <p className="scale-note">
        Tokens leídos de las transcripciones locales del agente. La caché escrita es contexto
        nuevo que se guarda en caché; el razonamiento son los tokens de pensamiento del modelo.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Servers — what each service counted on its own side
// ---------------------------------------------------------------------------

function RemoteDetail({ models }: { models: RemoteModel[] }) {
  return (
    <ul className="stat-list">
      {models.map((model, index) => (
        <li key={model.name}>
          <span
            className="legend-swatch"
            style={{ background: index < MAX_SLICES ? colorAt(index) : REST_COLOR }}
            aria-hidden="true"
          />
          <span className="legend-label">{model.name}</span>
          <span className="stat-line">
            ENT {fmtTokens(safe(model.input))} · SAL {fmtTokens(safe(model.output))} · CACHÉ{" "}
            {fmtTokens(safe(model.input_cached))} · RAZ {fmtTokens(safe(model.reasoning))}
            {safe(model.spend_usd) > 0 ? ` · ${fmtMoney(model.spend_usd)}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ServersPanel({
  remote,
  local,
}: {
  remote: RemoteTokens;
  local: LocalTokens;
}) {
  const remoteSlices = toSlices(
    remote.models,
    (model) => safe(model.total),
    (model) => model.name
  ).slices;
  const localSlices = toSlices(
    local.models,
    (model) => safe(model.total),
    (model) => model.name
  ).slices;

  return (
    <div className="server-panel">
      <h3 className="sub-title">LITELLM · REMOTO</h3>
      {remote.status !== "ok" ? (
        <MissingChip label="litellm" />
      ) : remote.models.length === 0 ? (
        <p className="model-empty">Sin consumo desde que arrancó el proxy.</p>
      ) : (
        <>
          <div className="chart-row">
            <Treemap slices={remoteSlices} height={150} />
            <ChartLegend slices={remoteSlices} />
          </div>
          <RemoteDetail models={remote.models} />
        </>
      )}

      <h3 className="sub-title">LLAMA.CPP · LOCAL</h3>
      {local.status !== "ok" ? (
        <MissingChip label="llama.cpp" />
      ) : local.models.length === 0 ? (
        <p className="model-empty">Ningún servidor local expone --metrics.</p>
      ) : (
        <>
          <Columns slices={localSlices} height={110} />
          <ul className="stat-list">
            {local.models.map((model, index) => (
              <li key={`${model.port}-${model.name}`}>
                <span
                  className="legend-swatch"
                  style={{ background: index < MAX_SLICES ? colorAt(index) : REST_COLOR }}
                  aria-hidden="true"
                />
                <span className="legend-label">
                  {model.name} :{model.port}
                </span>
                <span className="stat-line">
                  ENT {fmtTokens(safe(model.input))} · SAL {fmtTokens(safe(model.output))} ·
                  CACHÉ {fmtTokens(safe(model.input_cached))}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      <p className="scale-note">
        Contadores acumulados desde que arrancó cada servicio: se reinician con él.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Codex — subscription windows (percentages, not tokens)
// ---------------------------------------------------------------------------

function CodexPanel({ codex }: { codex: CodexTokens }) {
  if (codex.windows.length === 0) {
    return <p className="model-empty">Sin ventanas informadas por codexbar.</p>;
  }

  // Reaching the top of a window is the risky state: colour it by pressure.
  const pressureColor = (used: number) => {
    if (used >= 90) return "#d95080";
    if (used >= 70) return "#e0a040";
    return "#3cc97a";
  };

  return (
    <div className="codex-panel">
      <div className="chart-row">
        {codex.windows.map((window) => {
          const used = safe(window.used_percent);
          return (
            <div className="codex-window" key={window.label}>
              <Donut
                size={150}
                thickness={18}
                slices={[
                  { label: "consumido", value: used, color: pressureColor(used) },
                  { label: "disponible", value: Math.max(0, 100 - used), color: TRACK_COLOR },
                ]}
                centerValue={`${used.toFixed(0)}%`}
                centerLabel={window.label}
              />
              <span className="codex-reset">resetea {fmtReset(window.resets_at)}</span>
            </div>
          );
        })}
      </div>

      <div className="stat-chips">
        {codex.plan && (
          <span className="stat-chip">
            plan <b>{codex.plan}</b>
          </span>
        )}
        <span className="stat-chip">
          <b>{codex.windows.length}</b> ventana{codex.windows.length === 1 ? "" : "s"}
        </span>
      </div>

      <p className="scale-note">
        La suscripción se mide como porcentaje de la ventana, no en tokens.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Tokens component
// ---------------------------------------------------------------------------

export function Tokens({
  snapshot,
  error,
}: {
  snapshot: FullSnapshot | null;
  error: boolean;
}) {
  const tokens = snapshot?.tokens ?? null;

  if (!tokens) {
    return (
      <>
        {error && <ErrorIndicator message="Error de conexión con el backend." />}
        {snapshot ? (
          <div className="loading-state">
            <span className="spinner" />
            <span>Recolectando consumo de tokens…</span>
          </div>
        ) : (
          <LoadingState />
        )}
      </>
    );
  }

  const { remote, local, pi, codex_cli, claude_code, codex } = tokens;

  return (
    <div className="tokens-content">
      {error && (
        <ErrorIndicator message="Error de conexión con el backend (mostrando los últimos datos válidos)." />
      )}

      <Section title="CONSUMIDORES · LO QUE SALE DE PI">
        {pi.status !== "ok" ? (
          <MissingChip label="sesiones de Pi" />
        ) : (
          <ConsumersPanel pi={pi} />
        )}
      </Section>

      <Section title="SERVIDORES · LO QUE ENTRA A CADA SERVICIO">
        <ServersPanel remote={remote} local={local} />
      </Section>

      <Section title="AGENTE · CODEX CLI">
        {codex_cli.status !== "ok" ? (
          <MissingChip label="codex_cli" />
        ) : (
          <TranscriptPanel tokens={codex_cli} />
        )}
      </Section>

      <Section title="AGENTE · CLAUDE CODE">
        {claude_code.status !== "ok" ? (
          <MissingChip label="claude_code" />
        ) : (
          <TranscriptPanel tokens={claude_code} />
        )}
      </Section>

      <Section title="SUSCRIPCIÓN CODEX">
        {codex.status !== "ok" ? (
          <MissingChip label="codexbar" />
        ) : (
          <CodexPanel codex={codex} />
        )}
      </Section>

      <p className="scale-note">
        Pi cuenta lo que sale del cliente; litellm y llama.cpp cuentan lo que entra a cada
        servidor; codex_cli y claude_code agregan las transcripciones locales de cada agente. Los
        alcances no son excluyentes (un mismo pedido puede aparecer en más de una sección), por
        eso no se suman en un total único.
      </p>
    </div>
  );
}
