/**
 * Hand-built SVG village scene, driven entirely by props derived from the
 * current step. Static/diagrammatic on purpose -- no animation loops, no
 * rotating blades: it must read instantly, not entertain.
 */
export default function VillageVisual({ solarActive, windActive, dieselActive, batteryPct, criticalOK }) {
  const pct = Math.max(0, Math.min(100, batteryPct ?? 0));
  const battFillHeight = (66 * pct) / 100;

  return (
    <svg viewBox="0 0 900 300" className="w-full h-auto" role="img" aria-label="Village power system diagram">
      {/* ground */}
      <line x1="0" y1="270" x2="900" y2="270" stroke="var(--border-subtle)" strokeWidth="2" />

      {/* households, abstracted as a row of houses */}
      {[50, 115, 180, 245].map((x, i) => (
        <g key={i}>
          <rect x={x} y={225} width={48} height={45} fill="var(--bg-panel-raised)" stroke="var(--border-subtle)" strokeWidth="1.5" />
          <polygon
            points={`${x - 6},225 ${x + 24},198 ${x + 54},225`}
            fill="var(--bg-panel-raised)"
            stroke="var(--border-subtle)"
            strokeWidth="1.5"
          />
          <rect x={x + 18} y={244} width={12} height={26} fill="var(--bg-base)" />
        </g>
      ))}

      {/* health centre */}
      <g>
        <rect x={335} y={190} width={110} height={80} fill="var(--bg-panel-raised)" stroke="var(--border-strong)" strokeWidth="2" />
        <polygon points="329,190 390,158 451,190" fill="var(--bg-panel-raised)" stroke="var(--border-strong)" strokeWidth="2" />
        <rect
          x={372}
          y={210}
          width={36}
          height={32}
          fill={criticalOK ? "var(--status-good)" : "var(--status-critical)"}
          fillOpacity={0.85}
        />
        <rect x={385} y={217} width={10} height={18} fill="var(--bg-panel)" />
        <rect x={378} y={223} width={24} height={6} fill="var(--bg-panel)" />
        <text x={390} y={285} textAnchor="middle" fontSize="12" fill="var(--text-tertiary)" fontFamily="Inter, sans-serif">
          HEALTH CENTRE
        </text>
      </g>

      {/* solar array */}
      <g>
        <rect
          x={505}
          y={205}
          width={95}
          height={48}
          fill={solarActive ? "var(--accent-solar)" : "var(--bg-panel-raised)"}
          fillOpacity={solarActive ? 0.85 : 1}
          stroke={solarActive ? "var(--accent-solar)" : "var(--border-subtle)"}
          strokeWidth="1.5"
          transform="skewX(-10)"
        />
        {[0, 1, 2, 3].map((i) => (
          <line
            key={i}
            x1={505 + i * 24}
            y1={205}
            x2={505 + i * 24 - 8}
            y2={253}
            stroke="var(--bg-base)"
            strokeWidth="1"
          />
        ))}
        <line x1={552} y1={253} x2={552} y2={270} stroke="var(--border-subtle)" strokeWidth="3" />
        <text x={552} y={288} textAnchor="middle" fontSize="12" fill="var(--text-tertiary)" fontFamily="Inter, sans-serif">
          SOLAR
        </text>
      </g>

      {/* wind turbine */}
      <g>
        <line x1={660} y1={270} x2={660} y2={150} stroke="var(--border-strong)" strokeWidth="4" />
        <circle cx={660} cy={150} r={5} fill={windActive ? "var(--accent-wind)" : "var(--text-tertiary)"} />
        {[0, 120, 240].map((deg) => (
          <line
            key={deg}
            x1={660}
            y1={150}
            x2={660 + 40 * Math.cos((deg * Math.PI) / 180)}
            y2={150 + 40 * Math.sin((deg * Math.PI) / 180)}
            stroke={windActive ? "var(--accent-wind)" : "var(--text-tertiary)"}
            strokeWidth="3"
            strokeLinecap="round"
          />
        ))}
        <text x={660} y={288} textAnchor="middle" fontSize="12" fill="var(--text-tertiary)" fontFamily="Inter, sans-serif">
          WIND
        </text>
      </g>

      {/* battery */}
      <g>
        <rect x={735} y={200} width={52} height={70} fill="none" stroke="var(--border-strong)" strokeWidth="2" />
        <rect x={753} y={192} width={16} height={8} fill="var(--border-strong)" />
        <rect
          x={737}
          y={270 - battFillHeight}
          width={48}
          height={battFillHeight}
          fill="var(--accent-battery)"
          fillOpacity={0.75}
        />
        <text x={761} y={288} textAnchor="middle" fontSize="12" fill="var(--text-tertiary)" fontFamily="Inter, sans-serif">
          BATTERY {Math.round(pct)}%
        </text>
      </g>

      {/* diesel genset */}
      <g>
        <rect
          x={815}
          y={225}
          width={62}
          height={45}
          fill="var(--bg-panel-raised)"
          stroke={dieselActive ? "var(--accent-diesel)" : "var(--border-subtle)"}
          strokeWidth="2"
        />
        <rect x={865} y={210} width={8} height={20} fill="var(--border-strong)" />
        {dieselActive && (
          <>
            <circle cx={873} cy={198} r={4} fill="var(--text-tertiary)" opacity={0.55} />
            <circle cx={880} cy={185} r={5} fill="var(--text-tertiary)" opacity={0.35} />
          </>
        )}
        <text x={846} y={288} textAnchor="middle" fontSize="12" fill="var(--text-tertiary)" fontFamily="Inter, sans-serif">
          DIESEL
        </text>
      </g>
    </svg>
  );
}
