<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { useAdmin } from "../stores/admin";
const account = ref("");
const password = ref("");
const error = ref("");
const busy = ref(false);
const admin = useAdmin();
const router = useRouter();
async function submit() {
  error.value = "";
  busy.value = true;
  try {
    await admin.login(account.value.trim(), password.value);
    password.value = "";
    await router.push("/admin");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "登录失败";
    password.value = "";
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <main class="admin-login">
    <section class="admin-login-card">
      <div class="admin-brand">
        <span class="admin-mark">迹</span>
        <div><strong>学迹</strong><small>PLATFORM CONSOLE</small></div>
      </div>
      <h1>管理端登录</h1>
      <p class="muted">使用部署时预置的管理员账户，管理平台能力与运行。</p>
      <form @submit.prevent="submit">
        <label
          >管理员账号<input
            v-model="account"
            autocomplete="username"
            required
            maxlength="32"
            autofocus
        /></label>
        <label
          >密码<input
            v-model="password"
            type="password"
            autocomplete="current-password"
            required
        /></label>
        <p v-if="error" class="admin-error" role="alert">{{ error }}</p>
        <button class="primary" :disabled="busy">
          {{ busy ? "正在登录…" : "登录管理端" }}
        </button>
      </form>
      <RouterLink to="/login">返回用户端</RouterLink>
    </section>
  </main>
</template>
