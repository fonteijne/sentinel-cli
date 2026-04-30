import { Icon, type IconName } from "../icons";
import { Sparkline } from "./Sparkline";

interface Props {
  icon: IconName;
  tone: "primary" | "success" | "warning" | "info";
  label: string;
  value: string | number;
  trend?: number; // optional percent change
  data?: number[];
}

const TONE = {
  primary: { bg: "var(--primary-soft)", color: "var(--primary)" },
  success: { bg: "var(--success-soft)", color: "var(--success)" },
  warning: { bg: "var(--warning-soft)", color: "var(--warning)" },
  info: { bg: "var(--info-soft)", color: "var(--info)" },
} as const;

export function KPI({ icon, tone, label, value, trend, data }: Props) {
  const t = TONE[tone];
  const trendColor =
    trend === undefined
      ? "var(--text-subtle)"
      : trend > 0
        ? "var(--success)"
        : trend < 0
          ? "var(--danger)"
          : "var(--text-subtle)";
  return (
    <div className="kpi">
      <div className="kpi-head">
        <div className="kpi-icon" style={{ background: t.bg, color: t.color }}>
          <Icon name={icon} size={20} />
        </div>
        {trend !== undefined && (
          <span className="kpi-trend" style={{ color: trendColor }}>
            <Icon
              name={trend >= 0 ? "arrowUp" : "arrowDown"}
              size={12}
              stroke={2.5}
            />
            {Math.abs(trend)}%
          </span>
        )}
      </div>
      <div>
        <div className="kpi-value">{value}</div>
        <div className="kpi-label">{label}</div>
      </div>
      <Sparkline values={data ?? [0, 0]} color={t.color} />
    </div>
  );
}
