<script setup lang="ts">
import { newStage, newPlanTask, type PlanContent } from '../lib/learning';
defineProps<{ value: PlanContent; showTitle?: boolean; disabled?: boolean; sources?: {id:string;title:string}[] }>();
function lines(event: Event) { return (event.target as HTMLTextAreaElement).value.split('\n').map(s => s.trim()).filter(Boolean); }
</script>
<template>
  <fieldset class="learning-editor" :disabled="disabled">
    <label v-if="showTitle">计划名称<input v-model="value.title" maxlength="50" required /></label>
    <label>学习目标<textarea v-model="value.goal" required rows="2" /></label>
    <label>截止日期（可选）<input :value="value.deadline ?? ''" type="date" @input="value.deadline = ($event.target as HTMLInputElement).value || null" /></label>
    <section v-for="(stage, i) in value.stages" :key="stage.id" class="learning-card">
      <div class="learning-row"><b>阶段 {{ i + 1 }}</b><button type="button" :disabled="i === 0" aria-label="上移阶段" @click="value.stages.splice(i - 1, 0, value.stages.splice(i, 1)[0]!)">上移</button><button type="button" :disabled="value.stages.length < 2" @click="value.stages.splice(i, 1)">删除阶段</button></div>
      <label>阶段名称<input v-model="stage.title" required /></label><label>阶段目标<textarea v-model="stage.goal" required rows="2" /></label>
      <label>知识点（每行一项）<textarea :value="stage.knowledge_points.join('\n')" rows="3" @input="stage.knowledge_points = lines($event)" required /></label>
      <section v-for="(task, j) in stage.tasks" :key="task.id" class="learning-task">
        <label>任务 {{ j + 1 }}<input v-model="task.title" required /></label><label>预计耗时（分钟）<input v-model.number="task.estimated_minutes" type="number" min="1" step="1" required /></label>
        <label>练习建议（每行一项）<textarea :value="task.exercise_suggestions.join('\n')" rows="2" @input="task.exercise_suggestions = lines($event)" /></label>
        <p v-if="task.resource_source_ids.length" class="muted">保留 {{ task.resource_source_ids.length }} 项资料引用</p>
        <fieldset v-if="sources?.length"><legend>参考资料</legend><label v-for="source in sources" :key="source.id" class="check-row"><input v-model="task.resource_source_ids" type="checkbox" :value="source.id" />{{source.title}}</label></fieldset>
        <button type="button" :disabled="j === 0" @click="stage.tasks.splice(j - 1, 0, stage.tasks.splice(j, 1)[0]!)">上移任务</button> <button type="button" :disabled="stage.tasks.length < 2" @click="stage.tasks.splice(j, 1)">删除任务</button>
      </section><button type="button" @click="stage.tasks.push(newPlanTask())">添加任务</button>
    </section><button type="button" @click="value.stages.push(newStage())">添加阶段</button>
  </fieldset>
</template>
