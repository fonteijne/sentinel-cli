interface Props {
  values: number[];
  color?: string;
  height?: number;
}

export function Sparkline({ values, color = "var(--primary)", height = 40 }: Props) {
  const w = 120;
  const h = height;
  if (values.length < 2) {
    return (
      <div
        style={{
          height: h,
          background: "var(--surface-3)",
          borderRadius: "var(--radius-sm)",
        }}
      />
    );
  }
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;
  const step = w / (values.length - 1);
  const pts = values.map(
    (v, i) => `${i * step},${h - ((v - min) / range) * (h - 6) - 3}`
  );
  const path = "M " + pts.join(" L ");
  const area = path + ` L ${w},${h} L 0,${h} Z`;
  const id = `spark-${color.replace(/[^a-z0-9]/gi, "")}`;
  return (
    <svg
      width="100%"
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      style={{ display: "block" }}
    >
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.25" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path
        d={path}
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
