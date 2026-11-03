<script setup lang="ts">
import { computed } from 'vue';
import { learningStates, roleNames, type RoleRun } from '../lib/learning';
const props = defineProps<{ runs?: RoleRun[] }>();
const ordered = computed(() => [...(props.runs ?? [])].sort((a,b) => a.step_no-b.step_no));
</script>
<template><details v-if="ordered.length" class="role-runs reference-disclosure"><summary>协作执行过程（{{ ordered.length }}）</summary><ol><li v-for="run in ordered" :key="run.step_no"><b>{{ roleNames[run.role] ?? run.role }}</b> · {{ learningStates[run.status] ?? run.status }}<span v-if="run.phase"> · {{ run.phase }}</span><small v-if="run.error_code"> · {{ run.error_code }}</small><p v-if="run.degradation" class="muted">{{ Array.isArray(run.degradation) ? run.degradation.join('；') : run.degradation }}</p></li></ol></details></template>
