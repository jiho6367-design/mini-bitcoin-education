import { test as base } from "@playwright/test";

// Personal education records belong to the same browser owner cookie as the UI.
export const test = base.extend({
  request: async ({ page }, use) => {
    await use(page.request);
  },
});
export { expect } from "@playwright/test";
export type { Page, APIRequestContext } from "@playwright/test";
