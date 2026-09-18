// Chart primitives for the graphical tabs: pure SVG/CSS, no chart library.
//
// Both charts are decorative-but-honest: every colour has a legend or a label
// carrying the exact number, so the shape can never be the only source of
// truth.

import { fmtTokens } from "./Meter";

/// One slice of a donut or one bar of a column chart.
export interface Slice {
  label: string;
  value: number;
  color: string;
  caption?: string;
}

function safe(v: number): number {
  return Number.isFinite(v) ? v : 0;
}

/// Donut with the total in the middle. Segments are drawn as dash-array arcs on
/// one circle, which keeps the markup tiny and resize-friendly.
export function Donut({
  slices,
  centerValue,
  centerLabel,
  size = 190,
  thickness = 24,
}: {
  slices: Slice[];
  centerValue: string;
  centerLabel: string;
  size?: number;
  thickness?: number;
}) {
  const total = slices.reduce((acc, slice) => acc + Math.max(0, safe(slice.value)), 0);
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;

  let offset = 0;

  return (
    <div className="donut-wrap" style={{ width: size, height: size }}>
      <svg
        viewBox={`0 0 ${size} ${size}`}
        className="donut"
        role="img"
        aria-label={`${centerLabel}: ${centerValue}. ${slices
          .map((slice) => `${slice.label} ${fmtTokens(slice.value)}`)
          .join(", ")}`}
      >
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="rgba(15, 52, 96, 0.55)"
            strokeWidth={thickness}
          />
          {total > 0 &&
            slices.map((slice) => {
              const length = (Math.max(0, safe(slice.value)) / total) * circumference;
              const arc = (
                <circle
                  key={slice.label}
                  cx={size / 2}
                  cy={size / 2}
                  r={radius}
                  fill="none"
                  stroke={slice.color}
                  strokeWidth={thickness}
                  strokeDasharray={`${length} ${circumference - length}`}
                  strokeDashoffset={-offset}
                />
              );
              offset += length;
              return arc;
            })}
        </g>
      </svg>

      <div className="donut-center">
        <span className="donut-value">{centerValue}</span>
        <span className="donut-label">{centerLabel}</span>
      </div>
    </div>
  );
}

/// Vertical columns scaled against the largest value in the set.
export function Columns({
  slices,
  height = 150,
}: {
  slices: Slice[];
  height?: number;
}) {
  const max = Math.max(0, ...slices.map((slice) => safe(slice.value)));

  return (
    <div className="columns">
      {slices.map((slice) => {
        const ratio = max > 0 ? safe(slice.value) / max : 0;
        const short = slice.label.length > 13 ? `${slice.label.slice(0, 12)}…` : slice.label;
        return (
          <div className="column" key={slice.label} title={`${slice.label}: ${fmtTokens(slice.value)}`}>
            <span className="column-value">{fmtTokens(slice.value)}</span>
            <div className="column-track" style={{ height }}>
              <div
                className="column-bar"
                style={{ height: `${Math.max(ratio * 100, 2)}%`, background: slice.color }}
              />
            </div>
            <span className="column-label">{short}</span>
            {slice.caption && <span className="column-caption">{slice.caption}</span>}
          </div>
        );
      })}
    </div>
  );
}

/// Legend shared by both charts: swatch, name, exact value and share. With
/// `onSelect` each entry becomes a filter control.
export function ChartLegend({
  slices,
  onSelect,
  selected,
}: {
  slices: Slice[];
  onSelect?: (slice: Slice) => void;
  selected?: string;
}) {
  const total = slices.reduce((acc, slice) => acc + Math.max(0, safe(slice.value)), 0);

  return (
    <ul className="legend-list">
      {slices.map((slice) => {
        const content = (
          <>
            <span className="legend-swatch" style={{ background: slice.color }} aria-hidden="true" />
            <span className="legend-label">{slice.label}</span>
            <span className="legend-value">{fmtTokens(slice.value)}</span>
            <span className="legend-pct">
              {total > 0 ? Math.round((Math.max(0, safe(slice.value)) / total) * 100) : 0}%
            </span>
          </>
        );

        return (
          <li key={slice.label}>
            {onSelect ? (
              <button
                type="button"
                className={`legend-entry${selected === slice.label ? " selected" : ""}`}
                onClick={() => onSelect(slice)}
              >
                {content}
              </button>
            ) : (
              <span className="legend-entry">{content}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Treemap — area proportional to value
// ---------------------------------------------------------------------------

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface Placed {
  slice: Slice;
  rect: Rect;
}

/// Alternating split: the ordered slices are halved by cumulative value and the
/// rect is divided along one axis, flipping axis on every level. It is not a
/// squarified treemap, but it keeps the reading order (largest first) and stays
/// a handful of lines instead of a layout algorithm.
function layoutTreemap(slices: Slice[], rect: Rect, horizontal: boolean): Placed[] {
  if (slices.length === 0) return [];
  if (slices.length === 1) return [{ slice: slices[0], rect }];

  const total = slices.reduce((acc, slice) => acc + Math.max(0, safe(slice.value)), 0);
  let accumulated = 0;
  let splitAt = 1;
  for (let index = 0; index < slices.length - 1; index += 1) {
    accumulated += Math.max(0, safe(slices[index].value));
    if (accumulated >= total / 2) {
      splitAt = index + 1;
      break;
    }
  }

  const head = slices.slice(0, splitAt);
  const tail = slices.slice(splitAt);
  const headTotal = head.reduce((acc, slice) => acc + Math.max(0, safe(slice.value)), 0);
  const ratio = total > 0 ? headTotal / total : head.length / slices.length;

  if (horizontal) {
    const width = rect.w * ratio;
    return [
      ...layoutTreemap(head, { ...rect, w: width }, !horizontal),
      ...layoutTreemap(tail, { ...rect, x: rect.x + width, w: rect.w - width }, !horizontal),
    ];
  }

  const height = rect.h * ratio;
  return [
    ...layoutTreemap(head, { ...rect, h: height }, !horizontal),
    ...layoutTreemap(tail, { ...rect, y: rect.y + height, h: rect.h - height }, !horizontal),
  ];
}

/// Clicking a cell calls `onSelect`, which is what turns the chart into a
/// filter rather than a picture.
export function Treemap({
  slices,
  height = 200,
  onSelect,
  selected,
}: {
  slices: Slice[];
  height?: number;
  onSelect?: (slice: Slice) => void;
  selected?: string;
}) {
  const placed = layoutTreemap(
    slices.filter((slice) => Math.max(0, safe(slice.value)) > 0),
    { x: 0, y: 0, w: 100, h: 100 },
    true
  );

  if (placed.length === 0) {
    return <p className="model-empty">Sin datos para graficar.</p>;
  }

  return (
    <div className="treemap" style={{ height }}>
      {placed.map(({ slice, rect }) => (
        <button
          key={slice.label}
          type="button"
          className={`treemap-cell${selected === slice.label ? " selected" : ""}`}
          style={{
            left: `${rect.x}%`,
            top: `${rect.y}%`,
            width: `${rect.w}%`,
            height: `${rect.h}%`,
            background: slice.color,
          }}
          title={`${slice.label}: ${fmtTokens(slice.value)}`}
          onClick={() => onSelect?.(slice)}
        >
          <span className="treemap-label">{slice.label}</span>
          <span className="treemap-value">{fmtTokens(slice.value)}</span>
        </button>
      ))}
    </div>
  );
}
