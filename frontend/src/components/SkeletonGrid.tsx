export default function SkeletonGrid({ count = 12 }: { count?: number }) {
  return (
    <div className="skeleton-grid" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="skeleton-tile" />
      ))}
    </div>
  );
}
