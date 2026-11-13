declare module "virtual:api" {
  export * from "nope-missing";
  export const k: number;
}

declare module "virtual:other" {
  export * from "also-missing";
}
