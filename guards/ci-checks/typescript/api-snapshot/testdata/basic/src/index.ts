/** Opens a path. */
export function open(path: string, x = 5): number {
  return helper(path.length + x);
}

function helper(n: number): number {
  return n;
}

export function load(id: number): string;
export function load(id: string, strict?: boolean): string;
export function load(id: number | string): string {
  return String(id);
}

export const twice = (n: number, by = 2): number => n * by;

export default function main(): void {}

export { Box, Color, VERSION, type Options, type Pair } from "./box.js";
export * as util from "./util.js";
