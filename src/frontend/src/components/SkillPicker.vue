<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Skill } from "../lib/api";
const props = defineProps<{ skills: Skill[]; query: string }>();
const emit = defineEmits<{ select: [skill: Skill]; close: [] }>();
const active = ref(0);
const matches = computed(() =>
  props.skills.filter((s) =>
    `${s.name} ${s.description}`
      .toLowerCase()
      .includes(props.query.toLowerCase()),
  ),
);
watch(
  () => [props.query, props.skills],
  () => {
    active.value = 0;
  },
);
const activeId = computed(() =>
  matches.value.length
    ? `skill-option-${matches.value[active.value]?.id}`
    : undefined,
);
function handleKey(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229 || event.shiftKey)
    return false;
  if (event.key === "Escape") {
    event.preventDefault();
    emit("close");
    return true;
  }
  if (["ArrowDown", "ArrowUp"].includes(event.key)) {
    event.preventDefault();
    if (matches.value.length) {
      active.value =
        (active.value +
          (event.key === "ArrowDown" ? 1 : -1) +
          matches.value.length) %
        matches.value.length;
      document
        .getElementById(activeId.value ?? "")
        ?.scrollIntoView?.({ block: "nearest" });
    }
    return true;
  }
  if (event.key === "Enter" && matches.value[active.value]) {
    event.preventDefault();
    emit("select", matches.value[active.value]!);
    return true;
  }
  return false;
}
defineExpose({ handleKey, activeId });
</script>
<template>
  <div class="slash-picker" @mousedown.prevent>
    <div class="section-label">
      选择技能 <small>↑ ↓ 选择 · Enter 确定 · Esc 关闭</small>
    </div>
    <div id="skill-options" role="listbox" aria-label="可用技能">
      <button
        v-for="(skill, i) in matches"
        :id="`skill-option-${skill.id}`"
        :key="skill.id"
        type="button"
        role="option"
        :aria-selected="i === active"
        :class="{ active: i === active }"
        @mouseenter="active = i"
        @click="emit('select', skill)"
      >
        <strong>/{{ skill.name }}</strong
        ><span>{{ skill.description }}</span>
      </button>
      <p v-if="!matches.length" class="muted">
        {{
          skills.length ? "没有匹配的技能。" : "当前智能体暂无已开启的技能。"
        }}
      </p>
    </div>
  </div>
</template>
