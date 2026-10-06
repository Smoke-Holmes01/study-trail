<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  Bot,
  BookOpen,
  Plus,
  Settings,
  PanelLeftClose,
  PanelLeftOpen,
  MessageSquare,
  ArrowUp,
  Square,
  Paperclip,
  FileText,
  MoreHorizontal,
  Search,
  Sun,
  Moon,
  ChevronRight,
  Sparkles,
  X,
  LogOut,
  Compass,
} from "lucide-vue-next";
import { useSession } from "../stores/session";
import { useDrafts, type Draft, type SendBody } from "../stores/drafts";
import {
  api,
  all,
  mutation,
  patch,
  remove,
  operationKey,
  subscribe,
  ApiError,
  type Agent,
  type Conversation,
  type Message,
  type Attachment,
  type KnowledgeBase,
  type StoredFile,
  type Plan,
  type Source,
  type Task,
  type ModelOption,
  type ChatAccepted,
  type Preview,
  type Impact,
  type UploadResult,
  type Exercise,
} from "../lib/api";
import Modal from "../components/Modal.vue";
import Markdown from "../components/Markdown.vue";
const route = useRoute();
const router = useRouter();
const session = useSession();
const isKnowledge = computed(() => route.path === "/knowledge-bases");
const collapsed = ref(false);
const agentExpanded = ref(true);
const agents = ref<Agent[]>([]);
const libraries = ref<KnowledgeBase[]>([]);
const models = ref<ModelOption[]>([]);
const agentId = ref("");
const conversationId = ref("");
const libraryId = ref("");
const conversations = ref<Conversation[]>([]);
const messages = ref<Message[]>([]);
const files = ref<StoredFile[]>([]);
const plans = ref<Plan[]>([]);
const selectedAgent = computed(() =>
  agents.value.find((a) => a.id === agentId.value),
);
const selectedConversation = computed(() =>
  conversations.value.find((c) => c.id === conversationId.value),
);
const selectedLibrary = computed(() =>
  libraries.value.find((k) => k.id === libraryId.value),
);
const task = ref<Task | null>(null);
const connected = ref(true);
let unsubscribe: (() => void) | undefined;
let promptUnsubscribe: (() => void) | undefined;
const taskHistory = reactive(new Map<string, Task>());
const generating = computed(
  () => task.value && ["queued", "running"].includes(task.value.status),
);
const busy = ref(false);
const loading = ref(false);
const toast = ref("");
const toastError = ref(false);
let toastTimer: ReturnType<typeof setTimeout>;
const drafts = useDrafts().forOwner(session.user!.id);
const emptyDraft = reactive<Draft>({ text: "", images: [] });
const draft = computed(() => drafts.get(conversationId.value) ?? emptyDraft);
const target = ref<{
  id: string;
  name: string;
  version: number;
  deleted?: boolean;
} | null>(null);
const modelIncompatible = computed(
  () =>
    (!!draft.value.images.length || !!selectedConversation.value?.has_images) &&
    !models.value.find((m) => m.id === selectedAgent.value?.model_id)
      ?.supports_images,
);
const modelAvailable = computed(() =>
  models.value.some((m) => m.id === selectedAgent.value?.model_id && m.enabled),
);
const canSend = computed(
  () =>
    conversationId.value &&
    !generating.value &&
    !busy.value &&
    modelAvailable.value &&
    !modelIncompatible.value &&
    !target.value?.deleted &&
    (draft.value.text.trim() || draft.value.images.length),
);
const chatScroll = ref<HTMLElement>();
const imageInput = ref<HTMLInputElement>();
const fileInput = ref<HTMLInputElement>();
const composer = ref<HTMLTextAreaElement>();
const filter = ref("");
const visibleFiles = computed(() =>
  files.value.filter((f) =>
    f.original_name.toLowerCase().includes(filter.value.toLowerCase()),
  ),
);
type ModalKind =
  | ""
  | "agent"
  | "settings"
  | "plans"
  | "name"
  | "delete"
  | "preview"
  | "image"
  | "upload";
const modal = ref<ModalKind>("");
const modalError = ref("");
const saving = ref(false);
const modalTitle = computed(
  () =>
    ({
      agent: editingAgent.value ? "编辑智能体" : "创建智能体",
      settings: "个人设置",
      plans: "学习计划",
      name: naming.value.title,
      delete: "确认删除",
      preview: preview.value?.name ?? "资料文字预览",
      image: "图片预览",
      upload: "上传学习资料",
      "": "",
    })[modal.value],
);
const wizardStep = ref(1);
const editingAgent = ref<Agent | null>(null);
const agentForm = reactive({
  name: "",
  description: "",
  system_prompt: "",
  knowledge_base_ids: [] as string[],
});
const promptTask = ref<Task | null>(null);
const promptCandidate = computed(
  () =>
    promptTask.value?.result.prompt_text ||
    promptTask.value?.result.preview_text ||
    "",
);
const promptBusy = computed(
  () =>
    promptTask.value && ["queued", "running"].includes(promptTask.value.status),
);
const selectedPlanId = ref("");
const selectedPlan = computed(() =>
  plans.value.find((p) => p.id === selectedPlanId.value),
);
const conflictTask = ref<Task | null>(null);
const settingsTab = ref<"appearance" | "name" | "password">("appearance");
const displayName = ref("");
const passwordForm = reactive({
  old_password: "",
  new_password: "",
  confirm_password: "",
});
const naming = ref({ title: "", path: "", name: "", kind: "" });
const deletion = ref<{ path: string; name: string; impact: Impact } | null>(
  null,
);
const preview = ref<Preview | null>(null);
const previewPath = ref("");
const previewLoading = ref(false);
const previewImage = ref<{ images: Attachment[]; index: number } | null>(null);
const uploadItems = ref<{ name: string; status: string; message?: string }[]>(
  [],
);
const uploadProgress = ref(false);
function notify(message: string, error = false) {
  toast.value = message;
  toastError.value = error;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.value = ""), 6500);
}
function errorMessage(error: unknown) {
  if (error instanceof ApiError) {
    const fields = error.details.field_errors as
      { field: string; message: string }[] | undefined;
    return fields?.length
      ? error.message + "：" + fields.map((f) => f.message).join("；")
      : error.message;
  }
  return error instanceof Error ? error.message : "操作未完成，请稍后重试";
}
async function guard(action: () => Promise<void>) {
  try {
    await action();
  } catch (e) {
    notify(errorMessage(e), true);
  }
}
let resourceLoadRevision = 0;
async function loadResources() {
  const revision = ++resourceLoadRevision;
  const result = await Promise.all([
    all<Agent>("/agents"),
    all<KnowledgeBase>("/knowledge-bases"),
    api<{ items: ModelOption[] }>("/models"),
  ]);
  if (revision !== resourceLoadRevision) return;
  agents.value = result[0];
  libraries.value = result[1];
  models.value = result[2].items;
}
async function refreshMessages() {
  if (!conversationId.value) {
    messages.value = [];
    return;
  }
  const ident = conversationId.value;
  const result = await all<Message>(`/conversations/${ident}/messages`);
  if (ident === conversationId.value) {
    messages.value = result;
    const pending = result.filter(
      (m) =>
        m.task_id &&
        ["succeeded", "failed", "stopped"].includes(m.response_status ?? "") &&
        !taskHistory.has(m.task_id),
    );
    for (let i = 0; i < pending.length; i += 4)
      await Promise.all(
        pending.slice(i, i + 4).map(async (m) => {
          const snapshot = await api<Task>(`/tasks/${m.task_id}`);
          taskHistory.set(snapshot.id, snapshot);
        }),
      );
    await nextTick();
    if (chatScroll.value)
      chatScroll.value.scrollTop = chatScroll.value.scrollHeight;
  }
}
async function refreshFiles() {
  if (!libraryId.value) {
    files.value = [];
    return;
  }
  const id = libraryId.value;
  const result = await all<StoredFile>(`/knowledge-bases/${id}/files`);
  if (id === libraryId.value) files.value = result;
}
let agentLoadRevision = 0;
let conversationLoadRevision = 0;
async function openAgent(id: string) {
  const revision = ++agentLoadRevision;
  agentExpanded.value = true;
  unsubscribe?.();
  task.value = null;
  target.value = null;
  loading.value = true;
  agentId.value = id;
  try {
    const [list, savedPlans] = await Promise.all([
      all<Conversation>(`/agents/${id}/conversations`),
      all<Plan>(`/agents/${id}/plans`),
    ]);
    if (revision !== agentLoadRevision || id !== agentId.value) return;
    conversations.value = list;
    plans.value = savedPlans;
    if (conversations.value.length)
      await openConversation(conversations.value[0].id);
    else {
      conversationId.value = "";
      messages.value = [];
    }
  } finally {
    if (revision === agentLoadRevision) loading.value = false;
  }
}
async function openConversation(id: string) {
  const revision = ++conversationLoadRevision;
  unsubscribe?.();
  task.value = null;
  target.value = null;
  conversationId.value = id;
  if (!drafts.has(id)) drafts.set(id, { text: "", images: [] });
  const result = await mutation<Conversation>(`/conversations/${id}/access`);
  if (revision !== conversationLoadRevision || id !== conversationId.value)
    return;
  conversations.value = [
    result,
    ...conversations.value.filter((c) => c.id !== id),
  ];
  await refreshMessages();
  if (revision !== conversationLoadRevision || id !== conversationId.value)
    return;
  const running = [...messages.value]
    .reverse()
    .find(
      (m) =>
        m.role === "assistant" &&
        ["queued", "running"].includes(m.response_status ?? ""),
    );
  const pending = drafts.get(id)?.submittedTaskId;
  if (running?.task_id)
    attachTask(await api<Task>(`/tasks/${running.task_id}`), id, true);
  else if (pending) attachTask(await api<Task>(`/tasks/${pending}`), id);
}
async function newConversation() {
  if (!agentId.value) return;
  const result = await mutation<Conversation>(
    `/agents/${agentId.value}/conversations`,
    {},
  );
  conversations.value.unshift(result);
  await openConversation(result.id);
  await nextTick();
  composer.value?.focus();
}
async function selectAgent(id: string) {
  if (id === agentId.value && agentExpanded.value) {
    agentExpanded.value = false;
    return;
  }
  await openAgent(id);
}
async function newAgentConversation(id: string) {
  const result = await mutation<Conversation>(
    `/agents/${id}/conversations`,
    {},
  );
  agentId.value = id;
  agentExpanded.value = true;
  conversations.value = await all<Conversation>(`/agents/${id}/conversations`);
  plans.value = await all<Plan>(`/agents/${id}/plans`);
  await openConversation(result.id);
  await nextTick();
  composer.value?.focus();
}
async function leaveSession() {
  await session.logout();
  modal.value = "";
  await router.push("/login");
}
async function openLibrary(id: string) {
  libraryId.value = id;
  filter.value = "";
  await refreshFiles();
}
function applyTask(snapshot: Task, id: string) {
  if (["succeeded", "failed", "stopped"].includes(snapshot.status))
    taskHistory.set(snapshot.id, snapshot);
  if (task.value?.id === snapshot.id && task.value.revision > snapshot.revision)
    return;
  if (id === conversationId.value) task.value = snapshot;
  const input = snapshot.request_input;
  const d = drafts.get(id);
  if (
    d &&
    snapshot.status === "succeeded" &&
    d.submittedTaskId === snapshot.id
  ) {
    d.text = "";
    d.images = [];
    delete d.submittedTaskId;
    const change = snapshot.result.plan_mutation;
    if (
      id === conversationId.value &&
      change?.committed &&
      change.kind === "updated" &&
      change.plan_id === target.value?.id
    )
      target.value = null;
  }
  if (id === conversationId.value && input?.target_plan?.status === "deleted")
    target.value = {
      id: input.target_plan.id,
      name: input.target_plan.name_snapshot,
      version: input.target_plan.expected_plan_version,
      deleted: true,
    };
  if (id === conversationId.value && snapshot.assistant_message_id) {
    const assistant = messages.value.find(
      (m) => m.id === snapshot.assistant_message_id,
    );
    if (assistant) {
      assistant.content_text = snapshot.result.preview_text;
      assistant.response_status = snapshot.status;
    }
  }
}
function attachTask(snapshot: Task, id: string, restore = false) {
  if (id !== conversationId.value) {
    applyTask(snapshot, id);
    return;
  }
  unsubscribe?.();
  connected.value = true;
  if (restore && snapshot.request_input) {
    const d = drafts.get(id)!;
    d.text = snapshot.request_input.content_text;
    d.submittedTaskId = snapshot.id;
    const user = messages.value.find((m) => m.id === snapshot.user_message_id);
    d.images = (user?.attachments ?? []).map((attachment) => ({ attachment }));
    const p = snapshot.request_input.target_plan;
    if (p)
      target.value = {
        id: p.id,
        name: p.name_snapshot,
        version: p.expected_plan_version,
        deleted: p.status === "deleted",
      };
  }
  applyTask(snapshot, id);
  if (["queued", "running"].includes(snapshot.status)) {
    unsubscribe = subscribe(
      snapshot.id,
      (value) => {
        applyTask(value, id);
        if (
          ["succeeded", "failed", "stopped"].includes(value.status) &&
          id === conversationId.value
        )
          guard(async () => {
            await refreshMessages();
            plans.value = await all<Plan>(`/agents/${agentId.value}/plans`);
            if (value.error) notify(value.error.message, true);
          });
        nextTick(() => {
          if (chatScroll.value)
            chatScroll.value.scrollTop = chatScroll.value.scrollHeight;
        });
      },
      (value) => (connected.value = value),
    );
  } else guard(refreshMessages);
}
async function uploadOneImage(file: File, id = conversationId.value) {
  const form = new FormData();
  form.append("file", file);
  return await api<Attachment>(`/conversations/${id}/images`, {
    method: "POST",
    body: form,
    key: operationKey(),
  });
}
async function pickImages(event: Event) {
  const id = conversationId.value;
  const receivingDraft = drafts.get(id)!;
  const selected = Array.from((event.target as HTMLInputElement).files ?? []);
  (event.target as HTMLInputElement).value = "";
  if (receivingDraft.images.length + selected.length > 6) {
    notify("每条消息最多选择 6 张图片。", true);
    return;
  }
  busy.value = true;
  for (const file of selected) {
    if (file.size > 10 * 1024 ** 2 || file.size === 0) {
      notify(`${file.name}：单张图片应大于 0 且不超过 10 MiB。`, true);
      continue;
    }
    try {
      const attachment = await uploadOneImage(file, id);
      receivingDraft.images.push({ attachment, file });
    } catch (e) {
      notify(`${file.name}：${errorMessage(e)}`, true);
    }
  }
  busy.value = false;
}
async function removeImage(index: number) {
  const item = draft.value.images[index];
  if (item.attachment.state === "staged") {
    try {
      await remove(`/attachments/${item.attachment.id}`);
    } catch (e) {
      if (!(
        e instanceof ApiError &&
        ([404, 410].includes(e.status) || e.code === "ATTACHMENT_BOUND")
      ))
        throw e;
    }
  }
  draft.value.images.splice(index, 1);
}
async function send() {
  if (!canSend.value) return;
  busy.value = true;
  const id = conversationId.value;
  const sendingDraft = drafts.get(id)!;
  const sendingTarget = target.value ? { ...target.value } : null;
  try {
    const currentBody = (): SendBody => ({
      content_text: sendingDraft.text,
      attachment_ids: sendingDraft.images.map((x) => x.attachment.id),
      target_plan_id: sendingTarget?.id ?? null,
      expected_plan_version: sendingTarget?.version ?? null,
    });
    const pending = sendingDraft.pendingSend;
    if (pending && pending.signature !== JSON.stringify(currentBody())) {
      const original = await mutation<ChatAccepted>(
        `/conversations/${id}/messages`,
        pending.body,
        pending.key,
      );
      delete sendingDraft.pendingSend;
      const bound = new Map(
        original.user_message.attachments.map((a) => [a.id, a]),
      );
      for (const image of sendingDraft.images)
        image.attachment = bound.get(image.attachment.id) ?? image.attachment;
      delete sendingDraft.submittedTaskId;
      const actual = await api<Task>(`/tasks/${original.task.id}`);
      if (["queued", "running"].includes(actual.status)) {
        await refreshMessages();
        attachTask(actual, id);
        notify("原请求已受理，当前编辑的草稿已保留。请等待或停止原任务。");
        return;
      }
    }
    for (const item of sendingDraft.images)
      if (item.attachment.state === "bound") {
        const blob =
          item.file ??
          (await (await fetch(item.attachment.content_url)).blob());
        const file =
          item.file ??
          new File([blob], item.attachment.original_name, {
            type: item.attachment.media_type,
          });
        item.attachment = await uploadOneImage(file, id);
        item.file = file;
      }
    const body = currentBody();
    const signature = JSON.stringify(body);
    const key =
      sendingDraft.pendingSend?.signature === signature
        ? sendingDraft.pendingSend.key
        : operationKey();
    sendingDraft.pendingSend = { key, body, signature };
    const result = await mutation<ChatAccepted>(
      `/conversations/${id}/messages`,
      body,
      key,
    );
    delete sendingDraft.pendingSend;
    sendingDraft.submittedTaskId = result.task.id;
    const bound = new Map(
      result.user_message.attachments.map((a) => [a.id, a]),
    );
    for (const image of sendingDraft.images)
      image.attachment = bound.get(image.attachment.id) ?? image.attachment;
    attachTask(result.task, id);
    if (id === conversationId.value) await refreshMessages();
  } catch (e) {
    if (e instanceof ApiError && [400, 404, 409, 410, 422].includes(e.status))
      delete sendingDraft.pendingSend;
    notify(errorMessage(e), true);
  } finally {
    busy.value = false;
  }
}
function keydown(event: KeyboardEvent) {
  if (
    event.key === "Enter" &&
    !event.shiftKey &&
    !event.isComposing &&
    event.keyCode !== 229
  ) {
    event.preventDefault();
    send();
  }
}
async function stop() {
  if (!task.value) return;
  const ident = task.value.id;
  const id = conversationId.value;
  try {
    applyTask(await mutation<Task>(`/tasks/${ident}/stop`), id);
  } catch (e) {
    notify(errorMessage(e), true);
    guard(async () => applyTask(await api<Task>(`/tasks/${ident}`), id));
  }
}
async function retryMessage(message: Message, confirmedPlan?: Plan) {
  const id = message.conversation_id;
  if (!message.task_id || !message.user_message_id) return;
  const old = await api<Task>(`/tasks/${message.task_id}`);
  if (old.error?.code === "PLAN_VERSION_CONFLICT" && !confirmedPlan) {
    const targetId = old.request_input?.target_plan?.id;
    if (targetId) {
      const latest = await api<Plan>(`/plans/${targetId}`);
      plans.value = [latest, ...plans.value.filter((p) => p.id !== targetId)];
      selectedPlanId.value = targetId;
      conflictTask.value = old;
      modal.value = "plans";
    }
    return;
  }
  const result = await mutation<ChatAccepted>(
    `/messages/${message.user_message_id}/retry`,
    {
      previous_task_id: old.id,
      ...(confirmedPlan
        ? { expected_plan_version: confirmedPlan.content_version }
        : {}),
    },
    operationKey(),
  );
  const d = drafts.get(id)!;
  d.submittedTaskId = result.task.id;
  attachTask(result.task, id, true);
  if (id === conversationId.value) await refreshMessages();
  modal.value = "";
  conflictTask.value = null;
}
async function confirmConflictRetry() {
  if (!conflictTask.value || !selectedPlan.value) return;
  const message = messages.value.find(
    (m) => m.task_id === conflictTask.value!.id,
  );
  if (message) await retryMessage(message, selectedPlan.value);
}
async function requestAnswer(exercise: Exercise) {
  const id = exercise.conversation_id;
  if (generating.value) return;
  const result = await mutation<ChatAccepted>(
    `/exercises/${exercise.id}/answer`,
    {},
    operationKey(),
  );
  drafts.get(id)!.submittedTaskId = result.task.id;
  attachTask(result.task, id, true);
  if (id === conversationId.value) await refreshMessages();
}
async function startFeedback(exercise: Exercise, index: number) {
  if (generating.value) return;
  draft.value.text = `针对第 ${index + 1} 题：${exercise.question_text}\n我的作答：`;
  await nextTick();
  composer.value?.focus();
}
function openAgentForm(agent?: Agent) {
  editingAgent.value = agent ?? null;
  wizardStep.value = 1;
  modalError.value = "";
  promptTask.value = null;
  Object.assign(agentForm, {
    name: agent?.name ?? "",
    description: agent?.description ?? "",
    system_prompt: agent?.system_prompt ?? "",
    knowledge_base_ids: [...(agent?.knowledge_base_ids ?? [])],
  });
  modal.value = "agent";
}
function nextStep() {
  modalError.value = "";
  if (wizardStep.value === 1 && !agentForm.name.trim()) {
    modalError.value = "请填写智能体名称。";
    return;
  }
  if (wizardStep.value === 2 && !agentForm.system_prompt.trim()) {
    modalError.value = "请填写或采用系统提示词。";
    return;
  }
  wizardStep.value++;
}
async function saveAgent() {
  saving.value = true;
  modalError.value = "";
  try {
    const result = editingAgent.value
      ? await patch<Agent>(`/agents/${editingAgent.value.id}`, agentForm)
      : await mutation<Agent>("/agents", agentForm);
    modal.value = "";
    await loadResources();
    await router.push("/agents");
    await openAgent(result.id);
    notify("智能体已保存，新设置用于后续请求。");
  } catch (e) {
    modalError.value = errorMessage(e);
  } finally {
    saving.value = false;
  }
}
async function generatePrompt(mode: "generate" | "polish") {
  modalError.value = "";
  try {
    const result = await mutation<{ task: Task }>(
      "/prompt-tasks",
      {
        mode,
        name: agentForm.name,
        description: agentForm.description,
        input_prompt: mode === "polish" ? agentForm.system_prompt : "",
      },
      operationKey(),
    );
    followPrompt(result.task);
  } catch (e) {
    modalError.value = errorMessage(e);
  }
}
function followPrompt(value: Task) {
  promptUnsubscribe?.();
  promptTask.value = value;
  promptUnsubscribe = subscribe(
    value.id,
    (t) => (promptTask.value = t),
    () => {},
  );
}
async function retryPrompt() {
  if (promptTask.value)
    followPrompt(
      (
        await mutation<{ task: Task }>(
          `/tasks/${promptTask.value.id}/retry`,
          {},
          operationKey(),
        )
      ).task,
    );
}
async function changeModel(event: Event) {
  const value = (event.target as HTMLSelectElement).value;
  try {
    const result = await patch<Agent>(`/agents/${agentId.value}`, {
      model_id: value,
    });
    agents.value = agents.value.map((a) => (a.id === result.id ? result : a));
    notify("模型已更新，将用于下一次请求。");
  } catch (e) {
    notify(errorMessage(e), true);
  }
}
function startName(title: string, path: string, name: string, kind: string) {
  naming.value = { title, path, name, kind };
  modalError.value = "";
  modal.value = "name";
}
async function saveName() {
  saving.value = true;
  modalError.value = "";
  try {
    if (!naming.value.path) {
      const result = await mutation<KnowledgeBase>("/knowledge-bases", {
        name: naming.value.name,
      });
      libraryId.value = result.id;
    } else await patch(naming.value.path, { name: naming.value.name });
    modal.value = "";
    await loadResources();
    if (isKnowledge.value) await refreshFiles();
    else if (agentId.value) {
      conversations.value = await all<Conversation>(
        `/agents/${agentId.value}/conversations`,
      );
      plans.value = await all<Plan>(`/agents/${agentId.value}/plans`);
    }
  } catch (e) {
    modalError.value = errorMessage(e);
  } finally {
    saving.value = false;
  }
}
async function askDelete(path: string, name: string) {
  deletion.value = {
    path,
    name,
    impact: await api<Impact>(path + "/delete-impact"),
  };
  modalError.value = "";
  modal.value = "delete";
}
async function confirmDelete() {
  if (!deletion.value) return;
  saving.value = true;
  modalError.value = "";
  try {
    const path = deletion.value.path;
    await remove(path);
    modal.value = "";
    await loadResources();
    if (path === `/agents/${agentId.value}`) {
      agentId.value = "";
      conversationId.value = "";
      messages.value = [];
      plans.value = [];
      task.value = null;
      unsubscribe?.();
      if (agents.value.length) await openAgent(agents.value[0].id);
    } else if (path === `/conversations/${conversationId.value}`) {
      drafts.delete(conversationId.value);
      await openAgent(agentId.value);
    } else if (path === `/knowledge-bases/${libraryId.value}`) {
      libraryId.value = libraries.value[0]?.id ?? "";
      await refreshFiles();
    } else {
      if (agentId.value)
        plans.value = await all<Plan>(`/agents/${agentId.value}/plans`);
      if (path === `/plans/${target.value?.id}` && target.value)
        target.value.deleted = true;
      await refreshFiles();
      if (conversationId.value) await refreshMessages();
    }
    notify("资源已删除。");
  } catch (e) {
    modalError.value = errorMessage(e);
  } finally {
    saving.value = false;
  }
}
async function openPlans() {
  if (!agentId.value) return;
  plans.value = await all<Plan>(`/agents/${agentId.value}/plans`);
  selectedPlanId.value = plans.value[0]?.id ?? "";
  conflictTask.value = null;
  modal.value = "plans";
}
async function modifyPlan() {
  const p = selectedPlan.value;
  if (!p) return;
  if (!conversationId.value) await newConversation();
  target.value = { id: p.id, name: p.name, version: p.content_version };
  modal.value = "";
  await nextTick();
  composer.value?.focus();
}
async function showPreview(path: string, append = false) {
  previewLoading.value = true;
  previewPath.value = path;
  modal.value = "preview";
  try {
    const result = await api<Preview>(
      path +
        (append && preview.value?.next_cursor
          ? "?cursor=" + encodeURIComponent(preview.value.next_cursor)
          : ""),
    );
    preview.value =
      append && preview.value
        ? { ...result, blocks: [...preview.value.blocks, ...result.blocks] }
        : result;
  } catch (e) {
    modalError.value = errorMessage(e);
  } finally {
    previewLoading.value = false;
  }
}
async function openSource(source: Source) {
  if (source.status === "deleted") {
    notify("来源已删除，历史文字保留。");
    return;
  }
  if (source.kind === "web" && source.url && /^https?:\/\//i.test(source.url)) {
    window.open(source.url, "_blank", "noopener,noreferrer");
    return;
  }
  await showPreview(`/sources/${source.id}/preview`);
}
function sourceFromMessage(id: string, message: Message) {
  const source = message.sources.find((s) => s.id === id);
  if (source && message.response_status === "succeeded")
    guard(() => openSource(source));
}
function showImage(images: Attachment[], index: number) {
  previewImage.value = { images, index };
  modal.value = "image";
}
async function uploadDocuments(event: Event) {
  const chosen = Array.from((event.target as HTMLInputElement).files ?? []);
  (event.target as HTMLInputElement).value = "";
  if (!chosen.length) return;
  if (chosen.length > 10) {
    notify("每批最多上传 10 个文件。", true);
    return;
  }
  modal.value = "upload";
  uploadProgress.value = true;
  uploadItems.value = [];
  const form = new FormData();
  for (const f of chosen) {
    if (
      !/\.(pdf|docx|txt)$/i.test(f.name) ||
      f.size === 0 ||
      f.size > 50 * 1024 ** 2
    )
      uploadItems.value.push({
        name: f.name,
        status: "已跳过",
        message: "仅支持 PDF/DOCX/TXT，单个应大于 0 且不超过 50 MiB。",
      });
    else {
      form.append("files", f);
      uploadItems.value.push({ name: f.name, status: "上传中" });
    }
  }
  try {
    if (form.has("files")) {
      const result = await api<UploadResult>(
        `/knowledge-bases/${libraryId.value}/files`,
        { method: "POST", body: form, key: operationKey() },
      );
      for (const item of result.items) {
        const name = item.file?.original_name ?? item.original_name ?? "";
        const row = uploadItems.value.find(
          (x) => x.name === name && x.status === "上传中",
        );
        if (row) {
          row.status = item.error ? "未接受" : "处理中";
          row.message = item.error?.message;
        }
      }
      await refreshFiles();
      await loadResources();
    }
  } catch (e) {
    modalError.value = errorMessage(e);
    for (const row of uploadItems.value)
      if (row.status === "上传中") row.status = "状态待确认";
  } finally {
    uploadProgress.value = false;
  }
}
async function reprocess(file: StoredFile) {
  await mutation(`/files/${file.id}/reprocess`, {}, operationKey());
  await refreshFiles();
}
function openSettings() {
  displayName.value = session.user?.display_name ?? "";
  Object.assign(passwordForm, {
    old_password: "",
    new_password: "",
    confirm_password: "",
  });
  modalError.value = "";
  settingsTab.value = "appearance";
  modal.value = "settings";
}
async function saveSettings() {
  saving.value = true;
  modalError.value = "";
  try {
    if (settingsTab.value === "name") {
      await session.update({ display_name: displayName.value });
      notify("显示名称已更新。");
      modal.value = "";
    }
    if (settingsTab.value === "password") {
      await mutation("/auth/password", passwordForm);
      session.clear();
      modal.value = "";
      Object.assign(passwordForm, {
        old_password: "",
        new_password: "",
        confirm_password: "",
      });
      await router.push("/login");
    }
  } catch (e) {
    modalError.value = errorMessage(e);
  } finally {
    saving.value = false;
  }
}
function closeModal() {
  modal.value = "";
  modalError.value = "";
  conflictTask.value = null;
  Object.assign(passwordForm, {
    old_password: "",
    new_password: "",
    confirm_password: "",
  });
}
const bytes = (value: number) =>
  value >= 1024 ** 2
    ? `${(value / 1024 ** 2).toFixed(1)} MB`
    : `${(value / 1024).toFixed(1)} KB`;
const impactName = (key: string) =>
  (
    ({
      conversations: "对话",
      images: "图片",
      plans: "计划",
      files: "文件",
      agents: "关联智能体",
    }) as Record<string, string>
  )[key] ?? key;
const statusName = (status: string) =>
  ({
    processing: "处理中",
    ready: "可用",
    failed: "处理失败",
    queued: "排队中",
    running: "生成中",
    stopped: "已停止",
    succeeded: "已完成",
  })[status as "ready"] ?? status;
const planMutation = (message: Message) =>
  message.task_id
    ? taskHistory.get(message.task_id)?.result.plan_mutation
    : null;
const candidateSources = (message: Message) =>
  message.response_status === "succeeded"
    ? []
    : message.task_id === task.value?.id
      ? task.value.result.sources
      : message.task_id
        ? (taskHistory.get(message.task_id)?.result.sources ?? [])
        : [];
function locator(source: Source) {
  const l = source.locator;
  if (!l) return "";
  if (l.page_start)
    return `第 ${l.page_start}${l.page_end !== l.page_start ? "–" + l.page_end : ""} 页`;
  if (l.line_start) return `第 ${l.line_start} 行`;
  if (l.block_start) return `片段 ${l.block_start}`;
  return "";
}
let filePoll: ReturnType<typeof setInterval>;
onMounted(() =>
  guard(async () => {
    await loadResources();
    if (agents.value.length) await openAgent(agents.value[0].id);
    if (libraries.value.length) await openLibrary(libraries.value[0].id);
    filePoll = setInterval(() => {
      if (
        isKnowledge.value &&
        files.value.some((f) => f.status === "processing")
      )
        guard(async () => {
          await refreshFiles();
          await loadResources();
        });
    }, 3000);
  }),
);
watch(isKnowledge, () => {
  target.value = null;
});
onBeforeUnmount(() => {
  unsubscribe?.();
  promptUnsubscribe?.();
  clearInterval(filePoll);
  clearTimeout(toastTimer);
});
</script>

<template>
  <div class="workspace" :class="{ collapsed }">
    <nav class="rail" aria-label="主导航">
      <div class="logo-icon" title="学迹 Study Trail">
        <Compass :size="28" />
      </div>
      <RouterLink
        to="/agents"
        title="智能体"
        aria-label="智能体"
        :class="{ selected: !isKnowledge }"
        ><Bot
      /></RouterLink>
      <RouterLink
        to="/knowledge-bases"
        title="知识库"
        aria-label="知识库"
        :class="{ selected: isKnowledge }"
        ><BookOpen
      /></RouterLink>
      <div class="rail-spacer" />
      <button
        class="icon-button"
        title="个人设置"
        aria-label="个人设置"
        @click="openSettings"
      >
        <Settings />
      </button>
      <div class="avatar" :title="session.user?.display_name">
        {{ session.user?.display_name.slice(0, 1) }}
      </div>
    </nav>
    <aside v-if="!collapsed" class="resources">
      <div class="resource-heading">
        <div>
          <span class="eyebrow">STUDY TRAIL</span>
          <h1>{{ isKnowledge ? "知识库" : "智能体" }}</h1>
        </div>
        <button
          class="icon-button"
          title="折叠资源栏"
          aria-label="折叠资源栏"
          @click="collapsed = true"
        >
          <PanelLeftClose :size="19" />
        </button>
      </div>
      <button
        class="resource-create"
        @click="
          isKnowledge
            ? startName('新建知识库', '', '', 'library')
            : openAgentForm()
        "
      >
        <Plus :size="18" />{{ isKnowledge ? "新建知识库" : "创建智能体" }}
      </button>
      <div class="resource-list" v-if="!isKnowledge">
        <div v-for="agent in agents" :key="agent.id" class="agent-group">
          <div class="resource-item" :class="{ active: agentId === agent.id }">
            <button
              class="resource-main"
              :title="agent.name"
              @click="guard(() => selectAgent(agent.id))"
            >
              <span class="resource-icon"><Bot :size="18" /></span
              ><span>{{ agent.name }}</span>
            </button>
            <button
              class="agent-add icon-button"
              :aria-label="'为 ' + agent.name + ' 新建对话'"
              title="新建对话"
              @click="guard(() => newAgentConversation(agent.id))"
            >
              <Plus :size="15" />
            </button>
            <details class="object-menu">
              <summary aria-label="智能体操作">
                <MoreHorizontal :size="16" />
              </summary>
              <div class="menu">
                <button @click="openAgentForm(agent)">编辑智能体</button
                ><button
                  @click="
                    guard(() => askDelete('/agents/' + agent.id, agent.name))
                  "
                >
                  删除智能体
                </button>
              </div>
            </details>
          </div>
          <div
            v-if="agentId === agent.id && agentExpanded"
            class="conversation-list"
          >
            <button class="new-conversation" @click="guard(newConversation)">
              <Plus :size="15" />新建对话
            </button>
            <div
              v-for="conv in conversations"
              :key="conv.id"
              class="conversation-item"
              :class="{ active: conversationId === conv.id }"
            >
              <button
                :title="conv.name"
                @click="guard(() => openConversation(conv.id))"
              >
                <MessageSquare :size="14" /><span>{{ conv.name }}</span>
              </button>
              <details class="object-menu">
                <summary aria-label="对话操作">
                  <MoreHorizontal :size="15" />
                </summary>
                <div class="menu">
                  <button
                    @click="
                      startName(
                        '重命名对话',
                        '/conversations/' + conv.id,
                        conv.name,
                        'conversation',
                      )
                    "
                  >
                    重命名</button
                  ><button
                    @click="
                      guard(() =>
                        askDelete('/conversations/' + conv.id, conv.name),
                      )
                    "
                  >
                    删除对话
                  </button>
                </div>
              </details>
            </div>
          </div>
        </div>
        <p v-if="!agents.length" class="sidebar-empty">
          创建你的第一个学习伙伴。
        </p>
      </div>
      <div class="resource-list" v-else>
        <div
          v-for="library in libraries"
          :key="library.id"
          class="resource-item"
          :class="{ active: libraryId === library.id }"
        >
          <button
            class="resource-main"
            @click="guard(() => openLibrary(library.id))"
          >
            <span class="resource-icon"><BookOpen :size="18" /></span
            ><span
              >{{ library.name
              }}<small
                >{{ library.file_count }} 个文件 ·
                {{ library.ready_file_count }} 个可用</small
              ></span
            >
          </button>
          <details class="object-menu">
            <summary aria-label="知识库操作">
              <MoreHorizontal :size="16" />
            </summary>
            <div class="menu">
              <button
                @click="
                  startName(
                    '重命名知识库',
                    '/knowledge-bases/' + library.id,
                    library.name,
                    'library',
                  )
                "
              >
                重命名</button
              ><button
                @click="
                  guard(() =>
                    askDelete('/knowledge-bases/' + library.id, library.name),
                  )
                "
              >
                删除知识库
              </button>
            </div>
          </details>
        </div>
        <p v-if="!libraries.length" class="sidebar-empty">
          把教材与笔记整理在这里。
        </p>
      </div>
      <div class="resource-foot">
        <span class="status-dot" />个人学习空间<small
          >仅你可以访问自己的资料</small
        >
      </div>
    </aside>
    <main class="work-area">
      <header class="work-header">
        <div class="header-title">
          <button
            v-if="collapsed"
            class="icon-button"
            title="展开资源栏"
            aria-label="展开资源栏"
            @click="collapsed = false"
          >
            <PanelLeftOpen :size="20" /></button
          ><span class="header-icon"
            ><component :is="isKnowledge ? BookOpen : Bot" :size="21"
          /></span>
          <div>
            <h2>
              {{
                isKnowledge
                  ? (selectedLibrary?.name ?? "我的知识库")
                  : (selectedAgent?.name ?? "我的学习伙伴")
              }}
            </h2>
            <small>{{
              isKnowledge
                ? "让资料成为理解的依据"
                : selectedAgent?.description || "与问题对话，向理解前进"
            }}</small>
          </div>
        </div>
        <div v-if="!isKnowledge && selectedAgent" class="header-actions">
          <button @click="guard(openPlans)">
            <BookOpen :size="17" />学习计划</button
          ><button
            class="icon-button"
            aria-label="编辑当前智能体"
            title="编辑智能体"
            @click="openAgentForm(selectedAgent)"
          >
            <Settings :size="18" />
          </button>
        </div>
        <button
          v-else-if="isKnowledge && selectedLibrary"
          class="primary"
          @click="fileInput?.click()"
        >
          <Plus :size="17" />上传文件
        </button>
      </header>
      <template v-if="!isKnowledge"
        ><section v-if="!selectedAgent" class="empty-screen">
          <div class="empty-symbol"><Bot :size="43" /></div>
          <span class="eyebrow">从一个学习伙伴开始</span>
          <h2>让学习，有一个清晰的起点。</h2>
          <p>
            为你的目标创建智能体，定义辅导方式，<br />再把自己的教材与笔记关联进来。
          </p>
          <button class="primary" @click="openAgentForm()">
            <Plus :size="18" />创建第一个智能体
          </button>
        </section>
        <template v-else
          ><div ref="chatScroll" class="chat-scroll">
            <div v-if="loading" class="empty-screen">
              <p>正在打开学习空间…</p>
            </div>
            <div v-else-if="!messages.length" class="chat-welcome">
              <span class="welcome-logo"><Sparkles :size="32" /></span>
              <h2>今天，想从哪里开始？</h2>
              <p>描述你的问题，或者告诉我想达成的学习目标。</p>
              <div class="suggestions">
                <button
                  @click="
                    guard(async () => {
                      if (!conversationId) await newConversation();
                      draft.text = '请帮我理解一个知识点，并用具体例子解释。';
                    })
                  "
                >
                  理解一个知识点<ChevronRight :size="16" /></button
                ><button
                  @click="
                    guard(async () => {
                      if (!conversationId) await newConversation();
                      draft.text =
                        '请帮我制定一份学习计划，我会补充目标和可用时间。';
                    })
                  "
                >
                  规划下一步学习<ChevronRight :size="16" /></button
                ><button
                  @click="
                    guard(async () => {
                      if (!conversationId) await newConversation();
                      draft.text =
                        '请根据关联资料给我几道练习题，先不要展示答案。';
                    })
                  "
                >
                  通过练习巩固<ChevronRight :size="16" />
                </button>
              </div>
              <button
                v-if="!conversationId"
                class="primary"
                @click="guard(newConversation)"
              >
                开始新对话
              </button>
            </div>
            <article
              v-for="message in messages"
              :key="message.id"
              class="message"
              :class="message.role"
            >
              <div class="message-identity">
                <span
                  :class="
                    message.role === 'user' ? 'user-avatar' : 'assistant-avatar'
                  "
                  ><component
                    :is="message.role === 'assistant' ? Compass : MessageSquare"
                    :size="17" /></span
                ><b>{{
                  message.role === "user" ? session.user?.display_name : "学迹"
                }}</b
                ><span
                  v-if="
                    message.role === 'assistant' &&
                    message.response_status !== 'succeeded'
                  "
                  class="badge"
                  :class="message.response_status"
                  >{{ statusName(message.response_status ?? "")
                  }}<template
                    v-if="
                      ['failed', 'stopped'].includes(
                        message.response_status ?? '',
                      )
                    "
                  >
                    · 未完成</template
                  ></span
                >
              </div>
              <div class="message-content">
                <div v-if="message.attachments.length" class="message-images">
                  <button
                    v-for="(image, i) in message.attachments"
                    :key="image.id"
                    :aria-label="'放大图片 ' + image.original_name"
                    @click="showImage(message.attachments, i)"
                  >
                    <img :src="image.content_url" :alt="image.original_name" />
                  </button>
                </div>
                <Markdown
                  :text="message.content_text"
                  :interactive="
                    message.role === 'user' ||
                    message.response_status === 'succeeded'
                  "
                  @source="(id) => sourceFromMessage(id, message)"
                />
                <div
                  v-if="
                    !message.content_text &&
                    ['queued', 'running'].includes(
                      message.response_status ?? '',
                    )
                  "
                  class="thinking"
                >
                  <i /><i /><i /><span>{{
                    task?.progress.label ?? "等待处理"
                  }}</span>
                </div>
                <div v-if="message.sources.length" class="sources">
                  <button
                    v-for="source in message.sources"
                    :key="source.id"
                    :class="{ unavailable: source.status === 'deleted' }"
                    @click="guard(() => openSource(source))"
                  >
                    <FileText :size="13" />{{ source.title
                    }}<span>{{
                      source.status === "deleted"
                        ? "来源已删除"
                        : locator(source)
                    }}</span>
                  </button>
                </div>
                <div v-if="candidateSources(message).length" class="sources">
                  <button
                    v-for="source in candidateSources(message)"
                    :key="source.id"
                    disabled
                    title="本次结果尚未正式保存，候选引用不可打开"
                  >
                    <FileText :size="13" />{{ source.title }} ·
                    {{
                      source.status === "deleted"
                        ? "来源已删除"
                        : "候选依据 · 未保存"
                    }}
                  </button>
                </div>
                <div
                  v-if="
                    message.response_status === 'succeeded' &&
                    message.exercises.length
                  "
                  class="exercise-actions"
                >
                  <template
                    v-for="(exercise, i) in message.exercises"
                    :key="exercise.id"
                    ><button
                      :disabled="!!generating"
                      @click="guard(() => requestAnswer(exercise))"
                    >
                      查看第 {{ i + 1 }} 题答案</button
                    ><button
                      :disabled="!!generating"
                      @click="guard(() => startFeedback(exercise, i))"
                    >
                      针对第 {{ i + 1 }} 题作答
                    </button></template
                  >
                </div>
                <div
                  v-if="
                    message.response_status === 'succeeded' &&
                    planMutation(message)?.committed
                  "
                  class="saved-plan"
                >
                  <BookOpen :size="18" /><span
                    >{{
                      plans.some((p) => p.id === planMutation(message)?.plan_id)
                        ? "学习计划已保存"
                        : "计划已删除 · 历史记录保留"
                    }}
                    · v{{ planMutation(message)?.content_version }}</span
                  ><button
                    v-if="
                      plans.some((p) => p.id === planMutation(message)?.plan_id)
                    "
                    @click="
                      guard(async () => {
                        await openPlans();
                        selectedPlanId = planMutation(message)!.plan_id;
                      })
                    "
                  >
                    查看计划 →
                  </button>
                </div>
                <div
                  v-if="
                    ['failed', 'stopped'].includes(
                      message.response_status ?? '',
                    )
                  "
                  class="failure-actions"
                >
                  <span>{{
                    task?.assistant_message_id === message.id
                      ? task.error?.message
                      : "本次回复未完整完成，正式计划保持原版本。"
                  }}</span
                  ><button
                    :disabled="!!generating"
                    @click="guard(() => retryMessage(message))"
                  >
                    重试此请求</button
                  ><button
                    v-if="message.user_message_id"
                    @click="
                      guard(async () => {
                        const t = await api<Task>('/tasks/' + message.task_id);
                        attachTask(t, conversationId, true);
                        await nextTick();
                        composer?.focus();
                      })
                    "
                  >
                    编辑原输入
                  </button>
                </div>
              </div>
            </article>
          </div>
          <div class="composer-region">
            <div v-if="!connected && generating" class="warning-box compact">
              连接中断，后台状态待确认；正在重新连接。
            </div>
            <div v-if="modelIncompatible" class="warning-box compact">
              当前模型不支持此对话中的图片，请手动选择图片模型。历史图片不会被忽略。
            </div>
            <div v-if="!modelAvailable" class="warning-box compact">
              当前模型不可用，请手动选择可用模型。已输入内容会保留。
            </div>
            <div
              v-if="target"
              class="target-tag"
              :class="{ invalid: target.deleted }"
            >
              <BookOpen :size="15" /><span
                >{{ target.deleted ? "目标已删除：" : "修改目标："
                }}{{ target.name }} · v{{ target.version }}</span
              ><button
                aria-label="移除目标计划"
                :disabled="!!generating"
                @click="target = null"
              >
                <X :size="14" />
              </button>
            </div>
            <div class="composer" :class="{ disabled: generating }">
              <div v-if="draft.images.length" class="draft-images">
                <div
                  v-for="(item, i) in draft.images"
                  :key="item.attachment.id"
                >
                  <button
                    :aria-label="'预览 ' + item.attachment.original_name"
                    @click="
                      showImage(
                        draft.images.map((x) => x.attachment),
                        i,
                      )
                    "
                  >
                    <img
                      :src="item.attachment.content_url"
                      :alt="item.attachment.original_name"
                    /></button
                  ><button
                    class="remove-image"
                    :disabled="!!generating || busy"
                    :aria-label="'移除 ' + item.attachment.original_name"
                    @click="guard(() => removeImage(i))"
                  >
                    ×
                  </button>
                </div>
              </div>
              <textarea
                ref="composer"
                v-model="draft.text"
                aria-label="输入学习问题"
                :readonly="!!generating || busy"
                :disabled="!conversationId"
                placeholder="输入问题、学习目标，或上传图片…"
                @keydown="keydown"
              />
              <div class="composer-tools">
                <button
                  class="icon-button"
                  aria-label="添加图片"
                  title="添加图片 · 单张≤10 MiB，最多6张"
                  :disabled="!!generating || busy || !conversationId"
                  @click="imageInput?.click()"
                >
                  <Paperclip :size="20" />
                </button>
                <select
                  aria-label="选择生成模型"
                  :value="selectedAgent.model_id"
                  @change="changeModel"
                >
                  <option
                    v-if="!modelAvailable"
                    :value="selectedAgent.model_id"
                    disabled
                  >
                    当前模型不可用
                  </option>
                  <option
                    v-for="model in models"
                    :key="model.id"
                    :value="model.id"
                  >
                    {{ model.display_name
                    }}{{ model.supports_images ? " · 图片" : " · 文字" }}
                  </option>
                </select>
                <small>{{
                  generating
                    ? task?.cancel_requested
                      ? "正在停止，等待后台确认…"
                      : task?.progress.label
                    : "Enter 发送 · Shift + Enter 换行"
                }}</small
                ><button
                  v-if="generating"
                  class="send-button"
                  aria-label="停止生成"
                  title="停止生成"
                  :disabled="task?.cancel_requested"
                  @click="stop"
                >
                  <Square :size="16" /></button
                ><button
                  v-else
                  class="send-button"
                  aria-label="发送消息"
                  title="发送消息"
                  :disabled="!canSend"
                  @click="send"
                >
                  <ArrowUp :size="20" />
                </button>
              </div>
            </div>
            <p class="composer-note">学迹可能出错，请结合资料核对重要内容。</p>
          </div></template
        >
      </template>
      <template v-else
        ><section v-if="!selectedLibrary" class="empty-screen">
          <div class="empty-symbol"><BookOpen :size="43" /></div>
          <span class="eyebrow">让知识成为你的底气</span>
          <h2>把学习资料，放在一起。</h2>
          <p>上传教材、讲义与笔记，<br />让每一次答疑都有可以追溯的依据。</p>
          <button
            class="primary"
            @click="startName('新建知识库', '', '', 'library')"
          >
            <Plus :size="18" />新建知识库
          </button>
        </section>
        <section v-else class="knowledge-content">
          <div class="knowledge-summary">
            <div>
              <h3>学习资料</h3>
              <p>
                {{ files.length }} 个文件 ·
                {{ files.filter((f) => f.status === "ready").length }} 个可用
              </p>
            </div>
            <div class="search-field">
              <Search :size="17" /><input
                v-model="filter"
                placeholder="搜索文件名称"
                aria-label="搜索文件名称"
              />
            </div>
          </div>
          <div v-if="!files.length" class="upload-empty">
            <div class="empty-symbol"><FileText :size="36" /></div>
            <h3>为这个知识库添加第一份资料</h3>
            <p>支持文字 PDF、DOCX、TXT；单个≤50 MiB，每批最多10个。</p>
            <p class="muted">不支持扫描 OCR 和旧版 DOC。</p>
            <button @click="fileInput?.click()">
              <Plus :size="18" />选择文件
            </button>
          </div>
          <div v-else class="file-table-wrap">
            <table class="file-table">
              <thead>
                <tr>
                  <th>文件名称</th>
                  <th>大小</th>
                  <th>状态</th>
                  <th>上传时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="file in visibleFiles" :key="file.id">
                  <td>
                    <div class="filename">
                      <span class="file-icon"><FileText :size="21" /></span>
                      <div>
                        <b>{{ file.original_name }}</b
                        ><small
                          >{{ file.extension.replace(".", "").toUpperCase()
                          }}<span v-if="file.error">
                            · {{ file.error.message }}</span
                          ></small
                        >
                      </div>
                    </div>
                  </td>
                  <td>{{ bytes(file.byte_size) }}</td>
                  <td>
                    <span class="badge" :class="file.status">{{
                      statusName(file.status)
                    }}</span>
                  </td>
                  <td>{{ new Date(file.created_at).toLocaleDateString() }}</td>
                  <td>
                    <div class="file-actions">
                      <button
                        v-if="file.status === 'ready'"
                        @click="
                          guard(() =>
                            showPreview('/files/' + file.id + '/preview'),
                          )
                        "
                      >
                        预览</button
                      ><button
                        v-if="file.status === 'failed'"
                        @click="guard(() => reprocess(file))"
                      >
                        重试</button
                      ><button
                        class="icon-button"
                        aria-label="删除文件"
                        title="删除文件"
                        @click="
                          guard(() =>
                            askDelete('/files/' + file.id, file.original_name),
                          )
                        "
                      >
                        <X :size="16" />
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
            <p v-if="!visibleFiles.length" class="sidebar-empty">
              没有匹配的文件。
            </p>
          </div>
          <p class="knowledge-note">
            只有完整处理后的可用文件会参与检索。原始文件与聊天图片私有保存。
          </p>
        </section></template
      >
    </main>
    <input
      ref="imageInput"
      type="file"
      accept=".jpg,.jpeg,.png,.webp"
      multiple
      hidden
      @change="pickImages"
    /><input
      ref="fileInput"
      type="file"
      accept=".pdf,.docx,.txt"
      multiple
      hidden
      @change="uploadDocuments"
    />
    <div
      v-if="toast"
      class="toast"
      :class="{ error: toastError }"
      role="status"
    >
      {{ toast }}<button aria-label="关闭提示" @click="toast = ''">×</button>
    </div>
  </div>

  <Modal
    :open="!!modal"
    :title="modalTitle"
    :wide="['plans', 'preview', 'image'].includes(modal)"
    @close="closeModal"
  >
    <template v-if="modal === 'agent'"
      ><div v-if="!editingAgent" class="wizard-steps">
        <span
          v-for="(label, i) in ['基础信息', '系统提示词', '关联知识库']"
          :key="label"
          :class="{ current: wizardStep === i + 1, done: wizardStep > i + 1 }"
          ><i>{{ i + 1 }}</i
          >{{ label }}</span
        >
      </div>
      <form
        id="agent-form"
        @submit.prevent="
          editingAgent || wizardStep === 3 ? saveAgent() : nextStep()
        "
      >
        <section v-if="editingAgent || wizardStep === 1">
          <p class="muted">为学习伙伴取个名字，告诉它想帮助你做什么。</p>
          <label
            >智能体名称<input
              v-model="agentForm.name"
              required
              maxlength="50"
              placeholder="例如：线性代数学习伙伴" /></label
          ><label
            >描述 <span class="muted">可选</span
            ><textarea
              v-model="agentForm.description"
              maxlength="500"
              placeholder="你的学习方向与辅导需求"
            />
          </label>
        </section>
        <section v-if="editingAgent || wizardStep === 2">
          <label
            >系统提示词<textarea
              v-model="agentForm.system_prompt"
              class="prompt-input"
              required
              maxlength="10000"
              placeholder="描述辅导角色、讲解方式与边界，也可以让 AI 帮你生成。"
            />
          </label>
          <div class="inline-actions">
            <button
              type="button"
              :disabled="!!promptBusy"
              @click="generatePrompt('generate')"
            >
              <Sparkles :size="16" />AI 生成</button
            ><button
              type="button"
              :disabled="!!promptBusy || !agentForm.system_prompt.trim()"
              @click="generatePrompt('polish')"
            >
              润色提示词</button
            ><button
              v-if="promptBusy"
              type="button"
              @click="
                guard(async () => {
                  if (promptTask)
                    promptTask = await mutation<Task>(
                      '/tasks/' + promptTask.id + '/stop',
                    );
                })
              "
            >
              停止
            </button>
          </div>
          <div v-if="promptTask" class="prompt-result">
            <div class="section-label">
              {{ promptBusy ? promptTask.progress.label : "AI 候选提示词" }}
            </div>
            <pre>{{ promptCandidate }}</pre>
            <p v-if="promptTask.error" class="error-box">
              {{ promptTask.error.message }}
            </p>
            <button
              v-if="promptTask.status === 'succeeded'"
              type="button"
              @click="
                agentForm.system_prompt = promptCandidate;
                promptTask = null;
              "
            >
              采用结果</button
            ><button
              v-if="['failed', 'stopped'].includes(promptTask.status)"
              type="button"
              @click="guard(retryPrompt)"
            >
              重试
            </button>
            <p class="muted">采用结果只修改当前草稿；保存后才更新智能体。</p>
          </div>
        </section>
        <section v-if="editingAgent || wizardStep === 3">
          <p class="muted">
            选择你的知识库，为答疑提供资料依据。也可以暂不关联。
          </p>
          <div class="library-checklist">
            <label v-for="library in libraries" :key="library.id"
              ><input
                v-model="agentForm.knowledge_base_ids"
                type="checkbox"
                :value="library.id"
              /><BookOpen :size="19" /><span
                >{{ library.name
                }}<small>{{ library.ready_file_count }} 个可用文件</small></span
              ></label
            >
            <div v-if="!libraries.length" class="soft-panel">
              <BookOpen :size="24" />
              <p>还没有知识库。可以先创建智能体，之后再关联资料。</p>
            </div>
          </div>
        </section>
      </form></template
    >
    <template v-else-if="modal === 'settings'"
      ><div class="tabs">
        <button
          :class="{ active: settingsTab === 'appearance' }"
          @click="
            settingsTab = 'appearance';
            modalError = '';
          "
        >
          外观</button
        ><button
          :class="{ active: settingsTab === 'name' }"
          @click="
            settingsTab = 'name';
            modalError = '';
          "
        >
          显示名称</button
        ><button
          :class="{ active: settingsTab === 'password' }"
          @click="
            settingsTab = 'password';
            modalError = '';
          "
        >
          密码
        </button>
      </div>
      <div v-if="settingsTab === 'appearance'">
        <p class="muted">选择适合你的阅读外观。</p>
        <div class="theme-options">
          <button
            :class="{ active: session.user?.theme === 'light' }"
            @click="guard(() => session.update({ theme: 'light' }))"
          >
            <span class="theme-preview light"><i /><i /><i /></span
            ><Sun :size="17" />明亮</button
          ><button
            :class="{ active: session.user?.theme === 'dark' }"
            @click="guard(() => session.update({ theme: 'dark' }))"
          >
            <span class="theme-preview dark"><i /><i /><i /></span
            ><Moon :size="17" />暗色
          </button>
        </div>
        <div class="settings-account">
          <div>
            <b>{{ session.user?.display_name }}</b
            ><small>账号：{{ session.user?.login }}</small>
          </div>
        </div>
      </div>
      <form v-else id="settings-form" @submit.prevent="saveSettings">
        <template v-if="settingsTab === 'name'"
          ><label>登录账号<input :value="session.user?.login" disabled /></label
          ><label
            >显示名称<input v-model="displayName" required maxlength="32"
          /></label>
          <p class="muted">显示名称可以修改，登录账号保持不变。</p></template
        ><template v-else
          ><label
            >原密码<input
              v-model="passwordForm.old_password"
              type="password"
              autocomplete="current-password"
              required /></label
          ><label
            >新密码<input
              v-model="passwordForm.new_password"
              type="password"
              autocomplete="new-password"
              minlength="8"
              maxlength="128"
              required /></label
          ><label
            >确认新密码<input
              v-model="passwordForm.confirm_password"
              type="password"
              autocomplete="new-password"
              required
          /></label>
          <p class="warning-box">
            修改密码后，所有登录会话都会失效，需要重新登录。
          </p></template
        >
      </form></template
    >
    <template v-else-if="modal === 'plans'"
      ><div v-if="!plans.length" class="modal-empty">
        <BookOpen :size="38" />
        <h3>还没有学习计划</h3>
        <p>回到对话，告诉学习伙伴你的目标和可用时间。</p>
        <button
          @click="
            modal = '';
            draft.text = '请根据我的目标制定一份学习计划。';
          "
        >
          回到对话
        </button>
      </div>
      <div v-else class="plan-layout">
        <aside>
          <button
            v-for="p in plans"
            :key="p.id"
            :class="{ active: selectedPlanId === p.id }"
            @click="selectedPlanId = p.id"
          >
            <BookOpen :size="17" /><span
              >{{ p.name
              }}<small>内容版本 v{{ p.content_version }}</small></span
            >
          </button>
        </aside>
        <section v-if="selectedPlan" class="plan-detail">
          <div class="plan-heading">
            <div>
              <span class="eyebrow"
                >学习计划 · v{{ selectedPlan.content_version }}</span
              >
              <h3>{{ selectedPlan.name }}</h3>
            </div>
            <div class="inline-actions">
              <button
                @click="
                  startName(
                    '重命名计划',
                    '/plans/' + selectedPlan.id,
                    selectedPlan.name,
                    'plan',
                  )
                "
              >
                重命名</button
              ><button
                @click="
                  guard(() =>
                    askDelete('/plans/' + selectedPlan!.id, selectedPlan!.name),
                  )
                "
              >
                删除
              </button>
            </div>
          </div>
          <p>{{ selectedPlan.content.goal }}</p>
          <p v-if="selectedPlan.content.deadline" class="muted">
            截止日期：{{ selectedPlan.content.deadline }}
          </p>
          <div
            v-for="(stage, i) in selectedPlan.content.stages"
            :key="stage.id"
            class="plan-stage"
          >
            <span class="stage-number">{{ i + 1 }}</span>
            <div>
              <h4>{{ stage.title }}</h4>
              <p>{{ stage.goal }}</p>
              <div class="knowledge-points">
                <span v-for="point in stage.knowledge_points" :key="point">{{
                  point
                }}</span>
              </div>
              <div v-for="item in stage.tasks" :key="item.id" class="plan-task">
                <b>{{ item.title }}</b
                ><span>{{ item.estimated_minutes }} 分钟</span>
                <p
                  v-for="suggestion in item.exercise_suggestions"
                  :key="suggestion"
                >
                  {{ suggestion }}
                </p>
                <small v-if="!item.resource_source_ids.length"
                  >模型建议 · 无资料引用</small
                >
              </div>
            </div>
          </div>
          <div class="sources">
            <button
              v-for="source in selectedPlan.sources"
              :key="source.id"
              @click="guard(() => openSource(source))"
            >
              {{ source.title }} ·
              {{ source.status === "deleted" ? "来源已删除" : locator(source) }}
            </button>
          </div>
          <p v-if="!selectedPlan.source_conversation_id" class="muted">
            来源对话已删除，独立计划仍然保留。
          </p>
          <button
            v-else
            class="text-button"
            @click="
              guard(async () => {
                modal = '';
                await openConversation(selectedPlan!.source_conversation_id!);
              })
            "
          >
            打开来源对话 →
          </button>
        </section>
      </div></template
    >
    <form
      v-else-if="modal === 'name'"
      id="name-form"
      @submit.prevent="saveName"
    >
      <label
        >名称<input v-model="naming.name" autofocus required maxlength="50"
      /></label>
      <p class="muted">1–50 个字符，允许重名。</p>
    </form>
    <template v-else-if="modal === 'delete' && deletion"
      ><p>
        确定删除 <b>「{{ deletion.name }}」</b> 吗？
      </p>
      <div class="error-box">{{ deletion.impact.warning }}</div>
      <div
        v-if="Object.keys(deletion.impact.counts).length"
        class="delete-counts"
      >
        <span v-for="(count, key) in deletion.impact.counts" :key="key"
          >{{ impactName(key) }}：{{ count }}</span
        >
      </div>
      <p v-for="item in deletion.impact.retained" :key="item" class="muted">
        保留：{{ item }}
      </p></template
    >
    <template v-else-if="modal === 'preview'"
      ><p v-if="previewLoading">正在读取文字…</p>
      <template v-if="preview"
        ><p class="preview-warning">
          {{ preview.warning
          }}<span v-if="preview.focus_locator">
            · 当前引用位置：{{ JSON.stringify(preview.focus_locator) }}</span
          >
        </p>
        <div class="document-preview">
          <section v-for="block in preview.blocks" :key="block.id">
            <small>{{
              block.locator.page_start
                ? "第 " + block.locator.page_start + " 页"
                : block.locator.line_start
                  ? "第 " + block.locator.line_start + " 行"
                  : "段落/片段 " + (block.locator.block_start ?? "")
            }}</small>
            <pre>{{ block.text }}</pre>
          </section>
        </div>
        <button
          v-if="preview.next_cursor"
          :disabled="previewLoading"
          @click="guard(() => showPreview(previewPath, true))"
        >
          加载更多文字
        </button></template
      ></template
    >
    <template v-else-if="modal === 'image' && previewImage"
      ><div class="image-preview">
        <img
          :src="previewImage.images[previewImage.index].content_url"
          :alt="previewImage.images[previewImage.index].original_name"
        />
      </div>
      <div class="image-switch">
        <button
          :disabled="previewImage.index === 0"
          @click="previewImage.index--"
        >
          上一张</button
        ><span
          >{{ previewImage.index + 1 }} / {{ previewImage.images.length }}</span
        ><button
          :disabled="previewImage.index === previewImage.images.length - 1"
          @click="previewImage.index++"
        >
          下一张
        </button>
      </div></template
    >
    <template v-else-if="modal === 'upload'"
      ><p class="muted">
        {{ selectedLibrary?.name }} · 单个≤50 MiB，每批最多10个。仅支持文字
        PDF、DOCX、TXT。
      </p>
      <div class="upload-list">
        <div v-for="(item, i) in uploadItems" :key="i">
          <FileText :size="20" /><span
            >{{ item.name
            }}<small v-if="item.message">{{ item.message }}</small></span
          ><span class="badge">{{ item.status }}</span>
        </div>
      </div>
      <p>成功受理的资料会在后台处理。关闭窗口不影响处理。</p>
      <button :disabled="uploadProgress" @click="fileInput?.click()">
        {{ uploadProgress ? "正在上传…" : "再选一批文件" }}
      </button></template
    >
    <p v-if="modalError" role="alert" class="error-box">{{ modalError }}</p>
    <template #footer
      ><template v-if="modal === 'agent'"
        ><button v-if="!editingAgent && wizardStep > 1" @click="wizardStep--">
          上一步</button
        ><button @click="closeModal">取消</button
        ><button
          class="primary"
          form="agent-form"
          :disabled="saving || !!promptBusy"
        >
          {{
            saving
              ? "保存中…"
              : editingAgent
                ? "保存修改"
                : wizardStep === 3
                  ? "创建智能体"
                  : "下一步"
          }}
        </button></template
      ><template v-else-if="modal === 'name'"
        ><button @click="closeModal">取消</button
        ><button class="primary" form="name-form" :disabled="saving">
          保存
        </button></template
      ><template v-else-if="modal === 'delete'"
        ><button @click="closeModal">取消</button
        ><button class="danger" :disabled="saving" @click="confirmDelete">
          {{ saving ? "删除中…" : "确认删除" }}
        </button></template
      ><template v-else-if="modal === 'settings'">
        <button class="logout-action" @click="guard(leaveSession)">
          <LogOut :size="16" />退出登录
        </button>
        <button @click="closeModal">
          {{ settingsTab === "appearance" ? "关闭" : "取消" }}
        </button>
        <button
          v-if="settingsTab !== 'appearance'"
          class="primary"
          form="settings-form"
          :disabled="saving"
        >
          {{ settingsTab === "password" ? "修改密码" : "保存" }}
        </button></template
      ><template v-else-if="modal === 'plans' && selectedPlan"
        ><button @click="closeModal">关闭</button
        ><button
          v-if="conflictTask"
          class="primary"
          @click="guard(confirmConflictRetry)"
        >
          确认按此版本重试</button
        ><button v-else class="primary" @click="guard(modifyPlan)">
          <MessageSquare :size="17" />回到对话修改
        </button></template
      ><button v-else @click="closeModal">关闭</button></template
    >
  </Modal>
</template>
