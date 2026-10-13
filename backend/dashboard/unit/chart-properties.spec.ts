// @vitest-environment jsdom
import { expect, it } from "vitest";
import fc from "fast-check";
import {
  gapAwareData,
  hasAnyValue,
  tooltipHtml,
  waterfallBase,
} from "../lib/echartsOptions";

it("preserves every reading and gap, including a lone measured zero", () => {
  fc.assert(
    fc.property(
      fc.array(
        fc.oneof(fc.integer(), fc.constant(null), fc.constant(undefined)),
        { maxLength: 50 },
      ),
      fc.integer({ min: 1, max: 20 }),
      (values, size) => {
        const data = gapAwareData(Object.freeze(values), size);
        expect(
          data.map((point) =>
            typeof point === "object" && point !== null ? point.value : point,
          ),
        ).toEqual(values.map((value) => value ?? null));
        expect(hasAnyValue(values)).toBe(
          values.some((value) => typeof value === "number"),
        );
        values.forEach((value, index) => {
          if (
            value != null &&
            values[index - 1] == null &&
            values[index + 1] == null
          ) {
            // ECharts needs a visible marker when no line segment can reach a reading.
            expect(data[index]).toMatchObject({
              value,
              symbol: "circle",
              symbolSize: size,
            });
          }
        });
      },
    ),
    { numRuns: 100 },
  );
});

it("waterfall bars join successive balances and preserve geometry when shifted", () => {
  fc.assert(
    fc.property(
      fc.array(fc.integer({ min: -100_000, max: 100_000 }), { maxLength: 40 }),
      fc.integer({ min: -1_000_000, max: 1_000_000 }),
      fc.integer({ min: -1_000_000, max: 1_000_000 }),
      (deltas, start, shift) => {
        const chart = waterfallBase(Object.freeze(deltas), start);
        const shifted = waterfallBase(deltas, start + shift);
        expect(chart.bars).toHaveLength(deltas.length);
        expect(chart.base).toHaveLength(deltas.length);
        deltas.forEach((delta, index) => {
          const before =
            start +
            deltas.slice(0, index).reduce((sum, value) => sum + value, 0);
          const after = before + delta;
          expect(chart.base[index]).toBe(Math.min(before, after));
          expect(chart.base[index] + chart.bars[index]).toBe(
            Math.max(before, after),
          );
          expect(shifted.base[index]).toBe(chart.base[index] + shift);
        });
        expect(shifted.bars).toEqual(chart.bars);
      },
    ),
    { numRuns: 100 },
  );
});

it("shows tenant tooltip labels as text even when they contain HTML", () => {
  const text = fc
    .array(fc.constantFrom("<", ">", "&", '"', "'", "é", "行", "a", "0", " "), {
      minLength: 1,
      maxLength: 40,
    })
    .map((characters) => characters.join(""));
  fc.assert(
    fc.property(text, text, text, text, (title, label, value, note) => {
      // ECharts consumes this public formatter's HTML through the DOM.
      const tooltip = document.createElement("div");
      tooltip.innerHTML = tooltipHtml(title, [{ label, value, note }]);
      expect(tooltip.textContent).toBe(`${title}${label}: ${value} · ${note}`);
      expect(tooltip.querySelector("script, img, iframe")).toBeNull();
    }),
    { numRuns: 100 },
  );
});
