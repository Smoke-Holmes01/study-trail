<script setup lang="ts">
import { useRoute } from "vue-router";
import { watch } from "vue";
import { useSession } from "./stores/session";
const route = useRoute();
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
  <div v-if="route.path.startsWith('/admin')" class="small-window">
    请扩大窗口至至少 1024 × 768，以便完整使用学迹。
  </div>
</template>
