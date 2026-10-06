import { defineStore } from "pinia";
import { useDrafts } from "./drafts";
import { ref } from "vue";
import { api, mutation, patch, setCsrf, type Student } from "../lib/api";
export const useSession = defineStore("session", () => {
  const user = ref<Student | null>(null);
  const ready = ref(false);
  async function restore() {
    try {
      user.value = await api<Student>("/me");
      setCsrf((await api<{ csrf_token: string }>("/auth/csrf")).csrf_token);
    } catch {
      user.value = null;
    }
    ready.value = true;
  }
  async function login(login: string, password: string) {
    const result = await mutation<{ student: Student; csrf_token: string }>(
      "/auth/login",
      { login, password },
    );
    user.value = result.student;
    setCsrf(result.csrf_token);
  }
  async function update(
    body: Partial<Pick<Student, "display_name" | "theme">>,
  ) {
    user.value = await patch<Student>("/me", body);
  }
  async function logout() {
    await mutation("/auth/logout");
    if (user.value) useDrafts().clearOwner(user.value.id);
    clear();
  }
  function clear() {
    user.value = null;
    setCsrf("");
  }
  return { user, ready, restore, login, update, logout, clear };
});
