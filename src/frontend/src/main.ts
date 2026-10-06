import { createApp } from "vue";
import { createPinia } from "pinia";
import { createRouter, createWebHistory } from "vue-router";
import App from "./App.vue";
import AuthView from "./views/AuthView.vue";
import WorkspaceView from "./views/WorkspaceView.vue";
import { useSession } from "./stores/session";
import "./style.css";
import "katex/dist/katex.min.css";
const pinia = createPinia();
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", component: AuthView },
    { path: "/register", component: AuthView },
    { path: "/", redirect: "/agents" },
    { path: "/agents", component: WorkspaceView },
    { path: "/knowledge-bases", component: WorkspaceView },
  ],
});
router.beforeEach(async (to) => {
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
createApp(App).use(pinia).use(router).mount("#app");
