export default function SegmentedControl({ options, value, onChange }) {
  return (
    <div className="inline-flex items-center bg-bg-panel border border-border-subtle rounded p-1 gap-1">
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={`px-3 py-1.5 text-body rounded-sm border transition-colors duration-150 ease-out whitespace-nowrap ${
              active
                ? "border-border-strong bg-bg-panel-raised text-text-primary"
                : "border-transparent text-text-secondary hover:text-text-primary"
            }`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
