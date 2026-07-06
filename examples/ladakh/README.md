# Ladakh example trip data

These files are the original hardcoded, Ladakh-specific data that selects
once shipped in its Python source. They are kept here as a worked example of how
to customize a library for a particular trip or region.

## Files

- `landmarks.json` — ~50 named GPS landmarks (Pangong Tso, Nubra Valley, Leh,
  Hemis Monastery, ...). Each entry is
  `{"name": str, "lat": float, "lon": float, "radius_m": number}`. When a photo
  cluster's centroid falls within `radius_m` of a landmark, that landmark's name
  is used instead of a generic Nominatim reverse-geocode result. `radius_m` is
  optional (defaults to ~1500 m) and is internally capped at ~1500 m.
- `keywords.json` — theme buckets (`{label: [keyword, ...]}`) used to build
  "pattern" stories and thematic cross-cuts by matching a photo's visual tags.
- `tag_prompts.json` — the zero-shot SigLIP tag taxonomy
  (`{tag: [prompt, ...]}`), including region-specific tags like `prayer_flags`,
  `yak`, and `barren_terrain`.

## How to use

Copy any of these files into your photo folder's hidden state directory:

```
<your photo folder>/.selects/landmarks.json
<your photo folder>/.selects/keywords.json
<your photo folder>/.selects/tag_prompts.json
```

Each file is optional and independent. If a file is absent (or malformed),
selects falls back to its built-in, travel-generic defaults — for landmarks
that means relying entirely on Nominatim reverse-geocoding. Re-run the ML stages
after adding or editing a file to pick up the changes.
