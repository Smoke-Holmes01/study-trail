<script setup lang="ts">
import { roleNames } from '../lib/learning';
defineProps<{ report: { by_kind?: Record<string, number>; total_role_runs?: number; role_execution_counts?: Record<string, number> } }>();
</script>
<template>
  <section class="admin-card" aria-label="多智能体执行统计">
    <h2>学习请求与角色执行</h2>
    <dl class="admin-details">
      <dt>学习请求（用户整轮）</dt><dd>{{ report.by_kind?.chat ?? 0 }}</dd>
      <dt>专业角色执行总数</dt><dd>{{ report.total_role_runs ?? 0 }}</dd>
      <template v-for="(label, role) in roleNames" :key="role">
        <dt>{{ label }}</dt><dd>{{ report.role_execution_counts?.[role] ?? 0 }}</dd>
      </template>
    </dl>
    <p class="muted">一轮学习请求只计一次；角色执行与模型、工具的物理调用分别统计。角色成功不代表整轮结果已保存。</p>
  </section>
</template>
