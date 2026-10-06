<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useSession } from "../stores/session";
import { mutation } from "../lib/api";
const route = useRoute();
const router = useRouter();
const session = useSession();
const registering = computed(() => route.path === "/register");
const login = ref("");
const password = ref("");
const confirmation = ref("");
const error = ref("");
const notice = ref("");
const busy = ref(false);
async function submit() {
  error.value = "";
  busy.value = true;
  try {
    if (registering.value) {
      await mutation("/auth/register", {
        login: login.value,
        password: password.value,
        confirm_password: confirmation.value,
      });
      password.value = "";
      confirmation.value = "";
      notice.value = "注册成功，请登录。";
      await router.push("/login");
    } else {
      await session.login(login.value, password.value);
      password.value = "";
      await router.push("/agents");
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <main class="auth-screen">
    <section class="auth-story">
      <div class="brand">
        <span class="brand-mark">迹</span>学迹 <small>Study Trail</small>
      </div>
      <div>
        <span class="eyebrow">YOUR OWN LEARNING SPACE</span>
        <h1>每一步学习，<br />都有迹可循。</h1>
        <p>
          把问题、资料与目标放在一起。<br />让理解慢慢深入，让学习清晰向前。
        </p>
        <div class="trail-art"><i /><i /><i /><i /><i /><i /></div>
      </div>
      <small>智能辅导 · 知识库 · 学习计划</small>
    </section>
    <section class="auth-form">
      <form @submit.prevent="submit">
        <span class="eyebrow">欢迎来到学迹</span>
        <h2>{{ registering ? "创建你的账户" : "继续你的学习旅程" }}</h2>
        <p class="muted">
          {{
            registering
              ? "一个属于自己的学习空间。"
              : "登录后，接着上一次的探索。"
          }}
        </p>
        <label
          >账号<input
            v-model="login"
            autocomplete="username"
            minlength="3"
            maxlength="32"
            pattern="[a-zA-Z0-9_]+"
            required
            placeholder="3–32 位字母、数字或下划线"
        /></label>
        <label
          >密码<input
            v-model="password"
            type="password"
            :autocomplete="registering ? 'new-password' : 'current-password'"
            minlength="8"
            maxlength="128"
            required
            placeholder="8–128 个字符"
        /></label>
        <label v-if="registering"
          >确认密码<input
            v-model="confirmation"
            type="password"
            autocomplete="new-password"
            required
        /></label>
        <p v-if="error" role="alert" class="error-box">{{ error }}</p>
        <p v-if="notice" role="status" class="success-box">{{ notice }}</p>
        <button class="primary full" :disabled="busy">
          {{ busy ? "请稍候…" : registering ? "注册账户" : "登录" }}
        </button>
        <p class="auth-switch">
          {{ registering ? "已有账户？" : "还没有账户？"
          }}<RouterLink :to="registering ? '/login' : '/register'">{{
            registering ? "去登录" : "创建账户"
          }}</RouterLink>
        </p>
      </form>
    </section>
  </main>
</template>
