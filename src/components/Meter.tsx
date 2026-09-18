// Shared meter primitives for the graphical tabs. Widths are driven by an
// already-normalized share so the caller owns the scale, and the exact value
// always stays visible next to the bar: the bar is the glance, the number is
// the truth.

export function Bar({ share, color }: { share: number; color: string }) {
  return (
    <div className="bar" aria-hidden="true">
      <div
        className="bar-fill"
        style={{ width: `${Math.min(100, Math.max(0, share))}%`, background: color }}
      />
    </div>
  );
}

export function Meter({
  label,
  display,
  share,
  color,
}: {
  label: string;
  display: string;
  share: number;
  color: string;
}) {
  return (
    <div className="meter">
      <span className="meter-label">{label}</span>
      <Bar share={share} color={color} />
      <span className="meter-value">{display}</span>
    </div>
  );
}

/// Share of `value` over `max`, clamped to 0..100 for a bar width.
export function pctOf(value: number, max: number): number {
  if (!(max > 0)) return 0;
  const safe = Number.isFinite(value) ? value : 0;
  return Math.min(100, Math.max(0, (safe / max) * 100));
}

/// 1234567 -> "1.2M". Token totals are read at a glance, so millions and
/// billions are the useful units.
export function fmtTokens(value: number): string {
  const safe = Number.isFinite(value) ? value : 0;
  if (safe >= 1e9) return `${(safe / 1e9).toFixed(2)}B`;
  if (safe >= 1e6) return `${(safe / 1e6).toFixed(1)}M`;
  if (safe >= 1e3) return `${(safe / 1e3).toFixed(1)}k`;
  return String(Math.round(safe));
}
