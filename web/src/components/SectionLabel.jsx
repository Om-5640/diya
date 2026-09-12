export default function SectionLabel({ children, className = "" }) {
  return <div className={`text-label uppercase text-text-tertiary ${className}`}>{children}</div>;
}
