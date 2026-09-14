/**
 * A point estimate with its confidence interval, one row per series.
 *
 * Deliberately NOT a bar chart. Every figure this project reports is a point
 * estimate with an interval, and the interval is the more informative object —
 * on 45 questions a gap of 0.05 sits comfortably inside the noise. A bar encodes
 * the point and silently discards the interval, which is the single most
 * misleading thing a chart of these numbers could do. A dot with a whisker
 * encodes both, and the reference line says where "no effect" is.
 *
 * Rules it keeps, each for a reason the tables already follow:
 *
 * - **A row with no value is drawn as absent, not as zero.** An arm with no
 *   detector has no AUROC; plotting it at 0 or 0.5 would put a placeholder on a
 *   chart where the truth is "not measured".
 * - **Underpowered rows carry the flag, not a different colour.** Status colour
 *   is reserved and never doubles as a series hue, so the warning travels as a
 *   mark beside the label — the same amber flag the metric table uses.
 * - **Colour is never the only encoding.** Every row is labelled and every value
 *   is printed; the chart is a second reading of the table, not a substitute.
 */

export interface IntervalRow {
  label: string
  /** null means UNMEASURED - the row is drawn as absent, never at zero. */
  value: number | null
  low: number | null
  high: number | null
  /** Shown as an amber flag beside the label, as in the metric table. */
  underpowered?: boolean
  /** Extra context for the row's tooltip. */
  note?: string | null
}

interface Props {
  rows: IntervalRow[]
  /** Where "no effect" sits: 0 for a contrast, 0.5 for an AUROC (chance). */
  reference: number
  referenceLabel: string
  caption: string
  /** Fixes the x-domain; otherwise it is taken from the data with padding. */
  domain?: [number, number]
  /** Limits the padded domain to what the quantity can take - [0, 1] for an
   *  AUROC, whose axis otherwise ended at an impossible 1.060. */
  bounds?: [number, number]
  places?: number
}

const ROW_H = 30
const PAD_TOP = 26
const PAD_BOTTOM = 34
const LABEL_W = 128
const VALUE_W = 190
const PLOT_W = 400

export function IntervalChart({
  rows,
  reference,
  referenceLabel,
  caption,
  domain,
  bounds,
  places = 3,
}: Props) {
  const drawn = rows.filter((r) => r.value !== null)
  if (drawn.length === 0) {
    return (
      <p className="note">
        Nothing to plot — no row in this selection carries a measured value.
      </p>
    )
  }

  const numbers = drawn.flatMap((r) =>
    [r.value, r.low, r.high].filter((n): n is number => n !== null),
  )
  const rawMin = Math.min(...numbers, reference)
  const rawMax = Math.max(...numbers, reference)
  const pad = (rawMax - rawMin) * 0.12 || 0.05
  const [min, max] = domain ?? [
    Math.max(rawMin - pad, bounds?.[0] ?? -Infinity),
    Math.min(rawMax + pad, bounds?.[1] ?? Infinity),
  ]
  const span = max - min || 1

  const x = (v: number) => LABEL_W + ((v - min) / span) * PLOT_W
  const height = PAD_TOP + rows.length * ROW_H + PAD_BOTTOM
  const width = LABEL_W + PLOT_W + VALUE_W

  // Ends only. The reference is labelled above its own line, and drawing it here
  // too collided with a nearby end tick - 0.44 and 0.50 rendered as "0.440.500".
  const ticks = [min, max]

  return (
    <figure className="chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={caption}
        preserveAspectRatio="xMinYMin meet"
      >
        {/* Reference line: chance for an AUROC, no-effect for a contrast. */}
        <line
          x1={x(reference)}
          x2={x(reference)}
          y1={PAD_TOP - 10}
          y2={PAD_TOP + rows.length * ROW_H - 6}
          className="chart-reference"
        />
        <text x={x(reference)} y={PAD_TOP - 15} className="chart-tick" textAnchor="middle">
          {referenceLabel}
        </text>

        {ticks.map((t, i) => (
          <text
            key={`${t}-${i}`}
            x={x(t)}
            y={PAD_TOP + rows.length * ROW_H + 16}
            className="chart-tick"
            textAnchor={i === 0 ? 'start' : 'end'}
          >
            {t.toFixed(places)}
          </text>
        ))}

        {rows.map((row, i) => {
          const y = PAD_TOP + i * ROW_H + ROW_H / 2
          const hasInterval = row.low !== null && row.high !== null
          return (
            <g key={row.label}>
              <title>
                {row.label}:{' '}
                {row.value === null
                  ? 'not measured'
                  : `${row.value.toFixed(places)}${
                      hasInterval
                        ? ` [${row.low!.toFixed(places)}, ${row.high!.toFixed(places)}]`
                        : ''
                    }`}
                {row.underpowered ? ' — underpowered' : ''}
                {row.note ? ` — ${row.note}` : ''}
              </title>

              <text x={LABEL_W - 10} y={y + 4} className="chart-label" textAnchor="end">
                {row.label}
              </text>

              {row.value === null ? (
                // Absent, not zero. Said in words on the plot itself.
                <text x={LABEL_W + 8} y={y + 4} className="chart-absent">
                  not measured
                </text>
              ) : (
                <>
                  {hasInterval && (
                    <>
                      <line
                        x1={x(row.low!)}
                        x2={x(row.high!)}
                        y1={y}
                        y2={y}
                        className="chart-whisker"
                      />
                      <line
                        x1={x(row.low!)}
                        x2={x(row.low!)}
                        y1={y - 5}
                        y2={y + 5}
                        className="chart-whisker"
                      />
                      <line
                        x1={x(row.high!)}
                        x2={x(row.high!)}
                        y1={y - 5}
                        y2={y + 5}
                        className="chart-whisker"
                      />
                    </>
                  )}
                  {/* A surface-coloured ring keeps the dot readable where it
                      overlaps its own whisker. */}
                  <circle cx={x(row.value)} cy={y} r={5.5} className="chart-dot-halo" />
                  <circle cx={x(row.value)} cy={y} r={4} className="chart-dot" />
                  <text x={LABEL_W + PLOT_W + 10} y={y + 4} className="chart-value">
                    {row.value.toFixed(places)}
                    {hasInterval && (
                      <tspan className="chart-value-ci">
                        {' '}
                        [{row.low!.toFixed(places)}, {row.high!.toFixed(places)}]
                      </tspan>
                    )}
                  </text>
                </>
              )}

              {row.underpowered && (
                <text x={LABEL_W - 10} y={y + 15} className="chart-flag" textAnchor="end">
                  underpowered
                </text>
              )}
            </g>
          )
        })}
      </svg>
      <figcaption>{caption}</figcaption>
    </figure>
  )
}
