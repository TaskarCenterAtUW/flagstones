const int = new Intl.NumberFormat("en-US");
export const fmtInt = (n) => (n == null ? "" : int.format(Math.round(n)));
export const fmtM2 = (a) => (a == null ? "" : `${int.format(Math.round(a))} m²`);
export const fmtPct = (x) => (x == null ? "" : `${(x * 100).toFixed(1)}%`);
export const fmtM = (x) => (x == null ? "" : `${x.toFixed(2)} m`);
