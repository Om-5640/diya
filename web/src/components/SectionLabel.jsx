export default function SectionLabel({ children, className = "" }) {
  return (
    <div className={`text-h2 font-semibold uppercase text-text-tertiary ${className}`}>
      {children}
    </div>
  );
}
