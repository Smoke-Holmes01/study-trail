<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  Activity,
  Users,
  Cpu,
  FileText,
  Sparkles,
  Plug,
  Bot,
  Files,
  ListChecks,
  Settings,
  ScrollText,
  Sun,
  Moon,
  LogOut,
  RefreshCw,
  Plus,
  Search,
} from "lucide-vue-next";
import { adminApi, type Config, type Page } from "../lib/admin";
import { ApiError } from "../lib/api";
import { safeErrorReason } from "../lib/safe-errors";
import { useAdmin } from "../stores/admin";
import AdminConfigForm from "../components/AdminConfigForm.vue";
const route = useRoute();
const router = useRouter();
const admin = useAdmin();
const nav = [
  { id: "overview", label: "概览与统计", icon: Activity },
  { id: "users", label: "用户管理", icon: Users },
  { id: "models", label: "模型接口", icon: Cpu },
  { id: "prompts", label: "平台提示词", icon: FileText },
  { id: "skills", label: "Skills", icon: Sparkles },
  { id: "mcp", label: "MCP 服务", icon: Plug },
  { id: "agents", label: "智能体概况", icon: Bot },
  { id: "file-runs", label: "资源处理", icon: Files },
  { id: "task-runs", label: "学习任务", icon: ListChecks },
  { id: "settings", label: "系统设置", icon: Settings },
  { id: "operation-logs", label: "操作日志", icon: ScrollText },
];
const section = computed(() => String(route.params.section || "overview"));
const title = computed(
  () => nav.find((n) => n.id === section.value)?.label ?? "概览与统计",
);
const configKind = computed(
  () =>
    ({ models: "model", prompts: "prompt", skills: "skill", mcp: "mcp" })[
      section.value
    ],
);
const busy = ref(false);
const error = ref("");
const notice = ref("");
const rows = ref<any[]>([]);
const cursor = ref<string | null>(null);
const cursorStack = ref<(string | null)[]>([]);
const currentCursor = ref<string | null>(null);
const q = ref("");
const status = ref("");
const owner = ref("");
const modelKind = ref("");
const action = ref("");
const purpose = ref("");
function localDay(date: Date) {
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(
    date,
  );
}
const toDay = ref(localDay(new Date()));
const fromDay = ref(localDay(new Date(Date.now() - 29 * 86400000)));
const usage = ref<any>(null);
const taskReport = ref<any>(null);
const callReport = ref<any>(null);
const settings = ref<Record<string, any>>({});
const settingValues = ref<Record<string, number>>({});
const bindings = ref<any[]>([]);
const editing = ref<Config | null>(null);
const editorOpen = ref(false);
const editorKey = ref(0);
const saving = ref(false);
const testing = ref(false);
const editorError = ref("");
const editorFields = ref<Record<string, string>>({});
const testResult = ref("");
const selectedTask = ref<any>(null);
const taskCalls = ref<any[]>([]);
const callsCursor = ref<string | null>(null);
const confirmation = ref<{
  title: string;
  text: string;
  run: () => Promise<void>;
} | null>(null);
const dark = ref(localStorage.getItem("study-trail:admin-theme") === "dark");
watch(
  dark,
  (value) => {
    document.documentElement.dataset.theme = value ? "dark" : "light";
    localStorage.setItem("study-trail:admin-theme", value ? "dark" : "light");
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  document.documentElement.dataset.theme = "light";
});
const stateNames: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  succeeded: "成功",
  failed: "失败",
  stopped: "已停止",
  unknown: "未知 / 中断",
  success: "成功",
  failure: "失败",
};
const typeNames: Record<string, string> = {
  users: "普通用户",
  agents: "智能体",
  conversations: "对话",
  knowledge_bases: "知识库",
  files: "文件",
  learning_plans: "学习计划",
  generative: "生成",
  embedding: "嵌入",
  rerank: "重排",
  chat: "学习对话",
  prompt_generate: "提示词生成",
  prompt_polish: "提示词润色",
  file_process: "文件处理",
  model: "模型",
  mcp_tool: "工具",
};
const purposeNames: Record<string, string> = {
  intent: "意图识别",
  coverage: "资料覆盖判断",
  answer: "学习回答",
  plan_extract: "计划结构提取",
  plan_format_repair: "计划格式修复",
  prompt_generate: "提示词生成",
  prompt_polish: "提示词润色",
  context_summary: "上下文摘要",
};
const settingsLabels: Record<string, string> = {
  file_max_bytes: "单文件容量（字节）",
  batch_max_files: "每批文件数",
  image_max_bytes: "单图容量（字节）",
  message_max_images: "每条消息图片数",
  ai_timeout_seconds: "AI任务超时（秒）",
  file_timeout_seconds: "文件处理超时（秒）",
  tool_rounds: "工具轮数",
  tool_slots: "工具调用位置数",
  tool_result_max_bytes: "工具结果容量（字节）",
  tool_timeout_seconds: "工具单次超时（秒）",
};
function date(value: string | null) {
  return value
    ? new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        dateStyle: "short",
        timeStyle: "medium",
      }).format(new Date(value))
    : "—";
}
function short(value: string) {
  return value ? value.slice(0, 8) : "—";
}
function metric(value: any) {
  return value?.value == null
    ? "未采集"
    : `${value.value}${value.state === "partial" ? "（不完整）" : ""}`;
}
function duration(value: number | null) {
  return value == null ? "—" : (value / 1000).toFixed(2) + " 秒";
}
function message(e: unknown) {
  return e instanceof Error ? e.message : "操作未完成，请重试";
}
function interval() {
  if (!fromDay.value || !toDay.value || fromDay.value > toDay.value)
    throw new Error("请选择有效的日期范围");
  const from = new Date(fromDay.value + "T00:00:00+08:00");
  const to = new Date(
    new Date(toDay.value + "T00:00:00+08:00").getTime() + 86400000,
  );
  return new URLSearchParams({
    from: from.toISOString(),
    to: to.toISOString(),
  });
}
let revision = 0;
async function load(next: string | null = null) {
  const current = ++revision;
  busy.value = true;
  error.value = "";
  try {
    const pageSection = section.value;
    if (pageSection === "overview") {
      const query = interval().toString();
      const results = await Promise.all([
        adminApi(`/reports/usage?${query}`),
        adminApi(`/reports/tasks?${query}`),
        adminApi(`/reports/calls?${query}`),
      ]);
      if (current !== revision) return;
      [usage.value, taskReport.value, callReport.value] = results;
    } else if (pageSection === "settings") {
      const result = await adminApi("/settings");
      if (current !== revision) return;
      settings.value = result;
      settingValues.value = Object.fromEntries(
        Object.keys(settingsLabels).map((key) => [key, result[key]]),
      );
    } else {
      const query = new URLSearchParams({ limit: "20" });
      let path = "/" + pageSection;
      if (configKind.value) {
        path = "/configs/" + configKind.value;
        if (q.value.trim()) query.set("q", q.value.trim());
        if (status.value) query.set("enabled", status.value);
        if (modelKind.value && configKind.value === "model")
          query.set("model_kind", modelKind.value);
        if (purpose.value && configKind.value === "prompt")
          query.set("purpose", purpose.value);
      } else if (pageSection === "users") {
        if (q.value.trim()) query.set("q", q.value.trim());
        if (status.value) query.set("enabled", status.value);
      } else {
        interval().forEach((value, key) => query.set(key, value));
        if (owner.value.trim())
          query.set(
            pageSection === "operation-logs" ? "actor_id" : "owner_id",
            owner.value.trim(),
          );
        if (status.value)
          query.set(
            pageSection === "operation-logs" ? "result" : "status",
            status.value,
          );
        if (action.value && pageSection === "operation-logs")
          query.set("action", action.value);
        if (modelKind.value && pageSection === "task-runs")
          query.set("kind", modelKind.value);
      }
      if (next) query.set("cursor", next);
      const result = await adminApi<Page<any>>(path + "?" + query);
      let slots: any[] = [];
      if (configKind.value === "model" || configKind.value === "prompt")
        slots = (await adminApi("/bindings")).items;
      if (current !== revision) return;
      rows.value = result.items;
      cursor.value = result.next_cursor;
      currentCursor.value = next;
      bindings.value = slots;
    }
  } catch (e) {
    if (current === revision) error.value = message(e);
  } finally {
    if (current === revision) busy.value = false;
  }
}
function search() {
  cursorStack.value = [];
  load();
}
function nextPage() {
  cursorStack.value.push(currentCursor.value);
  load(cursor.value);
}
function prevPage() {
  load(cursorStack.value.pop() ?? null);
}
watch(
  section,
  () => {
    ++revision;
    rows.value = [];
    usage.value = null;
    q.value = "";
    status.value = "";
    owner.value = "";
    purpose.value = "";
    action.value = "";
    modelKind.value = "";
    editorOpen.value = false;
    editing.value = null;
    selectedTask.value = null;
    confirmation.value = null;
    cursorStack.value = [];
    notice.value = "";
    search();
  },
  { immediate: true },
);
function openEditor(row: Config | null) {
  editing.value = row;
  editorKey.value++;
  editorError.value = "";
  editorFields.value = {};
  testResult.value = "";
  editorOpen.value = true;
}
function captureFieldErrors(e: unknown) {
  if (!(e instanceof ApiError) || !Array.isArray(e.details.field_errors)) return;
  editorFields.value = Object.fromEntries(
    e.details.field_errors.filter((item: any) => typeof item.field === "string" && typeof item.message === "string")
      .map((item: any) => [item.field, item.message]),
  );
}
async function saveConfig(body: Record<string, unknown>) {
  saving.value = true;
  editorError.value = "";
  editorFields.value = {};
  try {
    await adminApi(
      `/configs/${configKind.value}${editing.value ? "/" + editing.value.id : ""}`,
      editing.value ? "PATCH" : "POST",
      body,
    );
    editorOpen.value = false;
    editing.value = null;
    notice.value = "配置已保存，仅影响后续受理的任务。";
    await load(currentCursor.value);
  } catch (e) {
    editorError.value = message(e);
    captureFieldErrors(e);
  } finally {
    saving.value = false;
  }
}
async function testConfig(draft: Record<string, unknown>) {
  testing.value = true;
  testResult.value = "";
  editorError.value = "";
  editorFields.value = {};
  try {
    const result = await adminApi("/config-tests", "POST", {
      kind: configKind.value,
      ...(editing.value
        ? {
            config_id: editing.value.id,
            expected_version: editing.value.lock_version,
          }
        : {}),
      draft,
    });
    testResult.value = `${result.message}（${result.elapsed_ms} ms）`;
  } catch (e) {
    editorError.value = message(e);
    captureFieldErrors(e);
  } finally {
    testing.value = false;
  }
}
async function reloadEditor() {
  if (!editing.value) return;
  confirmation.value = {
    title: "重新载入配置",
    text: "当前未保存的编辑将被丢弃，载入后台最新版本。",
    run: async () => {
      const fresh = await adminApi<Config>(
        `/configs/${configKind.value}/${editing.value!.id}`,
      );
      openEditor(fresh);
    },
  };
}
function toggle(row: any, user = false) {
  const enable = !row.enabled;
  const previous =
    !user && configKind.value === "prompt" && enable
      ? bindings.value.find((b) => b.slot === "prompt:" + row.purpose)
      : null;
  confirmation.value = {
    title: `${enable ? "启用" : "停用"}${user ? "账户" : "配置"}`,
    text: user
      ? enable
        ? "用户需要重新登录后才能访问。"
        : "立即撤销用户访问及流式接收，个人资料保留；已受理任务继续保存结果。"
      : previous
        ? "将替换同用途当前生效的提示词，已受理任务继续采用原版本。"
        : "更改将作用于后续请求，已受理任务不受影响。",
    run: async () => {
      await adminApi(
        user
          ? `/users/${row.id}/status`
          : `/configs/${configKind.value}/${row.id}/status`,
        "PATCH",
        {
          enabled: enable,
          expected_version: row.lock_version,
          ...(previous
            ? { replace_active_id: previous.config_id, confirm_replace: true }
            : {}),
        },
      );
      notice.value = "状态已更新。";
      await load(currentCursor.value);
    },
  };
}
function isBound(row: Config) {
  return bindings.value.some((b) => b.config_id === row.id);
}
function setBinding(row: Config) {
  const slot =
    row.payload.model_kind === "generative"
      ? "default_generation"
      : row.payload.model_kind;
  const current = bindings.value.find((b) => b.slot === slot);
  confirmation.value = {
    title: "切换平台默认模型",
    text: `将${typeNames[row.payload.model_kind]}用途切换为「${row.name}」。已有任务继续使用受理配置，嵌入切换还须满足索引兼容要求。`,
    run: async () => {
      await adminApi(`/bindings/${slot}`, "PATCH", {
        config_id: row.id,
        expected_version: current?.lock_version,
        confirm_switch: true,
      });
      notice.value = "平台默认模型已更新。";
      await load(currentCursor.value);
    },
  };
}
async function confirm() {
  if (!confirmation.value) return;
  saving.value = true;
  error.value = "";
  try {
    await confirmation.value.run();
    confirmation.value = null;
  } catch (e) {
    error.value = message(e);
    confirmation.value = null;
  } finally {
    saving.value = false;
  }
}
async function saveSettings() {
  saving.value = true;
  error.value = "";
  try {
    await adminApi("/settings", "PATCH", {
      ...settingValues.value,
      expected_version: settings.value.lock_version,
    });
    notice.value = "系统限制已保存，仅影响后续任务。";
    await load();
  } catch (e) {
    error.value = message(e);
  } finally {
    saving.value = false;
  }
}
async function showTask(row: any) {
  error.value = "";
  try {
    selectedTask.value = await adminApi(`/task-runs/${row.task_id}`);
    const page = await adminApi<Page<any>>(`/task-runs/${row.task_id}/calls`);
    taskCalls.value = page.items;
    callsCursor.value = page.next_cursor;
  } catch (e) {
    error.value = message(e);
  }
}
async function moreCalls() {
  try {
    const page = await adminApi<Page<any>>(
      `/task-runs/${selectedTask.value.task_id}/calls?cursor=${encodeURIComponent(callsCursor.value!)}`,
    );
    taskCalls.value.push(...page.items);
    callsCursor.value = page.next_cursor;
  } catch (e) {
    error.value = message(e);
  }
}
async function logout() {
  try {
    await admin.logout();
    await router.push("/admin/login");
  } catch (e) {
    error.value = message(e);
  }
}
const ordinaryList = computed(() =>
  ["agents", "file-runs", "task-runs", "operation-logs"].includes(
    section.value,
  ),
);
</script>
<template>
  <div class="admin-shell">
    <aside class="admin-sidebar">
      <RouterLink class="admin-brand" to="/admin"
        ><span class="admin-mark">迹</span>
        <div>
          <strong>学迹</strong><small>PLATFORM CONSOLE</small>
        </div></RouterLink
      >
      <p class="admin-nav-label">平台管理</p>
      <nav aria-label="管理导航">
        <RouterLink
          v-for="item in nav"
          :key="item.id"
          :to="item.id === 'overview' ? '/admin' : '/admin/' + item.id"
          :class="{ active: section === item.id }"
          ><component :is="item.icon" :size="17" />{{ item.label }}</RouterLink
        >
      </nav>
      <footer>
        <span>{{ admin.user?.display_name }}</span
        ><small>管理员</small
        ><button @click="logout"><LogOut :size="15" />退出管理端</button>
      </footer>
    </aside>
    <div class="admin-main">
      <header class="admin-topbar">
        <span>管理端 / {{ title }}</span>
        <div>
          <span class="admin-version">v0.0.2</span
          ><button
            class="icon"
            :aria-label="dark ? '切换明亮主题' : '切换暗色主题'"
            @click="dark = !dark"
          >
            <component :is="dark ? Sun : Moon" :size="18" />
          </button>
        </div>
      </header>
      <main class="admin-content">
        <div class="admin-heading">
          <div>
            <h1>{{ title }}</h1>
            <p class="muted">
              {{
                section === "overview"
                  ? "平台使用概况 · 当前存量与所选周期新增分别展示"
                  : configKind
                    ? "维护平台开放能力，配置变更只作用于后续受理任务"
                    : section === "settings"
                      ? "在技术硬上限内调整运行限制"
                      : "查询允许展示的账户信息与运行元数据"
              }}
            </p>
          </div>
          <button v-if="configKind" class="primary" @click="openEditor(null)">
            <Plus :size="17" />新建{{
              { model: "模型", prompt: "提示词", skill: "技能", mcp: "服务" }[
                configKind
              ]
            }}
          </button>
        </div>
        <p v-if="notice" class="admin-success" role="status">{{ notice }}</p>
        <div v-if="error" class="admin-error" role="alert">
          {{ error
          }}<button :disabled="busy" @click="load(currentCursor)">
            重新载入
          </button>
        </div>
        <form
          v-if="section !== 'settings'"
          class="admin-filters"
          @submit.prevent="search"
        >
          <template v-if="section === 'overview' || ordinaryList"
            ><label
              >开始日期<input v-model="fromDay" type="date" required /></label
            ><label
              >结束日期<input v-model="toDay" type="date" required /></label
            ><small>Asia/Shanghai</small></template
          >
          <label v-if="configKind || section === 'users'" class="admin-search"
            ><Search :size="16" /><input
              v-model="q"
              :placeholder="
                section === 'users' ? '账号或显示名称' : '搜索配置名称'
              "
              maxlength="100"
              aria-label="搜索"
          /></label>
          <label v-if="ordinaryList"
            >{{ section === "operation-logs" ? "操作者ID" : "归属用户ID"
            }}<input v-model="owner" placeholder="完整用户标识"
          /></label>
          <select
            v-if="section !== 'overview' && section !== 'agents'"
            v-model="status"
            aria-label="状态筛选"
          >
            <option value="">全部状态</option>
            <template v-if="configKind || section === 'users'"
              ><option value="true">启用</option>
              <option value="false">停用</option></template
            ><template v-else-if="section === 'operation-logs'"
              ><option value="success">成功</option>
              <option value="failure">失败</option></template
            ><template v-else
              ><option
                v-for="s in [
                  'queued',
                  'running',
                  'succeeded',
                  'failed',
                  'stopped',
                ]"
                :key="s"
                :value="s"
              >
                {{ stateNames[s] }}
              </option></template
            >
          </select>
          <select
            v-if="section === 'models'"
            v-model="modelKind"
            aria-label="模型类别"
          >
            <option value="">全部模型类别</option>
            <option
              v-for="v in ['generative', 'embedding', 'rerank']"
              :key="v"
              :value="v"
            >
              {{ typeNames[v] }}
            </option>
          </select>
          <select
            v-if="section === 'prompts'"
            v-model="purpose"
            aria-label="提示词用途"
          >
            <option value="">全部用途</option>
            <option v-for="(v, k) in purposeNames" :key="k" :value="k">
              {{ v }}
            </option>
          </select>
          <select
            v-if="section === 'task-runs'"
            v-model="modelKind"
            aria-label="任务类别"
          >
            <option value="">全部任务类别</option>
            <option
              v-for="v in ['chat', 'prompt_generate', 'prompt_polish']"
              :key="v"
              :value="v"
            >
              {{ typeNames[v] }}
            </option>
          </select>
          <label v-if="section === 'operation-logs'"
            >操作<input v-model="action" placeholder="完整操作名称"
          /></label>
          <button :disabled="busy">
            <RefreshCw :size="15" />{{ busy ? "加载中…" : "查询" }}
          </button>
        </form>
        <p
          v-if="busy && !rows.length && !usage"
          class="admin-empty"
          role="status"
        >
          正在载入…
        </p>
        <template v-if="section === 'overview' && usage">
          <div class="admin-stat-grid">
            <article
              v-for="(value, key) in usage.current_totals"
              :key="key"
              class="admin-card"
            >
              <span class="muted">{{ typeNames[String(key)] }} · 当前存量</span>
              <div class="admin-stat-value">{{ value }}</div>
              <small>周期新增 {{ metric(usage.new_counts[key]) }}</small>
            </article>
          </div>
          <div class="admin-report-grid">
            <section class="admin-card">
              <h2>任务状态 · 所选期间</h2>
              <div
                v-for="(value, key) in taskReport.counts"
                :key="key"
                class="admin-bar-row"
              >
                <span>{{ stateNames[String(key)] }}</span>
                <div class="admin-bar-track">
                  <div
                    :class="String(key)"
                    :style="{
                      width: `${Math.max(value ? 2 : 0, (100 * Number(value)) / Math.max(1, taskReport.total_tasks))}%`,
                    }"
                  />
                </div>
                <strong>{{ value }}</strong>
              </div>
              <p class="muted">任务覆盖：{{ metric(taskReport.coverage) }}</p>
            </section>
            <section class="admin-card">
              <h2>使用与调用</h2>
              <dl class="admin-details">
                <dt>活跃普通用户</dt>
                <dd>{{ metric(usage.active_users) }}</dd>
                <dt>平均执行耗时</dt>
                <dd>{{ duration(taskReport.average_duration_ms) }}</dd>
                <dt>完成耗时样本数</dt>
                <dd>{{ taskReport.completed_duration_samples }}</dd>
                <dt>平均排队耗时</dt>
                <dd>{{ duration(taskReport.average_queue_ms) }}</dd>
                <dt>模型与工具调用尝试</dt>
                <dd>{{ metric(callReport.coverage) }}</dd>
                <dt>模型 / 工具尝试</dt>
                <dd>{{ callReport.by_category.model ?? 0 }} / {{ callReport.by_category.mcp_tool ?? 0 }}</dd>
                <dt>调用成功 / 失败</dt>
                <dd>{{ callReport.counts.succeeded }} / {{ callReport.counts.failed }}</dd>
                <dt>未知 / 中断调用</dt>
                <dd>{{ callReport.counts.unknown }}</dd>
              </dl>
              <p class="muted">连接测试独立分类，不混入学习使用量。</p>
            </section>
          </div>
          <p class="admin-notice">
            当前存量采集于
            {{
              date(usage.snapshot_at)
            }}。历史覆盖不足显示“不完整”，未接入指标显示“未采集”，均不作为真实零值。
          </p>
        </template>
        <section
          v-if="section === 'settings' && settings.lock_version"
          class="admin-card"
        >
          <form @submit.prevent="saveSettings">
            <div class="admin-form-grid">
              <label v-for="(label, key) in settingsLabels" :key="key"
                >{{ label
                }}<input
                  v-model.number="settingValues[key]"
                  type="number"
                  :min="settings.bounds[key][0]"
                  :max="settings.bounds[key][1]"
                  required
                /><small
                  >允许范围 {{ settings.bounds[key][0] }}—{{
                    settings.bounds[key][1]
                  }}</small
                ></label
              >
            </div>
            <p class="admin-notice">
              保存仅影响后续任务。工具阶段最多三轮、六次调用位置，上传限制不能突破技术硬上限。
            </p>
            <button class="primary" :disabled="saving">
              {{ saving ? "正在保存…" : "保存系统设置" }}
            </button>
          </form>
        </section>
        <section
          v-if="section !== 'overview' && section !== 'settings'"
          class="admin-table-panel"
        >
          <div class="admin-table-scroll">
            <table>
              <thead>
                <tr v-if="configKind">
                  <th>名称 / 稳定标识</th>
                  <th>
                    {{
                      configKind === "model"
                        ? "用途"
                        : configKind === "prompt"
                          ? "处理用途"
                          : "说明"
                    }}
                  </th>
                  <th>内容版本</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
                <tr v-else-if="section === 'users'">
                  <th>账号 / 显示名称</th>
                  <th>用户标识</th>
                  <th>创建时间</th>
                  <th>最近登录</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
                <tr v-else-if="section === 'agents'">
                  <th>智能体标识</th>
                  <th>归属用户</th>
                  <th>对话 / 计划 / 知识库</th>
                  <th>最近任务</th>
                  <th>创建时间</th>
                </tr>
                <tr v-else-if="section === 'operation-logs'">
                  <th>操作时间</th>
                  <th>操作者</th>
                  <th>动作 / 目标类型</th>
                  <th>目标标识</th>
                  <th>结果</th>
                  <th>错误码</th>
                </tr>
                <tr v-else>
                  <th>任务标识</th>
                  <th>归属用户</th>
                  <th>
                    {{ section === "file-runs" ? "文件类型" : "任务类型" }}
                  </th>
                  <th>状态</th>
                  <th>受理时间</th>
                  <th>耗时</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in rows"
                  :key="row.id ?? row.task_id ?? row.agent_id"
                >
                  <template v-if="configKind"
                    ><td>
                      <strong>{{ row.name }}</strong
                      ><small
                        >{{ row.stable_id }}
                        <span v-if="isBound(row)" class="admin-badge"
                          >当前生效</span
                        ></small
                      >
                    </td>
                    <td>
                      {{
                        configKind === "model"
                          ? typeNames[row.payload.model_kind]
                          : configKind === "prompt"
                            ? purposeNames[row.purpose]
                            : row.description || "—"
                      }}
                    </td>
                    <td>v{{ row.revision_no }}</td>
                    <td>
                      <span
                        class="admin-badge"
                        :class="row.enabled ? 'succeeded' : 'stopped'"
                        >{{ row.enabled ? "启用" : "停用" }}</span
                      >
                    </td>
                    <td>
                      <div class="admin-row-actions">
                        <button @click="openEditor(row)">编辑</button
                        ><button @click="toggle(row)">
                          {{ row.enabled ? "停用" : "启用" }}</button
                        ><button
                          v-if="
                            configKind === 'model' &&
                            row.enabled &&
                            !isBound(row)
                          "
                          @click="setBinding(row)"
                        >
                          设为默认
                        </button>
                      </div>
                    </td></template
                  >
                  <template v-else-if="section === 'users'"
                    ><td>
                      <strong>{{ row.login }}</strong
                      ><small>{{ row.display_name }}</small>
                    </td>
                    <td class="admin-id" :title="row.id">{{ row.id }}</td>
                    <td>{{ date(row.created_at) }}</td>
                    <td>{{ date(row.last_login_at) }}</td>
                    <td>
                      <span
                        class="admin-badge"
                        :class="row.enabled ? 'succeeded' : 'stopped'"
                        >{{ row.enabled ? "启用" : "停用" }}</span
                      >
                    </td>
                    <td>
                      <button @click="toggle(row, true)">
                        {{ row.enabled ? "停用" : "启用" }}
                      </button>
                    </td></template
                  >
                  <template v-else-if="section === 'agents'"
                    ><td class="admin-id">{{ row.agent_id }}</td>
                    <td class="admin-id">{{ row.owner_id }}</td>
                    <td>
                      {{ row.conversation_count }} / {{ row.plan_count }} /
                      {{ row.knowledge_base_count }}
                    </td>
                    <td>{{ date(row.last_task_at) }}</td>
                    <td>{{ date(row.created_at) }}</td></template
                  >
                  <template v-else-if="section === 'operation-logs'"
                    ><td>{{ date(row.occurred_at) }}</td>
                    <td class="admin-id" :title="row.actor_id">
                      {{ short(row.actor_id) }}
                    </td>
                    <td>
                      {{ row.action }}<small>{{ row.target_kind }}</small>
                    </td>
                    <td class="admin-id" :title="row.target_id">
                      {{ short(row.target_id) }}
                    </td>
                    <td>
                      <span
                        class="admin-badge"
                        :class="
                          row.result === 'success' ? 'succeeded' : 'failed'
                        "
                        >{{ stateNames[row.result] }}</span
                      >
                    </td>
                    <td>{{ safeErrorReason(row.safe_error_code) }}<small>{{ row.safe_error_code }}</small></td></template
                  >
                  <template v-else
                    ><td class="admin-id" :title="row.task_id">
                      {{ short(row.task_id)
                      }}<small v-if="row.resource_deleted">资源已删除</small>
                    </td>
                    <td class="admin-id" :title="row.owner_id">
                      {{ short(row.owner_id) }}
                    </td>
                    <td>
                      {{
                        section === "file-runs"
                          ? row.extension
                          : typeNames[row.kind]
                      }}<small v-if="row.action">{{ row.action }}</small>
                    </td>
                    <td>
                      <span class="admin-badge" :class="row.status">{{
                        stateNames[row.status]
                      }}</span>
                      <small v-if="row.safe_error_code">{{ safeErrorReason(row.safe_error_code) }}（{{ row.safe_error_code }}）</small>
                    </td>
                    <td>{{ date(row.accepted_at) }}</td>
                    <td>{{ duration(row.duration_ms) }}</td>
                    <td>
                      <button @click="showTask(row)">运行详情</button>
                    </td></template
                  >
                </tr>
                <tr v-if="!busy && !rows.length && !error">
                  <td colspan="7" class="admin-empty">
                    {{
                      q || status || owner || purpose || modelKind
                        ? "没有符合筛选条件的记录"
                        : "暂无记录"
                    }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <footer class="admin-pagination">
            <span class="muted">每页最多20条</span
            ><button :disabled="!cursorStack.length || busy" @click="prevPage">
              上一页</button
            ><button :disabled="!cursor || busy" @click="nextPage">
              下一页
            </button>
          </footer>
        </section>
        <p v-if="ordinaryList" class="muted admin-privacy">
          仅展示运行元数据；不提供用户私有学习正文、资源预览或下载入口。
        </p>
      </main>
    </div>
    <AdminConfigForm
      v-if="editorOpen && configKind"
      :key="editorKey"
      :kind="configKind"
      :config="editing"
      :saving="saving"
      :testing="testing"
      :test-result="testResult"
      :error="editorError"
      :field-errors="editorFields"
      @save="saveConfig"
      @test="testConfig"
      @close="
        editorOpen = false;
        editing = null;
      "
      @reload="reloadEditor"
    />
    <div v-if="confirmation" class="admin-modal-backdrop">
      <section
        class="admin-modal admin-confirm"
        role="dialog"
        aria-modal="true"
        :aria-label="confirmation.title"
      >
        <h2>{{ confirmation.title }}</h2>
        <p>{{ confirmation.text }}</p>
        <footer>
          <button :disabled="saving" @click="confirmation = null">取消</button
          ><button class="primary" :disabled="saving" @click="confirm">
            {{ saving ? "正在处理…" : "确认" }}
          </button>
        </footer>
      </section>
    </div>
    <div
      v-if="selectedTask"
      class="admin-modal-backdrop"
      @click.self="selectedTask = null"
    >
      <section
        class="admin-modal admin-task-detail"
        role="dialog"
        aria-modal="true"
        aria-label="任务运行详情"
      >
        <header>
          <div>
            <p class="eyebrow">运行记录</p>
            <h2>任务运行详情</h2>
          </div>
          <button aria-label="关闭详情" @click="selectedTask = null">×</button>
        </header>
        <dl class="admin-details">
          <dt>任务标识</dt>
          <dd>{{ selectedTask.task_id }}</dd>
          <dt>归属用户</dt>
          <dd>{{ selectedTask.owner_id }}</dd>
          <dt>状态 / 耗时</dt>
          <dd>
            {{ stateNames[selectedTask.status] }} /
            {{ duration(selectedTask.duration_ms) }}
          </dd>
          <dt>受理时间</dt>
          <dd>{{ date(selectedTask.accepted_at) }}</dd>
          <dt>开始 / 结束</dt>
          <dd>
            {{ date(selectedTask.started_at) }} /
            {{ date(selectedTask.finished_at) }}
          </dd>
          <dt>模型配置标识</dt>
          <dd>{{ selectedTask.model_config_id ?? "—" }}</dd>
          <dt>模型 / 工具尝试</dt>
          <dd>
            {{ selectedTask.model_calls }} / {{ selectedTask.tool_calls }}
          </dd>
          <dt>安全错误码</dt>
          <dd>{{ selectedTask.safe_error_code ?? "—" }}</dd>
          <dt>失败原因</dt>
          <dd>{{ safeErrorReason(selectedTask.safe_error_code) }}</dd>
        </dl>
        <h3>实际调用尝试</h3>
        <div class="admin-table-scroll">
          <table>
            <thead>
              <tr>
                <th>类别 / 用途</th>
                <th>配置版本</th>
                <th>结果</th>
                <th>开始时间</th>
                <th>耗时</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="call in taskCalls" :key="call.id">
                <td>
                  {{
                    call.model_kind
                      ? typeNames[call.model_kind]
                      : call.tool_name
                  }}<small>{{ call.purpose }}</small>
                </td>
                <td class="admin-id" :title="call.revision_id">
                  {{ short(call.revision_id) }}
                </td>
                <td>
                  <span class="admin-badge" :class="call.status">{{
                    stateNames[call.status]
                  }}</span
                  ><small v-if="call.safe_error_code">{{ safeErrorReason(call.safe_error_code) }}（{{ call.safe_error_code }}）</small>
                </td>
                <td>{{ date(call.started_at) }}</td>
                <td>{{ duration(call.duration_ms) }}</td>
              </tr>
              <tr v-if="!taskCalls.length">
                <td colspan="5" class="admin-empty">暂无已采集的调用记录</td>
              </tr>
            </tbody>
          </table>
        </div>
        <button v-if="callsCursor" @click="moreCalls">加载更多调用</button>
        <p class="muted">
          {{
            selectedTask.resource_deleted
              ? "原资源已删除，保留历史运行元数据。"
              : "详情仅包含允许展示的运行信息。"
          }}
        </p>
      </section>
    </div>
  </div>
</template>
