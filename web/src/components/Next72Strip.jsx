import SectionLabel from "./SectionLabel";
import DispatchTimeline from "./DispatchTimeline";

export default function Next72Strip({ steps, currentIndex }) {
  const window = steps.slice(currentIndex, currentIndex + 72);

  return (
    <div>
      <SectionLabel className="mb-3">Next 72 Hours</SectionLabel>
      <DispatchTimeline steps={window} height={140} compact />
    </div>
  );
}
