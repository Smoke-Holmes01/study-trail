<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import type { Config } from "../lib/admin";
const props = defineProps<{
  kind: string;
  config: Config | null;
  saving: boolean;
  testing: boolean;
  testResult: string;
  error: string;
}>();
const emit = defineEmits<{
  save: [body: Record<string, unknown>];
  test: [draft: Record<string, unknown>];
  close: [];
  reload: [];
}>();
const original = props.config;
const payload = original?.payload ?? {};
const caps = payload.capabilities ?? {};
const form = reactive({
  stable_id: original?.stable_id ?? "",
  name: original?.name ?? "",
  description: original?.description ?? "",
  purpose: original?.purpose ?? "intent",
  body: payload.body ?? "",
  model_kind: payload.model_kind ?? "generative",
  endpoint: payload.endpoint ?? "",
  api_model_id: payload.api_model_id ?? "",
  supports_images: caps.supports_images ?? false,
  input_token_budget: caps.input_token_budget ?? 32768,
  max_output_tokens: caps.max_output_tokens ?? 4096,
  max_images: caps.max_images ?? 6,
  max_image_bytes: caps.max_image_bytes ?? 10485760,
  json_object: caps.json_object ?? false,
  model_revision: payload.model_revision ?? "initial",
  normalization_version: payload.normalization_version ?? "dense-v1",
  exposed: payload.exposed ?? true,
  transport: payload.transport ?? "streamable_http",
  url: payload.url ?? "",
  command: payload.command ?? "",
  args: (payload.args ?? []).join("\n"),
  cwd: payload.cwd ?? "",
  allowed_tools: (payload.allowed_tools ?? []).join("\n"),
  timeout_seconds: payload.timeout_seconds ?? 30,
  default_enabled: payload.default_enabled ?? false,
  env: JSON.stringify(payload.env ?? {}, null, 2),
  headers: JSON.stringify(
    payload.nonsecret_headers ?? payload.headers ?? {},
    null,
    2,
  ),
  schemas: JSON.stringify(payload.parameter_schemas ?? {}, null, 2),
  api_key: "",
  secret_slots: "{}",
});
const localError = ref("");
const purposes: Record<string, string> = {
  intent: "意图识别",
  coverage: "资料覆盖判断",
  answer: "学习回答",
  plan_extract: "计划结构提取",
  plan_format_repair: "计划格式修复",
  prompt_generate: "提示词生成",
  prompt_polish: "提示词润色",
  context_summary: "上下文摘要",
};
const title = computed(
  () =>
    ({
      model: "模型配置",
      prompt: "平台提示词",
      skill: "学习技能",
      mcp: "MCP服务",
    })[props.kind] ?? "配置",
);
function value() {
  let p: Record<string, unknown>;
  let secrets: Record<string, unknown> = {};
  if (props.kind === "model") {
    p = {
      model_kind: form.model_kind,
      endpoint: form.endpoint.trim(),
      api_model_id: form.api_model_id.trim(),
      capabilities: {},
    };
    if (form.model_kind === "generative")
      Object.assign(p, {
        exposed: form.exposed,
        capabilities: {
          supports_images: form.supports_images,
          input_token_budget: form.input_token_budget,
          max_output_tokens: form.max_output_tokens,
          max_images: form.supports_images ? form.max_images : 0,
          max_image_bytes: form.supports_images ? form.max_image_bytes : 0,
          json_object: form.json_object,
        },
      });
    if (form.model_kind === "embedding")
      Object.assign(p, {
        dimensions: 1024,
        model_revision: form.model_revision,
        normalization_version: form.normalization_version,
        chunking_version: payload.chunking_version ?? "v1",
      });
    secrets = { api_key: form.api_key };
  } else if (props.kind === "prompt")
    p = { purpose: form.purpose, body: form.body };
  else if (props.kind === "skill")
    p = {
      body: form.body,
      description: form.description,
      content_version: String((original?.revision_no ?? 0) + 1),
      default_enabled: form.default_enabled,
    };
  else {
    p = {
      transport: form.transport,
      allowed_tools: form.allowed_tools
        .split(/\r?\n/)
        .map((v: string) => v.trim())
        .filter(Boolean),
      timeout_seconds: form.timeout_seconds,
      default_enabled: form.default_enabled,
      parameter_schemas: JSON.parse(form.schemas),
    };
    if (form.transport === "streamable_http")
      Object.assign(p, {
        url: form.url.trim(),
        nonsecret_headers: JSON.parse(form.headers),
      });
    else
      Object.assign(p, {
        command: form.command,
        args: form.args.split(/\r?\n/).filter(Boolean),
        cwd: form.cwd || null,
        env: JSON.parse(form.env),
      });
    secrets = JSON.parse(form.secret_slots);
  }
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    payload: p,
    secret_updates: secrets,
    ...(original
      ? { expected_version: original.lock_version }
      : {
          stable_id: form.stable_id,
          enabled: false,
          ...(props.kind === "prompt" ? { purpose: form.purpose } : {}),
        }),
  };
}
function submit(test = false) {
  localError.value = "";
  try {
    const body = value();
    if (test)
      emit("test", {
        name: body.name,
        description: body.description,
        payload: body.payload,
        secret_updates: body.secret_updates,
      });
    else emit("save", body);
  } catch {
    localError.value = "请检查连接字段或参数约束的JSON格式。";
  }
}
</script>
<template>
  <div
    class="admin-modal-backdrop"
    @click.self="!saving && !testing && emit('close')"
  >
    <section
      class="admin-modal"
      role="dialog"
      aria-modal="true"
      :aria-label="title"
    >
      <header>
        <div>
          <p class="eyebrow">
            {{
              original ? `内容版本 ${original.revision_no}` : "新建后默认停用"
            }}
          </p>
          <h2>{{ original ? "编辑" : "新建" }}{{ title }}</h2>
        </div>
        <button
          aria-label="关闭"
          :disabled="saving || testing"
          @click="emit('close')"
        >
          ×
        </button>
      </header>
      <form @submit.prevent="submit()">
        <div class="admin-form-grid">
          <label
            >稳定标识<input
              v-model="form.stable_id"
              required
              :readonly="!!original"
              :pattern="
                kind === 'skill' || kind === 'mcp'
                  ? '[a-z0-9]+(-[a-z0-9]+)*'
                  : '[A-Za-z0-9][A-Za-z0-9._-]*'
              "
              maxlength="80"
            /><small>创建后不可修改，用于保留用户已有选择。</small></label
          >
          <label
            >名称<input v-model="form.name" required maxlength="80"
          /></label>
          <label class="span-2"
            >说明<textarea
              v-model="form.description"
              rows="2"
              maxlength="500"
            />
          </label>
          <template v-if="kind === 'model'">
            <label
              >模型用途<select v-model="form.model_kind" :disabled="!!original">
                <option value="generative">生成</option>
                <option value="embedding">嵌入</option>
                <option value="rerank">重排</option>
              </select></label
            >
            <label
              >接口模型标识<input v-model="form.api_model_id" required
            /></label>
            <label class="span-2"
              >连接地址<input
                v-model="form.endpoint"
                required
                type="url"
                placeholder="https://…/v1"
            /></label>
            <label class="span-2"
              >接口凭据<input
                v-model="form.api_key"
                type="password"
                autocomplete="new-password"
                :placeholder="
                  original?.secret_slots.api_key?.has_value
                    ? '••••••••（留空保留已有凭据）'
                    : '填写接口密钥'
                "
                :required="!original"
            /></label>
            <template v-if="form.model_kind === 'generative'">
              <label
                >上下文输入预算<input
                  v-model.number="form.input_token_budget"
                  type="number"
                  min="1"
                  required
              /></label>
              <label
                >最大输出预算<input
                  v-model.number="form.max_output_tokens"
                  type="number"
                  min="1"
                  required
              /></label>
              <label class="admin-check"
                ><input
                  v-model="form.exposed"
                  type="checkbox"
                />向用户开放</label
              >
              <label class="admin-check"
                ><input
                  v-model="form.json_object"
                  type="checkbox"
                />支持JSON对象输出</label
              >
              <label class="admin-check span-2"
                ><input
                  v-model="form.supports_images"
                  type="checkbox"
                />支持图片输入</label
              >
              <template v-if="form.supports_images"
                ><label
                  >模型图片数量上限<input
                    v-model.number="form.max_images"
                    type="number"
                    min="0" /></label
                ><label
                  >模型图片容量上限（字节）<input
                    v-model.number="form.max_image_bytes"
                    type="number"
                    min="0" /></label
              ></template>
            </template>
            <template v-if="form.model_kind === 'embedding'"
              ><label>维度<input value="1024" readonly /></label
              ><label
                >模型版本<input v-model="form.model_revision" required /></label
              ><label
                >归一化版本<input v-model="form.normalization_version" required
              /></label>
              <p class="admin-notice span-2">
                已有索引或在途文件处理时，不能保存改变索引身份的配置。凭据更新不改变索引身份。
              </p></template
            >
          </template>
          <template v-if="kind === 'prompt' || kind === 'skill'">
            <label v-if="kind === 'prompt'" class="span-2"
              >处理用途<select v-model="form.purpose" :disabled="!!original">
                <option v-for="(label, id) in purposes" :key="id" :value="id">
                  {{ label }}
                </option>
              </select></label
            >
            <label class="span-2"
              >指令正文<textarea
                v-model="form.body"
                required
                rows="13"
                maxlength="65536"
              />
            </label>
            <p class="muted span-2">
              保存产生新内容版本；已受理任务继续采用原版本。
            </p>
          </template>
          <template v-if="kind === 'mcp'">
            <label
              >连接方式<select v-model="form.transport">
                <option value="streamable_http">Streamable HTTP</option>
                <option value="stdio">标准输入输出</option>
              </select></label
            >
            <label
              >单次超时（秒）<input
                v-model.number="form.timeout_seconds"
                type="number"
                min="1"
                max="30"
                required
            /></label>
            <template v-if="form.transport === 'streamable_http'"
              ><label class="span-2"
                >服务地址<input v-model="form.url" type="url" required /></label
              ><label class="span-2"
                >非秘密请求头（JSON对象）<textarea
                  v-model="form.headers"
                  rows="3"
                  spellcheck="false"
                /></label
            ></template>
            <template v-else
              ><label>允许的程序<input v-model="form.command" required /></label
              ><label>允许的工作目录<input v-model="form.cwd" /></label
              ><label class="span-2"
                >程序参数（每行一项）<textarea
                  v-model="form.args"
                  rows="3"
                /></label
              ><label class="span-2"
                >非秘密环境字段（JSON对象）<textarea
                  v-model="form.env"
                  rows="3"
                /></label
            ></template>
            <label class="span-2"
              >工具白名单（每行一个名称）<textarea
                v-model="form.allowed_tools"
                required
                rows="3"
              />
            </label>
            <label class="span-2"
              >更换凭据（JSON对象）<textarea
                v-model="form.secret_slots"
                rows="3"
                spellcheck="false"
                autocomplete="off"
              /><small
                >如 {"header.Authorization":"Bearer …"} 或
                {"env.API_KEY":"…"}；空白保留原值。已有槽：{{
                  Object.keys(original?.secret_slots ?? {}).join("、") || "无"
                }}</small
              ></label
            >
            <details class="span-2">
              <summary>工具参数约束</summary>
              <textarea
                v-model="form.schemas"
                rows="5"
                aria-label="工具参数约束JSON"
                spellcheck="false"
              />
            </details>
            <label class="admin-check span-2"
              ><input
                v-model="form.default_enabled"
                type="checkbox"
              />新智能体默认开启此服务</label
            >
          </template>
        </div>
        <p v-if="error || localError" class="admin-error" role="alert">
          {{ localError || error }}
          <button
            v-if="error.includes('重新载入')"
            type="button"
            @click="emit('reload')"
          >
            重新载入记录
          </button>
        </p>
        <p v-if="testResult" class="admin-notice" role="status">
          {{ testResult }}。连接检查不保存草稿，也不代表完整模型能力验收。
        </p>
        <footer>
          <button
            v-if="kind === 'model' || kind === 'mcp'"
            type="button"
            :disabled="testing || saving"
            @click="submit(true)"
          >
            {{ testing ? "正在检查…" : "检查连接" }}</button
          ><span class="admin-spacer" /><button
            type="button"
            :disabled="saving || testing"
            @click="emit('close')"
          >
            取消</button
          ><button class="primary" :disabled="saving || testing">
            {{ saving ? "正在保存…" : "保存配置" }}
          </button>
        </footer>
      </form>
    </section>
  </div>
</template>
