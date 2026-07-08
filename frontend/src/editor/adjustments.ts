// Modular adjustment registry for the in-app photo editor.
//
// Each Adjustment is fully self-contained: its slider range AND its GLSL math.
// The engine (engine.ts) assembles the fragment shader by concatenating every
// adjustment's `glsl` in order, so ADDING A NEW ADJUSTMENT = ONE entry here.
//
// GLSL contract: each snippet may read its `uniform float <uniform>` and must
// transform the working variable `color` (a linear-ish vec3 in ~[0,1]). Helper
// functions available in scope: `luma(vec3)`, `rgb2hsl(vec3)`, `hsl2rgb(vec3)`.

export interface Adjustment {
  key: string;        // stable id, also the params-object key
  label: string;      // UI label
  group: string;      // UI section
  min: number;
  max: number;
  step: number;
  default: number;
  uniform: string;    // GLSL uniform name
  glsl: string;       // transforms `color`; `default` value must be a no-op
}

export const ADJUSTMENTS: Adjustment[] = [
  // ── Light ────────────────────────────────────────────────────────────────
  {
    key: "exposure", label: "Exposure", group: "Light",
    min: -2, max: 2, step: 0.01, default: 0, uniform: "u_exposure",
    glsl: `color *= pow(2.0, u_exposure);`,
  },
  {
    key: "contrast", label: "Contrast", group: "Light",
    min: -100, max: 100, step: 1, default: 0, uniform: "u_contrast",
    glsl: `{ float c = 1.0 + u_contrast / 100.0; color = (color - 0.5) * c + 0.5; }`,
  },
  {
    key: "highlights", label: "Highlights", group: "Light",
    min: -100, max: 100, step: 1, default: 0, uniform: "u_highlights",
    // Push/pull the bright end, masked by luminance so shadows are untouched.
    glsl: `{ float m = smoothstep(0.5, 1.0, luma(color)); color += (u_highlights / 100.0) * 0.5 * m; }`,
  },
  {
    key: "shadows", label: "Shadows", group: "Light",
    min: -100, max: 100, step: 1, default: 0, uniform: "u_shadows",
    glsl: `{ float m = 1.0 - smoothstep(0.0, 0.5, luma(color)); color += (u_shadows / 100.0) * 0.5 * m; }`,
  },
  {
    key: "whites", label: "Whites", group: "Light",
    min: -100, max: 100, step: 1, default: 0, uniform: "u_whites",
    glsl: `{ float m = smoothstep(0.7, 1.0, luma(color)); color += (u_whites / 100.0) * 0.35 * m; }`,
  },
  {
    key: "blacks", label: "Blacks", group: "Light",
    min: -100, max: 100, step: 1, default: 0, uniform: "u_blacks",
    glsl: `{ float m = 1.0 - smoothstep(0.0, 0.3, luma(color)); color += (u_blacks / 100.0) * 0.35 * m; }`,
  },

  // ── Color ────────────────────────────────────────────────────────────────
  {
    key: "temperature", label: "Temperature", group: "Color",
    min: -100, max: 100, step: 1, default: 0, uniform: "u_temperature",
    glsl: `{ float t = u_temperature / 100.0; color.r += t * 0.12; color.b -= t * 0.12; }`,
  },
  {
    key: "tint", label: "Tint", group: "Color",
    min: -100, max: 100, step: 1, default: 0, uniform: "u_tint",
    glsl: `{ float t = u_tint / 100.0; color.g -= t * 0.10; color.r += t * 0.05; color.b += t * 0.05; }`,
  },
  {
    key: "vibrance", label: "Vibrance", group: "Color",
    min: -100, max: 100, step: 1, default: 0, uniform: "u_vibrance",
    // Saturation boost weighted toward less-saturated pixels (protects skin/sky).
    glsl: `{ vec3 h = rgb2hsl(color); float amt = (u_vibrance / 100.0) * (1.0 - h.y); h.y = clamp(h.y + amt, 0.0, 1.0); color = hsl2rgb(h); }`,
  },
  {
    key: "saturation", label: "Saturation", group: "Color",
    min: -100, max: 100, step: 1, default: 0, uniform: "u_saturation",
    glsl: `{ vec3 h = rgb2hsl(color); h.y = clamp(h.y * (1.0 + u_saturation / 100.0), 0.0, 1.0); color = hsl2rgb(h); }`,
  },
];

export type EditParams = Record<string, number>;

export function defaultParams(): EditParams {
  const p: EditParams = {};
  for (const a of ADJUSTMENTS) p[a.key] = a.default;
  return p;
}

export function isDefault(params: EditParams): boolean {
  return ADJUSTMENTS.every((a) => (params[a.key] ?? a.default) === a.default);
}
