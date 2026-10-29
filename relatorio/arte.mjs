// Ilustração low-poly em SVG, gerada por código com a paleta da marca (sem imagens externas).
// Determinística: a mesma semente produz sempre o mesmo desenho.
function rng(semente) {
  let s = semente >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const misturar = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
const css = (c) => `rgb(${c.map((v) => Math.round(Math.max(0, Math.min(255, v)))).join(",")})`;

function rampa(paradas, t) {
  for (let i = 1; i < paradas.length; i++) {
    if (t <= paradas[i][0]) {
      const [p0, c0] = paradas[i - 1];
      const [p1, c1] = paradas[i];
      return misturar(rgb(c0), rgb(c1), (t - p0) / (p1 - p0 || 1));
    }
  }
  return rgb(paradas[paradas.length - 1][1]);
}

const pt = (p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`;
const poligono = (pontos, cor) => `<polygon points="${pontos.map(pt).join(" ")}" fill="${cor}" stroke="${cor}" stroke-width="0.7" stroke-linejoin="round"/>`;

function arvore(r, { x, y, s, paleta }) {
  const partes = [];
  partes.push(poligono([[x - s * 0.07, y], [x + s * 0.07, y], [x + s * 0.04, y - s * 0.62], [x - s * 0.04, y - s * 0.62]], css(rgb("#14382d"))));
  const cx = x;
  const cy = y - s * 1.15;
  const n = 10;
  const anel = Array.from({ length: n }, (_, i) => {
    const a = (i / n) * Math.PI * 2 + r() * 0.25;
    const raio = s * (0.78 + r() * 0.34);
    return [cx + Math.cos(a) * raio, cy + Math.sin(a) * raio * 0.86];
  });
  for (let i = 0; i < n; i++) {
    const base = rgb(paleta[Math.floor(r() * paleta.length)]);
    const l = (r() - 0.5) * 26;
    partes.push(poligono([[cx, cy], anel[i], anel[(i + 1) % n]], css(base.map((v) => v + l))));
  }
  return partes.join("");
}

/**
 * @param {{w:number,h:number,seed?:number,cols?:number,rows?:number,stops:[number,string][],tiras?:number[][],arvores?:{x:number,y:number,s:number,paleta:string[]}[]}} o
 */
export function arte({ w, h, seed = 1, cols = 22, rows = 12, stops, tiras = [], arvores = [] }) {
  const r = rng(seed);
  const dx = w / cols;
  const dy = h / rows;
  const grade = [];
  for (let j = 0; j <= rows; j++) {
    const linha = [];
    for (let i = 0; i <= cols; i++) {
      const bordaX = i === 0 || i === cols;
      const bordaY = j === 0 || j === rows;
      linha.push([i * dx + (bordaX ? 0 : (r() - 0.5) * dx * 0.8), j * dy + (bordaY ? 0 : (r() - 0.5) * dy * 0.8)]);
    }
    grade.push(linha);
  }
  const saida = [];
  const triangulo = (a, b, c) => {
    const cy = (a[1] + b[1] + c[1]) / 3 / h;
    const cx = (a[0] + b[0] + c[0]) / 3 / w;
    const t = Math.min(1, Math.max(0, cy + (r() - 0.5) * 0.12));
    const luz = (r() - 0.5) * 30 + (0.5 - cx) * 10;
    saida.push(poligono([a, b, c], css(rampa(stops, t).map((v) => v + luz))));
  };
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      const a = grade[j][i];
      const b = grade[j][i + 1];
      const c = grade[j + 1][i];
      const d = grade[j + 1][i + 1];
      if (r() < 0.5) {
        triangulo(a, b, c);
        triangulo(b, d, c);
      } else {
        triangulo(a, b, d);
        triangulo(a, d, c);
      }
    }
  }
  for (const [x, y, tw, th] of tiras) saida.push(`<rect x="${x}" y="${y}" width="${tw}" height="${th}" fill="#ffffff" fill-opacity="0.2"/>`);
  for (const a of arvores) saida.push(arvore(r, a));
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">${saida.join("")}</svg>`;
}

export const PALETA_ROSA = ["#D64573", "#B93C63", "#E8789A", "#F29BB4"];
export const PALETA_VERDE = ["#2E5C41", "#3F7A57", "#6BA37F", "#A7B7AA"];
