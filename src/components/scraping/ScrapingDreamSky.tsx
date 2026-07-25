/** Atmospheric dreamscape backdrop for Scraping Council surfaces. */

type Props = {
  className?: string;
  /** denser particles when live scrape is running */
  intensity?: "calm" | "live";
};

export function ScrapingDreamSky({ className, intensity = "calm" }: Props) {
  const stars = intensity === "live" ? 28 : 16;

  return (
    <div
      aria-hidden
      className={`pointer-events-none absolute inset-0 overflow-hidden ${className ?? ""}`}
    >
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(120% 80% at 50% -10%, #1a3d45 0%, #0b161c 42%, #070c10 100%)",
        }}
      />
      <div
        className="dream-drift absolute -left-[20%] top-[-25%] h-[70%] w-[70%] rounded-full blur-[90px]"
        style={{ background: "rgba(46, 120, 130, 0.28)" }}
      />
      <div
        className="dream-drift-alt absolute -right-[15%] top-[10%] h-[55%] w-[55%] rounded-full blur-[100px]"
        style={{ background: "rgba(212, 168, 75, 0.12)" }}
      />
      <div
        className="dream-drift absolute bottom-[-20%] left-[20%] h-[50%] w-[60%] rounded-full blur-[110px]"
        style={{ background: "rgba(24, 70, 90, 0.35)" }}
      />

      {/* horizon band */}
      <div
        className="absolute inset-x-0 bottom-0 h-1/3 opacity-70"
        style={{
          background:
            "linear-gradient(to top, rgba(7,12,16,0.95), rgba(26,61,69,0.25), transparent)",
        }}
      />

      {/* constellation dust */}
      {Array.from({ length: stars }).map((_, i) => (
        <span
          key={i}
          className="dream-twinkle absolute rounded-full bg-[#f3e6c4]"
          style={{
            width: i % 5 === 0 ? 3 : 2,
            height: i % 5 === 0 ? 3 : 2,
            left: `${(i * 37) % 100}%`,
            top: `${(i * 53) % 70}%`,
            animationDelay: `${(i % 9) * 0.35}s`,
            opacity: 0.35,
          }}
        />
      ))}

      {/* floating navigation shards */}
      {Array.from({ length: intensity === "live" ? 6 : 3 }).map((_, i) => (
        <span
          key={`shard-${i}`}
          className="dream-shard absolute h-8 w-px bg-gradient-to-t from-transparent via-[#d4a84b]/70 to-transparent"
          style={{
            left: `${12 + i * 14}%`,
            bottom: "8%",
            animationDelay: `${i * 1.1}s`,
          }}
        />
      ))}

      <div
        className="absolute inset-0 opacity-[0.07]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(243,230,196,0.5) 1px, transparent 1px), linear-gradient(90deg, rgba(243,230,196,0.5) 1px, transparent 1px)",
          backgroundSize: "72px 72px",
          maskImage: "radial-gradient(ellipse at center, black 20%, transparent 75%)",
        }}
      />
    </div>
  );
}
