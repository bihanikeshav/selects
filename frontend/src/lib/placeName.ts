/**
 * Geocoded place names keep a disambiguation suffix ("Leh (2)") because that
 * exact string is the key for `/best/place/<name>` and `VisitOut.name`. Strip
 * it for display only — never for the value sent back to the API.
 */
export function stripPlaceSuffix(name: string): string {
  return name.replace(/ \(\d+\)$/, "");
}
