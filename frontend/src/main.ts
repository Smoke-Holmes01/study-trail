import { createApp } from "vue";
import { createPinia } from "pinia";
import { createRouter, createWebHistory } from "vue-router";
import App from "./App.vue";
import AdminView from "./views/AdminView.vue";
import AdminLogin from "./views/AdminLogin.vue";
import { useAdmin } from "./stores/admin";
import "./admin.css";
import AuthView from "./views/AuthView.vue";
import WorkspaceView from "./views/WorkspaceView.vue";
import { useSession } from "./stores/session";
import "./style.css";
import "katex/dist/katex.min.css";
const pinia = createPinia();
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/admin/login", component: AdminLogin },
    { path: "/admin/:section?", component: AdminView },
    { path: "/login", component: AuthView },
    { path: "/register", component: AuthView },
    { path: "/", redirect: "/agents" },
    { path: "/agents", component: WorkspaceView },
    { path: "/knowledge-bases", component: WorkspaceView },
  ],
});
router.beforeEach(async (to) => {
  if (to.path.startsWith("/admin")) {
    const admin = useAdmin(pinia);
    if (!admin.ready) await admin.restore();
    if (!admin.user && to.path !== "/admin/login") return "/admin/login";
    if (admin.user && to.path === "/admin/login") return "/admin";
    const valid = ["overview", "users", "models", "prompts", "skills", "mcp", "agents", "file-runs", "task-runs", "settings", "operation-logs"];
    if (to.params.section && !valid.includes(String(to.params.section))) return "/admin";
    return;
  }
  const session = useSession(pinia);
  if (!session.ready) await session.restore();
  const auth = ["/login", "/register"].includes(to.path);
  if (!session.user && !auth) return "/login";
  if (session.user && auth) return "/agents";
});
window.addEventListener("study-trail:unauthorized", () => {
  useSession(pinia).clear();
  router.push("/login");
});
window.addEventListener("study-trail:admin-unauthorized", () => {
  useAdmin(pinia).clear(); router.push("/admin/login");
});
createApp(App).use(pinia).use(router).mount("#app");
