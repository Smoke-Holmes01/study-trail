<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { mutation, type MCPServer, type Skill } from "../lib/api";

const props = defineProps<{
  tab: "mcp" | "skills";
  servers: MCPServer[];
  skills: Skill[];
  mcpIds: string[];
  skillIds: string[];
}>();
const emit = defineEmits<{
  "update:mcpIds": [value: string[]];
  "update:skillIds": [value: string[]];
  checked: [server: MCPServer];
}>();
const filter = ref("");
const checking = ref(new Set<string>());
const filteredSkills = computed(() =>
  props.skills.filter((s) =>
    `${s.name} ${s.description}`
      .toLowerCase()
      .includes(filter.value.toLowerCase().trim()),
  ),
);
const missingMcp = computed(() =>
  props.mcpIds.filter((id) => !props.servers.some((s) => s.id === id)),
);
const missingSkills = computed(() =>
  props.skillIds.filter((id) => !props.skills.some((s) => s.id === id)),
);
const allSkills = computed(
  () =>
    !!props.skills.length &&
    props.skills.every((s) => props.skillIds.includes(s.id)),
);
function toggle(kind: "mcp" | "skill", id: string) {
  const current = kind === "mcp" ? props.mcpIds : props.skillIds;
  const next = current.includes(id)
    ? current.filter((x) => x !== id)
    : [...current, id];
  if (kind === "mcp") emit("update:mcpIds", next);
  else emit("update:skillIds", next);
}
async function check(server: MCPServer) {
  if (checking.value.has(server.id) || !server.enabled) return;
  checking.value.add(server.id);
  try {
    emit(
      "checked",
      await mutation<MCPServer>(
        `/mcp/servers/${encodeURIComponent(server.id)}/check`,
      ),
    );
  } catch {
    emit("checked", { ...server, status: "unavailable" });
  } finally {
    checking.value.delete(server.id);
  }
}
watch(
  () => props.tab,
  (tab) => {
    if (tab === "mcp") for (const server of props.servers) void check(server);
  },
  { immediate: true },
);
const statusLabels = {
  unknown: "未检查",
  connected: "已连接",
  unavailable: "连接不可用",
  disabled: "后台已关闭",
};
</script>

<template>
  <section v-if="tab === 'mcp'" class="capability-panel">
    <h3>可用 MCP 服务</h3>
    <p class="muted">开启后，学习伙伴可按需调用服务查询资料。</p>
    <div class="capability-list">
      <article
        v-for="server in servers"
        :key="server.id"
        class="capability-row"
      >
        <div class="capability-copy">
          <strong>{{ server.name }}</strong>
          <span class="connection-badge" :class="server.status" role="status">
            {{
              checking.has(server.id)
                ? "连接检查中…"
                : statusLabels[server.status]
            }}
          </span>
          <p>{{ server.description }}</p>
          <button
            v-if="server.enabled && server.status === 'unavailable'"
            type="button"
            class="text-action"
            :disabled="checking.has(server.id)"
            @click="check(server)"
          >
            重新检查
          </button>
        </div>
        <button
          type="button"
          class="capability-switch"
          role="switch"
          :aria-label="'启用 ' + server.name"
          :aria-checked="mcpIds.includes(server.id)"
          :disabled="!server.enabled && !mcpIds.includes(server.id)"
          @click="toggle('mcp', server.id)"
        >
          <span />
        </button>
      </article>
      <article v-for="id in missingMcp" :key="id" class="capability-row">
        <div class="capability-copy">
          <strong>{{ id }}</strong>
          <p>后台已移除，请关闭此项。</p>
        </div>
        <button
          type="button"
          class="capability-switch"
          role="switch"
          :aria-label="'移除 ' + id"
          :aria-checked="true"
          @click="toggle('mcp', id)"
        >
          <span />
        </button>
      </article>
    </div>
    <p v-if="!servers.length && !missingMcp.length" class="soft-panel">
      暂无 MCP 服务。
    </p>
  </section>
  <section v-else class="capability-panel">
    <h3>可用技能</h3>
    <p class="muted">开启后可在聊天输入框输入 / 选择技能。</p>
    <label class="skill-search"
      >搜索技能<input v-model="filter" placeholder="按名称或说明搜索"
    /></label>
    <div class="capability-row all-skills">
      <strong>全部开启</strong>
      <button
        type="button"
        class="capability-switch"
        role="switch"
        aria-label="全部开启技能"
        :aria-checked="allSkills"
        :disabled="!skills.length"
        @click="
          emit('update:skillIds', allSkills ? [] : skills.map((s) => s.id))
        "
      >
        <span />
      </button>
    </div>
    <div class="skill-grid">
      <article
        v-for="skill in filteredSkills"
        :key="skill.id"
        class="capability-row skill-card"
      >
        <div class="capability-copy">
          <strong>{{ skill.name }}</strong>
          <p>{{ skill.description }}</p>
        </div>
        <button
          type="button"
          class="capability-switch"
          role="switch"
          :aria-label="'启用 ' + skill.name"
          :aria-checked="skillIds.includes(skill.id)"
          @click="toggle('skill', skill.id)"
        >
          <span />
        </button>
      </article>
      <article
        v-for="id in missingSkills"
        :key="id"
        class="capability-row skill-card"
      >
        <div class="capability-copy">
          <strong>{{ id }}</strong>
          <p>后台已移除，请关闭此项。</p>
        </div>
        <button
          type="button"
          class="capability-switch"
          role="switch"
          :aria-label="'移除 ' + id"
          :aria-checked="true"
          @click="toggle('skill', id)"
        >
          <span />
        </button>
      </article>
    </div>
    <p v-if="!skills.length && !missingSkills.length" class="soft-panel">
      暂无技能。
    </p>
    <p
      v-else-if="!filteredSkills.length && !missingSkills.length"
      class="muted"
    >
      没有匹配的技能。
    </p>
  </section>
</template>
