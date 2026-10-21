<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  useId,
  watch,
} from "vue";
import { Check, ChevronDown, Sparkles } from "lucide-vue-next";
import type { ModelOption } from "../lib/api";
const props = defineProps<{
  models: ModelOption[];
  value: string;
  disabled?: boolean;
}>();
const emit = defineEmits<{ select: [id: string] }>();
const selected = computed(() =>
  props.models.find((model) => model.id === props.value),
);
const open = ref(false);
const trigger = ref<HTMLButtonElement>();
const panel = ref<HTMLElement>();
const listId = useId();
const position = ref({ left: "8px", top: "8px", width: "304px" });
let frame = 0;
function close(restoreFocus = false) {
  open.value = false;
  if (restoreFocus) trigger.value?.focus();
}
function place() {
  if (!open.value || !trigger.value?.isConnected) return;
  const rect = trigger.value.getBoundingClientRect();
  if (rect.bottom < 0 || rect.top > innerHeight) {
    close();
    return;
  }
  const width = Math.min(304, innerWidth - 16);
  const height = panel.value?.offsetHeight ?? 240;
  const above = rect.top - height - 8;
  position.value = {
    width: `${width}px`,
    left: `${Math.max(8, Math.min(rect.left, innerWidth - width - 8))}px`,
    top: `${Math.max(8, Math.min(above >= 8 ? above : rect.bottom + 8, innerHeight - height - 8))}px`,
  };
}
function schedule() {
  cancelAnimationFrame(frame);
  frame = requestAnimationFrame(place);
}
function options() {
  return Array.from(
    panel.value?.querySelectorAll<HTMLButtonElement>(
      '[role="option"]:not(:disabled)',
    ) ?? [],
  );
}
async function show() {
  if (props.disabled) return;
  open.value = true;
  await nextTick();
  place();
  const buttons = options();
  (
    buttons.find((button) => button.dataset.model === props.value) ??
    buttons[0] ??
    panel.value
  )?.focus({ preventScroll: true });
}
function choose(model: ModelOption) {
  if (!model.enabled) return;
  close(true);
  if (model.id !== props.value) emit("select", model.id);
}
function keydown(event: KeyboardEvent) {
  if (event.key === "Escape" && open.value) {
    event.preventDefault();
    event.stopPropagation();
    close(true);
    return;
  }
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  if (!open.value) {
    void show();
    return;
  }
  const buttons = options();
  if (!buttons.length) return;
  const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
  const next =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? buttons.length - 1
        : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) %
          buttons.length;
  buttons[next]?.focus();
  buttons[next]?.scrollIntoView({ block: "nearest" });
}
function outside(event: Event) {
  if (!open.value) return;
  const target = event.target as Node;
  if (!trigger.value?.contains(target) && !panel.value?.contains(target))
    close();
}
watch(
  () => [props.value, props.disabled],
  () => close(),
);
onMounted(() => {
  document.addEventListener("pointerdown", outside);
  document.addEventListener("focusin", outside);
  window.addEventListener("resize", schedule);
  window.addEventListener("scroll", schedule, true);
});
onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", outside);
  document.removeEventListener("focusin", outside);
  window.removeEventListener("resize", schedule);
  window.removeEventListener("scroll", schedule, true);
  cancelAnimationFrame(frame);
});
</script>
<template>
  <button
    ref="trigger"
    type="button"
    class="model-select-trigger"
    :class="{ expanded: open }"
    :disabled="disabled"
    aria-label="选择生成模型"
    aria-haspopup="listbox"
    :aria-expanded="open"
    :aria-controls="open ? listId : undefined"
    @click="open ? close() : show()"
    @keydown="keydown"
  >
    <Sparkles :size="14" aria-hidden="true" />
    <span class="model-select-name">{{
      selected?.enabled ? selected.display_name : "当前模型不可用"
    }}</span>
    <span v-if="selected?.enabled" class="model-capability">{{
      selected.supports_images ? "图片" : "文字"
    }}</span>
    <ChevronDown :size="14" class="model-select-chevron" aria-hidden="true" />
  </button>
  <Teleport to="body">
    <div
      v-if="open"
      ref="panel"
      class="model-select-panel"
      :style="position"
      tabindex="-1"
      @keydown="keydown"
    >
      <div class="model-select-heading">选择模型</div>
      <div
        :id="listId"
        class="model-select-options"
        role="listbox"
        aria-label="可用模型"
      >
        <button
          v-for="model in models"
          :key="model.id"
          type="button"
          role="option"
          :data-model="model.id"
          :aria-selected="model.id === value"
          :disabled="!model.enabled"
          :class="{ selected: model.id === value }"
          @click="choose(model)"
        >
          <span class="model-option-icon"
            ><Sparkles :size="17" aria-hidden="true"
          /></span>
          <span class="model-option-label"
            ><strong>{{ model.display_name }}</strong
            ><small>{{
              !model.enabled
                ? "暂不可用"
                : model.supports_images
                  ? "支持图片与文字"
                  : "仅支持文字"
            }}</small></span
          >
          <Check
            v-if="model.id === value"
            :size="16"
            class="model-option-check"
            aria-hidden="true"
          />
        </button>
        <p v-if="!models.length" class="muted">暂无可用模型</p>
      </div>
      <div class="model-select-note">切换后用于下一次回复</div>
    </div>
  </Teleport>
</template>
