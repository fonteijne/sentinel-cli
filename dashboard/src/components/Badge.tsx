import type { ReactNode } from "react";

type Tone = "default" | "primary" | "success" | "warning" | "danger" | "info" | "solid";

interface Props {
  tone?: Tone;
  dot?: boolean;
  children: ReactNode;
  style?: React.CSSProperties;
}

export function Badge({ tone = "default", dot, children, style }: Props) {
  const cls = ["badge"];
  if (tone !== "default") cls.push(`badge-${tone}`);
  if (dot) cls.push("dot");
  return (
    <span className={cls.join(" ")} style={style}>
      {children}
    </span>
  );
}
