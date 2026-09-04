interface BurstThumbProps {
  src: string;
  badge: string;
  isGold?: boolean;
  /** Marks the frame as kept (a green tick pip and ring). */
  isKept?: boolean;
  onClick: () => void;
  alt?: string;
}

export default function BurstThumb({ src, badge, isGold, isKept, onClick, alt }: BurstThumbProps) {
  const cls = [
    "burst-thumb",
    isGold ? "is-gold" : "",
    isKept ? "is-kept" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button type="button" className={cls} onClick={onClick}>
      <img src={src} alt={alt ?? ""} />
      <span className="badge">{badge}</span>
      {isKept && (
        <span className="kept-pip" aria-label="kept">
          <svg
            viewBox="0 0 24 24"
            width="11"
            height="11"
            fill="none"
            stroke="currentColor"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M20 6 9 17l-5-5" />
          </svg>
        </span>
      )}
    </button>
  );
}
