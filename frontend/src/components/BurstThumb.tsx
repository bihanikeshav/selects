interface BurstThumbProps {
  src: string;
  badge: string;
  isGold?: boolean;
  isSilver?: boolean;
  onClick: () => void;
  alt?: string;
}

export default function BurstThumb({ src, badge, isGold, isSilver, onClick, alt }: BurstThumbProps) {
  const cls = [
    "burst-thumb",
    isGold ? "is-gold" : "",
    isSilver ? "is-silver" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button className={cls} onClick={onClick}>
      <img src={src} alt={alt ?? ""} />
      <span className="badge">{badge}</span>
    </button>
  );
}
