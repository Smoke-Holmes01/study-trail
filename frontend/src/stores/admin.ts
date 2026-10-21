import { defineStore } from "pinia";
import { ref } from "vue";
import { adminApi, adminCsrf, type Admin } from "../lib/admin";
export const useAdmin = defineStore("admin-session", () => {
  const user = ref<Admin | null>(null);
  const ready = ref(false);
  async function restore() {
    try {
      user.value = await adminApi<Admin>("/me");
      adminCsrf((await adminApi("/auth/csrf")).csrf_token);
    } catch {
      clear();
    }
    ready.value = true;
  }
  async function login(login: string, password: string) {
    const result = await adminApi("/auth/login", "POST", { login, password });
    user.value = result.admin;
    adminCsrf(result.csrf_token);
    ready.value = true;
  }
  function clear() {
    user.value = null;
    adminCsrf("");
  }
  async function logout() {
    await adminApi("/auth/logout", "POST", {});
    clear();
  }
  return { user, ready, restore, login, logout, clear };
});
