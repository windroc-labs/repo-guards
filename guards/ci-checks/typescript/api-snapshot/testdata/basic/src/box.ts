export interface Options {
  readonly size?: number;
  label: string;
  (x: number): void;
  [key: string]: unknown;
}

class Base {
  base(): void {}
}

export class Box<T extends object = object> extends Base {
  static count = 0;
  readonly value: T;
  #secret = 1;
  private hidden = 2;
  protected guard = 3;

  constructor(value: T, scale = 2) {
    super();
    this.value = value;
    this.hidden = scale + this.#secret;
  }

  get size(): number {
    return this.hidden;
  }

  set size(v: number) {
    this.hidden = v;
  }

  grow(by = 1): this {
    this.hidden += by;
    return this;
  }

  static create(): Box<object> {
    return new Box({});
  }

  create(): void {}
}

export enum Color {
  Red,
  Green = "g",
}

export const VERSION = "1.0";

export type Pair<A> = [A, A];
