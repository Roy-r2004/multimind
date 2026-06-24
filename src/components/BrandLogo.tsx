export function BrandLogo({ className }: { className?: string }) {
  return (
    <img
      src="/favicon.svg"
      alt=""
      aria-hidden="true"
      className={className ? `rounded-lg ${className}` : "rounded-lg"}
    />
  );
}
