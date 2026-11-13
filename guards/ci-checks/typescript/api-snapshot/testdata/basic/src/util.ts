export function clamp(n: number, lo = 0, hi = 1): number {
  return Math.min(hi, Math.max(lo, n));
}

export namespace Shapes {
  export interface Point {
    x: number;
  }
  export function origin(): Point {
    return { x: 0 };
  }
}
