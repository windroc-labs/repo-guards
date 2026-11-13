import { Core } from "@ws/core";

export { Core };

export function make(): Core {
  return new Core();
}
