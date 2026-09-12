/** ScenarioSegmentedControl(options, value, onChange): a single-row segmented
 * control. Active segment gets an elevated surface + soft shadow (one of the
 * two named exceptions to "no shadows"); inactive segments are flat. */
export default function ScenarioSegmentedControl({ options, value, onChange }) {
  return (
    <div className="inline-flex items-center bg-bg-surface-sunken border border-border-subtle rounded-md p-1 gap-1">
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={`px-3.5 py-1.5 text-body rounded-md transition-colors duration-150 ease-out whitespace-nowrap ${
              active
                ? "bg-bg-surface text-brand font-medium shadow-elevated"
                : "text-text-secondary hover:text-text-primary"
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
