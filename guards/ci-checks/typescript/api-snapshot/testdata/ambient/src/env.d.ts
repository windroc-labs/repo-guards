declare module "*.css";

declare module "*.svg" {
  const url: string;
  export default url;
}

declare module "virtual:cfg" {
  export interface Cfg {
    a: number;
  }
}

declare module "virtual:*" {
  const value: number;
  export default value;
}
