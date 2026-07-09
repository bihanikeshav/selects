import { useId } from "react";

/** The selects brand mark: a rounded diamond with the four brand-colour
 *  quadrants around a dark centre. Multi-colour (not currentColor). */
export default function Logo({ size = 22, className }: { size?: number; className?: string }) {
  const id = useId();
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 128 128"
      fill="none"
      className={className}
      aria-hidden="true"
    >
      <g transform="rotate(45 64 64)">
        <clipPath id={id}>
          <rect x="26" y="26" width="76" height="76" rx="22" />
        </clipPath>
        <g clipPath={`url(#${id})`}>
          <rect x="64" y="26" width="38" height="38" fill="#78df93" />
          <rect x="64" y="64" width="38" height="38" fill="#f5cf68" />
          <rect x="26" y="64" width="38" height="38" fill="#ff7c78" />
          <rect x="26" y="26" width="38" height="38" fill="#7aa7ff" />
        </g>
        <rect x="45" y="45" width="38" height="38" rx="12" fill="#09090f" opacity="0.75" />
      </g>
    </svg>
  );
}
