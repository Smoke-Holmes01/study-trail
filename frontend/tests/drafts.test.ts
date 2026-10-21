import { beforeEach, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useDrafts } from "../src/stores/drafts";
beforeEach(() => setActivePinia(createPinia()));
it("keeps non-password drafts for reauthentication without exposing them to another owner", () => {
  const store = useDrafts();
  store
    .forOwner("student-a")
    .set("conversation", { text: "保留学习条件", images: [] });
  expect(useDrafts().forOwner("student-a").get("conversation")?.text).toBe(
    "保留学习条件",
  );
  expect(store.forOwner("student-b").has("conversation")).toBe(false);
  store.clearOwner("student-a");
  expect(store.forOwner("student-a").size).toBe(0);
});
