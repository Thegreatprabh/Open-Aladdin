// Number / currency / time formatting (en-IN, tabular numerals).

export function fmt(n: number, d = 2): string {
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
}

export function fmt0(n: number): string {
  if (!Number.isFinite(n)) return '—';
  return Math.round(n).toLocaleString('en-IN');
}

export function fmtSigned(n: number, d = 2): string {
  if (!Number.isFinite(n)) return '—';
  return `${n >= 0 ? '+' : '−'}${fmt(Math.abs(n), d)}`;
}

export function fmtINR(n: number, d = 0): string {
  if (!Number.isFinite(n)) return '—';
  return `${n < 0 ? '−₹' : '₹'}${fmt(Math.abs(n), d)}`;
}

/** n is already denominated in ₹ crore. */
export function fmtCr(n: number, d = 2): string {
  return `${n < 0 ? '−' : ''}₹${fmt(Math.abs(n), d)} Cr`;
}

export function fmtPct(n: number, d = 2): string {
  return `${n >= 0 ? '+' : '−'}${fmt(Math.abs(n), d)}%`;
}

export function fmtTime(ts: number): string {
  const d = new Date(ts);
  const p = (x: number) => String(x).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
