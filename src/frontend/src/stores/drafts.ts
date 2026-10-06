import { defineStore } from "pinia";
import { reactive } from "vue";
import type { Attachment } from "../lib/api";
export type SendBody = {
  content_text: string;
  skill_id?: string | null;
  attachment_ids: string[];
  target_plan_id: string | null;
  expected_plan_version: number | null;
};
export type Draft = {
  text: string;
  skill_id?: string;
  images: { attachment: Attachment; file?: File }[];
  submittedTaskId?: string;
  pendingSend?: { key: string; body: SendBody; signature: string };
};
export const useDrafts = defineStore("drafts", () => {
  const owners = reactive(new Map<string, Map<string, Draft>>());
  function forOwner(owner: string) {
    if (!owners.has(owner)) owners.set(owner, new Map());
    return owners.get(owner)!;
  }
  function clearOwner(owner: string) {
    owners.delete(owner);
  }
  return { forOwner, clearOwner };
});
