// The score ring. Its job is to make "coaching vs evaluative" visually unambiguous:
//   - evaluative + calibrated → ring colored pass/fail vs a charcoal TARGET TICK.
//   - coaching / uncalibrated → neutral gold ring, NO target tick, NO pass/fail color,
//     and a small trend arrow (vs the person's own baseline) instead of a verdict.
// This mirrors the SVG donut in dashboard/views.py but is mode-aware.

interface Props {
  score: number;
  target: number | null; // null in coaching mode / uncalibrated
  verdict: "pass" | "fail" | null;
  trend?: number | null; // coaching: today vs baseline
  dim?: boolean; // low-confidence quarantine
  size?: number;
}

const R = 34;
const C = 2 * Math.PI * R;

export function ScoreDonut({ score, target, verdict, trend, dim, size = 84 }: Props) {
  const frac = Math.max(0, Math.min(1, score / 100));
  const dash = `${frac * C} ${C}`;
  // color: evaluative shows pass/fail; coaching is always neutral gold.
  const ringColor = verdict === "pass" ? "#1d7a3a" : verdict === "fail" ? "#b00020" : "#D4AF37";
  const opacity = dim ? 0.4 : 1;

  // target tick angle (only when calibrated + evaluative)
  let tick = null;
  if (target != null) {
    const a = (target / 100) * 2 * Math.PI - Math.PI / 2;
    const x1 = 50 + (R - 7) * Math.cos(a);
    const y1 = 50 + (R - 7) * Math.sin(a);
    const x2 = 50 + (R + 7) * Math.cos(a);
    const y2 = 50 + (R + 7) * Math.sin(a);
    tick = <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#0D0D0D" strokeWidth={2.5} />;
  }

  const arrowColor = trend == null ? "#5a5a5a" : trend > 0.5 ? "#1d7a3a" : trend < -0.5 ? "#b00020" : "#5a5a5a";
  const arrowGlyph = trend == null ? "•" : trend > 0.5 ? "▲" : trend < -0.5 ? "▼" : "•";

  return (
    <div className="relative inline-block" style={{ width: size, height: size, opacity }}>
      <svg viewBox="0 0 100 100" width={size} height={size}>
        <circle cx="50" cy="50" r={R} fill="none" stroke="#ececec" strokeWidth={10} />
        <circle
          cx="50"
          cy="50"
          r={R}
          fill="none"
          stroke={ringColor}
          strokeWidth={10}
          strokeDasharray={dash}
          strokeLinecap="round"
          transform="rotate(-90 50 50)"
        />
        {tick}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-serif font-bold text-ink" style={{ fontSize: size * 0.26, lineHeight: 1 }}>
          {Math.round(score)}
        </span>
        {target != null ? (
          <span className="font-sans font-medium text-muted" style={{ fontSize: size * 0.1 }}>
            /{Math.round(target)}
          </span>
        ) : trend != null ? (
          <span className="font-sans font-semibold" style={{ fontSize: size * 0.12, color: arrowColor }}>
            {arrowGlyph} {trend > 0 ? "+" : ""}
            {Math.round(trend)}
          </span>
        ) : (
          <span className="font-sans font-medium text-muted" style={{ fontSize: size * 0.1 }}>
            new
          </span>
        )}
      </div>
    </div>
  );
}
