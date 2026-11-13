import u, { bar } from "untyped";
import * as whole from "untyped";
import { foo as hop } from "./mid.js";
import x = require("./a.cjs");
import y = whole.foo;

export { foo } from "untyped";
export { bar };
export default u;
export { foo as viaMid } from "./mid.js";
export { foo as viaMid2 } from "./mid2.js";
export { hop, x, y };

export const own = 1;
