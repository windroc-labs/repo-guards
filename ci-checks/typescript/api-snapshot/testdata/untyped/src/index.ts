import u, { bar } from "untyped";
import { foo as hop } from "./mid.js";

export { foo } from "untyped";
export { bar };
export default u;
export { foo as viaMid } from "./mid.js";
export { foo as viaMid2 } from "./mid2.js";
export { hop };

export const own = 1;
