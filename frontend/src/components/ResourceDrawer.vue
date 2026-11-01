<script setup lang="ts">
import { ref, watch, nextTick, onBeforeUnmount } from "vue";
const props = defineProps<{ open: boolean; compact: boolean }>();
const emit = defineEmits<{ close: [] }>();
const panel = ref<HTMLDialogElement>();
let previous: HTMLElement | null = null;
watch(
  () => [props.open, props.compact],
  async () => {
    await nextTick();
    if (props.open && props.compact && !panel.value?.open) {
      previous = document.activeElement as HTMLElement;
      panel.value?.showModal();
    } else if ((!props.open || !props.compact) && panel.value?.open) {
      panel.value.close();
      previous?.focus();
    }
  },
  { immediate: true },
);
onBeforeUnmount(() => panel.value?.close());
</script>
<template>
  <dialog
    v-if="compact"
    ref="panel"
    class="resource-drawer"
    aria-label="资源列表"
    @cancel.prevent="emit('close')"
    @click="$event.target === panel && emit('close')"
  >
    <button
      class="drawer-close icon-button"
      aria-label="关闭资源列表"
      @click="emit('close')"
    >
      ×
    </button>
    <div class="drawer-content"><slot /></div>
  </dialog>
  <slot v-else />
</template>
