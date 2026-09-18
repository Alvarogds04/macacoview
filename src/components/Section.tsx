// Shared section shell and the two states every tab needs.

export function Section({
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

export function LoadingState() {
  return (
    <div className="loading-state">
      <span className="spinner" />
      <span>Esperando datos…</span>
    </div>
  );
}

export function ErrorIndicator({ message }: { message: string }) {
  return <div className="error-banner">⚠️ {message}</div>;
}

/// Small chip for a source that returned no data. The tab keeps rendering the
/// sources that did work instead of blanking the whole view.
export function MissingChip({ label }: { label: string }) {
  return <span className="missing-chip">{label}: sin datos</span>;
}
