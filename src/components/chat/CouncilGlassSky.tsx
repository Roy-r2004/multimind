/** Ethereal dark sky for Chat Council — matches glass mockup. */

export function CouncilGlassSky() {
  return (
    <div aria-hidden className="council-glass-sky pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div className="council-glass-sky__base" />
      <div className="council-glass-sky__arc" />
      <div className="council-glass-sky__waves" />
      <div className="council-glass-sky__orb council-glass-sky__orb--a" />
      <div className="council-glass-sky__orb council-glass-sky__orb--b" />
      <div className="council-glass-sky__orb council-glass-sky__orb--c" />
      <div className="council-glass-sky__vignette" />
    </div>
  );
}
