// WebGL engine for the in-app editor. Assembles a single fragment shader from
// the ADJUSTMENTS registry, renders the image with the current params to a
// canvas, and can export the result to a Blob (bake). No dependencies.

import { ADJUSTMENTS, type EditParams } from "./adjustments";

const VERT = `
attribute vec2 a_pos;
varying vec2 v_uv;
void main() {
  v_uv = vec2(a_pos.x * 0.5 + 0.5, 0.5 - a_pos.y * 0.5); // flip Y (top-left origin)
  gl_Position = vec4(a_pos, 0.0, 1.0);
}`;

const HELPERS = `
precision highp float;
varying vec2 v_uv;
uniform sampler2D u_tex;
float luma(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }
vec3 rgb2hsl(vec3 c) {
  float mx = max(max(c.r, c.g), c.b);
  float mn = min(min(c.r, c.g), c.b);
  float l = (mx + mn) * 0.5;
  float h = 0.0, s = 0.0;
  float d = mx - mn;
  if (d > 1e-5) {
    s = l > 0.5 ? d / (2.0 - mx - mn) : d / (mx + mn);
    if (mx == c.r)      h = (c.g - c.b) / d + (c.g < c.b ? 6.0 : 0.0);
    else if (mx == c.g) h = (c.b - c.r) / d + 2.0;
    else                h = (c.r - c.g) / d + 4.0;
    h /= 6.0;
  }
  return vec3(h, s, l);
}
float hue2rgb(float p, float q, float t) {
  if (t < 0.0) t += 1.0;
  if (t > 1.0) t -= 1.0;
  if (t < 1.0/6.0) return p + (q - p) * 6.0 * t;
  if (t < 1.0/2.0) return q;
  if (t < 2.0/3.0) return p + (q - p) * (2.0/3.0 - t) * 6.0;
  return p;
}
vec3 hsl2rgb(vec3 hsl) {
  float h = hsl.x, s = hsl.y, l = hsl.z;
  if (s <= 1e-5) return vec3(l);
  float q = l < 0.5 ? l * (1.0 + s) : l + s - l * s;
  float p = 2.0 * l - q;
  return vec3(hue2rgb(p, q, h + 1.0/3.0), hue2rgb(p, q, h), hue2rgb(p, q, h - 1.0/3.0));
}`;

function buildFragmentShader(): string {
  const uniforms = ADJUSTMENTS.map((a) => `uniform float ${a.uniform};`).join("\n");
  const pipeline = ADJUSTMENTS.map((a) => `  ${a.glsl}`).join("\n");
  return `${HELPERS}\n${uniforms}\nvoid main() {\n  vec3 color = texture2D(u_tex, v_uv).rgb;\n${pipeline}\n  gl_FragColor = vec4(clamp(color, 0.0, 1.0), 1.0);\n}`;
}

function compile(gl: WebGLRenderingContext, type: number, src: string): WebGLShader {
  const sh = gl.createShader(type)!;
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(sh);
    gl.deleteShader(sh);
    throw new Error("shader compile failed: " + log);
  }
  return sh;
}

export class EditorEngine {
  private gl: WebGLRenderingContext;
  private program: WebGLProgram;
  private uniformLoc = new Map<string, WebGLUniformLocation | null>();
  private tex: WebGLTexture | null = null;
  private w = 0;
  private h = 0;

  constructor(private canvas: HTMLCanvasElement) {
    const gl = canvas.getContext("webgl", { preserveDrawingBuffer: true, premultipliedAlpha: false });
    if (!gl) throw new Error("WebGL not available");
    this.gl = gl;

    const prog = gl.createProgram()!;
    gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, buildFragmentShader()));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      throw new Error("program link failed: " + gl.getProgramInfoLog(prog));
    }
    this.program = prog;
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, "a_pos");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    for (const a of ADJUSTMENTS) this.uniformLoc.set(a.uniform, gl.getUniformLocation(prog, a.uniform));
  }

  setImage(img: TexImageSource, width: number, height: number): void {
    const gl = this.gl;
    this.w = width;
    this.h = height;
    this.canvas.width = width;
    this.canvas.height = height;
    if (this.tex) gl.deleteTexture(this.tex);
    this.tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, 0);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
  }

  render(params: EditParams): void {
    const gl = this.gl;
    gl.viewport(0, 0, this.w, this.h);
    gl.useProgram(this.program);
    for (const a of ADJUSTMENTS) {
      const l = this.uniformLoc.get(a.uniform);
      if (l) gl.uniform1f(l, params[a.key] ?? a.default);
    }
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  toBlob(type = "image/jpeg", quality = 0.92): Promise<Blob> {
    return new Promise((resolve, reject) => {
      this.canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("toBlob failed"))), type, quality);
    });
  }

  dispose(): void {
    const gl = this.gl;
    if (this.tex) gl.deleteTexture(this.tex);
    gl.deleteProgram(this.program);
  }
}
