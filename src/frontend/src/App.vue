<script setup lang="ts">
import { watch } from "vue";
import { useSession } from "./stores/session";
const session = useSession();
watch(
  () => session.user?.theme,
  (theme) => {
    document.documentElement.dataset.theme = theme ?? "light";
  },
  { immediate: true },
);
</script>
<template>
  <RouterView />
  <div class="small-window">
    请扩大窗口至至少 1024 × 768，以便完整使用学迹。
  </div>
</template>
