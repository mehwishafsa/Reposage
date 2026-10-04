import { Api } from "./api";
import * as util from "./util";
import format from "./format.js";
import { helper } from "some-library";

/** Program entry point. */
export function main(): void {
  const api = new Api();
  api.fetchUser(1);
  util.log(format("hi"));
  helper(new globalThis.Api());
}
