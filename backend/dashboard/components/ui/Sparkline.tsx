"use client";

export default function Sparkline({
  data,
  color = "rgb(var(--ok))",
  width = 80,
  height = 24,
  strokeWidth = 1.5,
}: {
  data: (number | null)[];
  /** Any CSS color, including token expressions like 'rgb(var(--ok))'. */
  color?: string;
  width?: number;
  height?: number;
  strokeWidth?: number;
}) {
  if (!data || data.length < 2) return null;
  const measured = data.filter((value): value is number => value !== null);
  if (measured.length === 0) return null;
  const min = Math.min(...measured);
  const max = Math.max(...measured);
  const range = max - min || 1;
  const stepX = width / (data.length - 1);
  const points = data
    .map((v, i) => {
      if (v === null) return "";
      const x = i * stepX;
      const y = height - ((v - min) / range) * height;
      return `${i === 0 || data[i - 1] === null ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-hidden
      style={{ overflow: "visible" }}
    >
      {data.map((value, index) =>
        value !== null && data[index - 1] == null && data[index + 1] == null ? (
          <circle
            key={index}
            cx={index * stepX}
            cy={height - ((value - min) / range) * height}
            r={strokeWidth}
            fill={color}
          />
        ) : null,
      )}
      <path
        d={points}
        fill="none"
        style={{ stroke: color }}
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
