/** Light cinematic atmosphere for Scraping Council pages. */

type Props = {
  className?: string;
  /** denser motion when a scrape is live */
  intensity?: "calm" | "live";
};

export function ScrapingDreamSky({ className, intensity = "calm" }: Props) {
  const motes = intensity === "live" ? 22 : 12;

  return (
    <div
      aria-hidden
      className={`pointer-events-none absolute inset-0 overflow-hidden ${className ?? ""}`}
    >
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(110% 70% at 12% -8%, rgba(125,211,252,0.42), transparent 58%), radial-gradient(90% 60% at 92% 0%, rgba(167,243,208,0.34), transparent 52%), radial-gradient(80% 55% at 70% 100%, rgba(253,230,138,0.28), transparent 48%), linear-gradient(180deg, #f7fbff 0%, #eef7f4 46%, #f8f5eb 100%)",
        }}
      />

      <div
        className="dream-drift absolute -left-[18%] top-[-20%] h-[62%] w-[62%] rounded-full blur-[90px]"
        style={{ background: "rgba(56, 189, 248, 0.28)" }}
      />
      <div
        className="dream-drift-alt absolute -right-[12%] top-[4%] h-[52%] w-[52%] rounded-full blur-[100px]"
        style={{ background: "rgba(52, 211, 153, 0.22)" }}
      />
      <div
        className="dream-drift absolute bottom-[-18%] left-[28%] h-[46%] w-[56%] rounded-full blur-[110px]"
        style={{ background: "rgba(251, 191, 36, 0.16)" }}
      />

      {/* soft horizon wash */}
      <div
        className="absolute inset-x-0 bottom-0 h-1/3"
        style={{
          background:
            "linear-gradient(to top, rgba(248,245,235,0.9), rgba(238,247,244,0.35), transparent)",
        }}
      />

      {Array.from({ length: motes }).map((_, i) => (
        <span
          key={i}
          className="scrape-mote absolute rounded-full"
          style={{
            width: i % 4 === 0 ? 5 : 3,
            height: i % 4 === 0 ? 5 : 3,
            left: `${(i * 41) % 100}%`,
            bottom: `${6 + (i % 9) * 4}%`,
            background:
              i % 3 === 0
                ? "rgba(14,116,144,0.45)"
                : i % 3 === 1
                  ? "rgba(16,185,129,0.4)"
                  : "rgba(245,158,11,0.38)",
            animationDelay: `${(i % 11) * 0.45}s`,
            animationDuration: `${5.5 + (i % 5) * 0.7}s`,
          }}
        />
      ))}

      {intensity === "live" &&
        Array.from({ length: 5 }).map((_, i) => (
          <span
            key={`beam-${i}`}
            className="scrape-sky-beam absolute bottom-0 w-px bg-gradient-to-t from-transparent via-sky-400/50 to-transparent"
            style={{
              left: `${14 + i * 16}%`,
              height: `${28 + i * 8}%`,
              animationDelay: `${i * 0.8}s`,
            }}
          />
        ))}
    </div>
  );
}
