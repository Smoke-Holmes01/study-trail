<script setup lang="ts">
import { computed } from "vue";
import { renderMarkdown } from "../lib/markdown";
const props = defineProps<{ text: string; interactive?: boolean }>();
const emit = defineEmits<{ source: [id: string] }>();
const html = computed(() => renderMarkdown(props.text));
function click(event: MouseEvent) {
  if (
    props.interactive === false &&
    (event.target as HTMLElement).closest("a")
  ) {
    event.preventDefault();
    return;
  }
  const link = (event.target as HTMLElement).closest<HTMLElement>(
    "[data-source]",
  );
  if (link) {
    event.preventDefault();
    emit("source", link.dataset.source!);
  }
}
</script>
<template><div class="markdown" v-html="html" @click="click" /></template>
