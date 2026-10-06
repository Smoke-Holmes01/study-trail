<script setup lang="ts">
import { computed } from "vue";
import { renderMarkdown } from "../lib/markdown";
import type { Source } from "../lib/api";
const props = withDefaults(
  defineProps<{ text: string; interactive?: boolean; sources?: Source[] }>(),
  { interactive: true },
);
const emit = defineEmits<{ source: [id: string, anchor: HTMLElement] }>();
const html = computed(() =>
  renderMarkdown(props.text, props.sources, props.interactive),
);
function click(event: MouseEvent) {
  if (
    props.interactive === false &&
    (event.target as HTMLElement).closest("a, [data-source]")
  ) {
    event.preventDefault();
    return;
  }
  const link = (event.target as HTMLElement).closest<HTMLElement>(
    "[data-source]",
  );
  if (link) {
    event.preventDefault();
    emit("source", link.dataset.source!, link);
  }
}
</script>
<template><div class="markdown" v-html="html" @click="click" /></template>
