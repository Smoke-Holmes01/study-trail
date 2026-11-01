<script setup lang="ts">
import { ref, watch, nextTick, useId } from "vue";
import { useResponsive } from "../composables/useResponsive";
const { height } = useResponsive();
const props = defineProps<{ open: boolean; title: string; wide?: boolean }>();
const emit = defineEmits<{ close: [] }>();
const titleId = useId();
const dialog = ref<HTMLDialogElement>();
let previous: HTMLElement | null = null;
watch(
  () => props.open,
  async (open) => {
    await nextTick();
    if (open && !dialog.value?.open) {
      previous = document.activeElement as HTMLElement;
      dialog.value?.showModal();
    }
    if (!open && dialog.value?.open) {
      dialog.value.close();
      previous?.focus();
    }
  },
  { immediate: true },
);
function backdrop(event: MouseEvent) {
  if (event.target !== dialog.value) return;
  const rect = dialog.value.getBoundingClientRect();
  if (
    event.clientX < rect.left ||
    event.clientX > rect.right ||
    event.clientY < rect.top ||
    event.clientY > rect.bottom
  )
    emit("close");
}
</script>
<template>
  <dialog
    ref="dialog"
    :style="{ maxHeight: `calc(${height} - 16px)` }"
    :class="{ wide }"
    :aria-labelledby="titleId"
    @cancel.prevent="emit('close')"
    @click="backdrop"
  >
    <header>
      <h2 :id="titleId">{{ title }}</h2>
      <button class="icon-button" aria-label="关闭弹窗" @click="emit('close')">
        ×
      </button>
    </header>
    <div class="modal-body"><slot /></div>
    <footer v-if="$slots.footer"><slot name="footer" /></footer>
  </dialog>
</template>
