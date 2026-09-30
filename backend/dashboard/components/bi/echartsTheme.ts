"use client";

/**
 * The bridge between the app's CSS design tokens and an ECharts canvas.
 *
 * Recharts renders SVG, so `lib/chartTheme.ts` can hand it the literal string
 * `var(--chart-1)` and the browser resolves it. A canvas cannot: ECharts paints
 * with `CanvasRenderingContext2D`, which has no CSS cascade and no `var()`. So
 * every colour a BI chart uses has to be RESOLVED first, by asking the document
 * for the computed value of each token — and re-resolved whenever the active
 * theme changes, because the tokens themselves change under `[data-theme]`.
 *
 * The resolved palette is registered with ECharts under a name that encodes the
 * values it was built from. `EChartCanvas` keys its instance on that name, so a
 * theme switch produces a new name, a fresh `init`, and a repaint in the new
 * palette. (ECharts bakes a theme at `init`; re-registering a name already in
 * use changes nothing for a live instance, which is exactly the bug the name
 * digest prevents.)
 *
 * The fallbacks below are reached only when `app/globals.css` has not been
 * applied at all. They are colours, and nothing here decides anything about a
 * figure.
 */

import { useEffect, useState } from "react";

export type BiChartTokens = Readonly<{
  /** Categorical series palette, in order. */
  series: readonly string[];
  grid: string;
  axis: string;
  text: string;
  heading: string;
  muted: string;
  surface: string;
  border: string;
  favourable: string;
  caution: string;
  adverse: string;
  accent: string;
}>;

const CHANNEL_TOKENS = {
  text: ["--text", "198 208 222"],
  heading: ["--heading", "237 242 249"],
  muted: ["--text-muted", "132 148 169"],
  surface: ["--surface-raised", "23 32 47"],
  border: ["--line-strong", "46 62 88"],
  axis: ["--line-strong", "46 62 88"],
  favourable: ["--ok", "53 194 141"],
  caution: ["--warn", "245 166 35"],
  adverse: ["--crit", "242 109 109"],
  accent: ["--accent", "77 159 255"],
} as const;

const SERIES_FALLBACK: readonly string[] = [
  "#5B9BFF",
  "#A78BFA",
  "#34D399",
  "#FBBF24",
  "#FB7185",
  "#2DD4BF",
];

const GRID_FALLBACK = "rgba(150, 170, 200, 0.14)";

function readRaw(
  style: CSSStyleDeclaration | null,
  name: string,
  fallback: string,
): string {
  const value = style?.getPropertyValue(name).trim();
  return value ? value : fallback;
}

function readChannels(
  style: CSSStyleDeclaration | null,
  name: string,
  fallback: string,
): string {
  return `rgb(${readRaw(style, name, fallback)})`;
}

/**
 * Resolve every token a BI chart paints with, from the live document.
 *
 * Reading from `document.documentElement` rather than from the chart's own
 * element is deliberate: the tokens are declared on `:root` and on
 * `[data-theme]`, and a chart inside a card must not inherit a card-local
 * override that the rest of the app does not have.
 */
export function readChartTokens(): BiChartTokens {
  const style =
    typeof window === "undefined" || typeof document === "undefined"
      ? null
      : window.getComputedStyle(document.documentElement);

  const series = SERIES_FALLBACK.map((fallback, index) =>
    readRaw(style, `--chart-${index + 1}`, fallback),
  );

  return {
    series,
    grid: readRaw(style, "--chart-grid", GRID_FALLBACK),
    axis: readChannels(style, ...CHANNEL_TOKENS.axis),
    text: readChannels(style, ...CHANNEL_TOKENS.text),
    heading: readChannels(style, ...CHANNEL_TOKENS.heading),
    muted: readChannels(style, ...CHANNEL_TOKENS.muted),
    surface: readChannels(style, ...CHANNEL_TOKENS.surface),
    border: readChannels(style, ...CHANNEL_TOKENS.border),
    favourable: readChannels(style, ...CHANNEL_TOKENS.favourable),
    caution: readChannels(style, ...CHANNEL_TOKENS.caution),
    adverse: readChannels(style, ...CHANNEL_TOKENS.adverse),
    accent: readChannels(style, ...CHANNEL_TOKENS.accent),
  };
}

/** A short, order-stable digest of the resolved palette, for the theme name. */
export function chartTokenDigest(tokens: BiChartTokens): string {
  const material = [
    ...tokens.series,
    tokens.grid,
    tokens.axis,
    tokens.text,
    tokens.heading,
    tokens.muted,
    tokens.surface,
    tokens.border,
    tokens.favourable,
    tokens.caution,
    tokens.adverse,
    tokens.accent,
  ].join("|");
  let hash = 2166136261;
  for (let index = 0; index < material.length; index += 1) {
    hash ^= material.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

/** The ECharts theme object, built entirely from resolved token values. */
export function buildEchartsTheme(
  tokens: BiChartTokens,
): Record<string, unknown> {
  const axisCommon = {
    axisLine: { show: true, lineStyle: { color: tokens.axis } },
    axisTick: { show: false },
    axisLabel: { color: tokens.muted, fontSize: 11 },
    splitLine: { show: true, lineStyle: { color: tokens.grid } },
  };

  return {
    color: [...tokens.series],
    backgroundColor: "transparent",
    textStyle: {
      color: tokens.text,
      fontSize: 12,
      fontFamily:
        'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
    },
    title: {
      textStyle: { color: tokens.heading, fontSize: 13, fontWeight: 600 },
      subtextStyle: { color: tokens.muted, fontSize: 11 },
    },
    grid: {
      left: 8,
      right: 12,
      top: 16,
      bottom: 8,
      containLabel: true,
      borderColor: tokens.border,
    },
    legend: {
      textStyle: { color: tokens.muted, fontSize: 11 },
      itemWidth: 10,
      itemHeight: 10,
      icon: "roundRect",
    },
    tooltip: {
      backgroundColor: tokens.surface,
      borderColor: tokens.border,
      borderWidth: 1,
      textStyle: { color: tokens.text, fontSize: 11 },
      axisPointer: {
        lineStyle: { color: tokens.axis },
        crossStyle: { color: tokens.axis },
      },
    },
    categoryAxis: { ...axisCommon, splitLine: { show: false } },
    valueAxis: axisCommon,
    timeAxis: axisCommon,
    logAxis: axisCommon,
    line: {
      symbol: "circle",
      symbolSize: 5,
      smooth: false,
      lineStyle: { width: 2 },
    },
    bar: { itemStyle: { borderRadius: [2, 2, 0, 0] } },
    pie: {
      itemStyle: { borderColor: tokens.surface, borderWidth: 1 },
      label: { color: tokens.muted, fontSize: 11 },
    },
  };
}

export type BiChartTheme = Readonly<{
  /** The name the palette is registered under; changes when the palette does. */
  name: string;
  tokens: BiChartTokens;
  theme: Record<string, unknown>;
}>;

export function chartThemeFrom(tokens: BiChartTokens): BiChartTheme {
  return {
    name: `aeq-bi-${chartTokenDigest(tokens)}`,
    tokens,
    theme: buildEchartsTheme(tokens),
  };
}

/**
 * The categorical series colour at `index`, cycling once the palette runs out.
 *
 * The RESOLVED value, not `var(--chart-N)`: a canvas cannot read a CSS variable,
 * which is the whole reason `readChartTokens` exists.
 */
export function seriesColor(tokens: BiChartTokens, index: number): string {
  const size = tokens.series.length;
  return tokens.series[((index % size) + size) % size];
}

/**
 * The active chart palette, rebuilt whenever the application theme changes.
 *
 * It watches `data-theme` on the document element — which is what both the
 * pre-paint script in `app/layout.tsx` and `ThemeProvider` write — rather than
 * subscribing to the provider, so a chart rendered outside the provider (or
 * before it has mounted) still repaints correctly. The OS preference is watched
 * too, for the "system" setting.
 */
export function useChartTheme(): BiChartTheme {
  const [chartTheme, setChartTheme] = useState<BiChartTheme>(() =>
    chartThemeFrom(readChartTokens()),
  );

  useEffect(() => {
    const refresh = () => {
      setChartTheme((current) => {
        const next = chartThemeFrom(readChartTokens());
        return next.name === current.name ? current : next;
      });
    };
    // The first resolve after mount: on the server there is no document, so the
    // initial state above was built from the fallbacks.
    refresh();

    const observer = new MutationObserver(refresh);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "class", "style"],
    });
    const media = window.matchMedia("(prefers-color-scheme: light)");
    media.addEventListener("change", refresh);
    return () => {
      observer.disconnect();
      media.removeEventListener("change", refresh);
    };
  }, []);

  return chartTheme;
}

/**
 * The resolved palette on its own, for a chart that builds its own option object
 * rather than handing ECharts a theme. Module charts need individual colours —
 * a series colour, the adverse tone for a breached floor — and this is the only
 * honest way to get one: read it from the live document, and re-read it when the
 * theme changes.
 */
export function useChartTokens(): BiChartTokens {
  return useChartTheme().tokens;
}
