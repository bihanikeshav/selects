interface BurstThumbProps {
  src: string;
  badge: string;
  isGold?: boolean;
  isLiked?: boolean;
  onClick: () => void;
  alt?: string;
}

export default function BurstThumb({ src, badge, isGold, isLiked, onClick, alt }: BurstThumbProps) {
  const cls = [
    "burst-thumb",
    isGold ? "is-gold" : "",
    isLiked ? "is-liked" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button className={cls} onClick={onClick}>
      <img src={src} alt={alt ?? ""} />
      <span className="badge">{badge}</span>
      {isLiked && (
        <span className="liked-pip" aria-label="liked">
          <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor" aria-hidden="true">
            <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
          </svg>
        </span>
      )}
    </button>
  );
}
