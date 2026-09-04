/**
 * Tags are stored with underscores ("prayer_flags"); people read spaces.
 * Display-only — the raw tag stays the value sent to the API.
 */
export function tagLabel(tag: string): string {
  return tag.replace(/_/g, " ");
}
