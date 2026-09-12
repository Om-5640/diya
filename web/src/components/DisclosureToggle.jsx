import { useState } from "react";

/** DisclosureToggle(label, defaultOpen?, children): one-click expand/collapse
 * used for Evidence's "Advanced analysis" section (collapsed by default). */
export default function DisclosureToggle({ label, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="border border-border-subtle rounded-md bg-bg-surface">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex items-center justify-between px-6 py-4 text-body font-medium text-text-primary hover:bg-bg-surface-sunken transition-colors duration-150 ease-out rounded-md"
      >
        <span>{label}</span>
        <span
          aria-hidden
          className={`text-text-tertiary transition-transform duration-180 ease-out ${open ? "rotate-180" : ""}`}
        >
          ▾
        </span>
      </button>
      {open && <div className="px-6 pb-6 pt-6 border-t border-border-subtle">{children}</div>}
    </div>
  );
}
