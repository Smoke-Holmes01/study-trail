import { api, ApiError, operationKey, type Plan, type Source } from './api';

export interface PlanTaskContent { id: string; title: string; estimated_minutes: number; resource_source_ids: string[]; exercise_suggestions: string[] }
export interface PlanStageContent { id: string; title: string; goal: string; knowledge_points: string[]; tasks: PlanTaskContent[] }
export interface PlanContent { title: string; goal: string; deadline: string | null; stages: PlanStageContent[] }
export interface LearningExercise { id: string; question_text: string; content_version: number; current_revision_id?: string; origin_kind?: string; user_edited?: boolean; provenance?: string; source_ids?: string[]; sources?: Source[]; links?: ExerciseLink[]; answer_text?: string | null; explanation?: string | null; template_version_id?: string; conversation_id?: string | null }
export interface ExerciseLink { plan_id: string; stage_id?: string | null; task_id?: string | null }
export interface ExerciseContent { question_text: string; answer_text: string | null; explanation: string | null; provenance?: string; source_ids: string[]; answer_source_ids?: string[]; links: ExerciseLink[] }
export interface ConfigChoices { system_prompt: string; model_id: string; mcp_server_ids: string[]; skill_ids: string[] }
export interface TemplateExerciseContent extends Omit<ExerciseContent, "links"> { links: {plan_id?:string;stage_id?:string|null;task_id?:string|null}[] }
export interface TemplatePayload { name?:string; description?:string; config_choices: ConfigChoices; plan: PlanContent; exercises: TemplateExerciseContent[]; asset_ids: string[] }
export interface TemplateSummary { id: string; name: string; description: string; published_version_id?: string; version_id?: string; status?: string; lock_version?: number }
export interface TemplatePreview extends TemplateSummary { version_id: string; config_choices: ConfigChoices; plan: PlanContent; exercises: ExerciseContent[]; asset_manifest: { id?: string; asset_id?: string; filename?: string; original_name?: string; locator?: unknown }[]; capability_status?: unknown }
export interface Adoption { id: string; agent_id: string; plan_ids: string[]; exercise_ids: string[]; template_version_id: string; import_batch_id: string | null }
export interface ImportBatch { id: string; status: string; revision: number; items: { id: string; file_id: string | null; filename?: string; status: string; error_code?: string | null; can_retry?: boolean }[] }
export interface RoleRun { role: string; step_no: number; status: string; phase?: string; started_at?: string | null; finished_at?: string | null; error_code?: string | null; degradation?: string | string[] | null }

export const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value));
export function newPlan(): PlanContent { return { title: '学习计划', goal: '', deadline: null, stages: [newStage()] }; }
export function newStage(): PlanStageContent { return { id: crypto.randomUUID(), title: '', goal: '', knowledge_points: [''], tasks: [newPlanTask()] }; }
export function newPlanTask(): PlanTaskContent { return { id: crypto.randomUUID(), title: '', estimated_minutes: 30, resource_source_ids: [], exercise_suggestions: [] }; }
export function planPayload(plan: Plan): PlanContent { return clone({ ...plan.content, title: plan.name }) as PlanContent; }
export function exerciseDraft(exercise?: LearningExercise): ExerciseContent { return { question_text: exercise?.question_text ?? '', answer_text: exercise?.answer_text ?? null, explanation: exercise?.explanation ?? null, source_ids: [...(exercise?.source_ids ?? [])], links: clone(exercise?.links ?? []) }; }
export function planError(value: PlanContent): string { if (!value.title.trim() || !value.goal.trim()) return '请填写计划名称和学习目标。'; if (!value.stages.length) return '请至少保留一个阶段。'; for (const stage of value.stages) { if (!stage.title.trim() || !stage.goal.trim() || !stage.knowledge_points.some(p => p.trim())) return '请填写每个阶段的名称、目标和知识点。'; if (!stage.tasks.length || stage.tasks.some(t => !t.title.trim() || !Number.isInteger(t.estimated_minutes) || t.estimated_minutes < 1)) return '每个阶段至少一项任务，任务名称和正整数耗时必填。'; } return ''; }
export function write<T>(path: string, method: string, body?: unknown, key: string = operationKey()) { return api<T>(path, { method, body, key }); }
export function requestFingerprint(body: unknown) {
 if (!(body instanceof FormData)) return JSON.stringify(body??null);
 const entries: unknown[]=[];
 body.forEach((value,key)=>entries.push([key,typeof value==='string'?value:[value.name,value.size,value.type,value.lastModified]]));
 return JSON.stringify(entries);
}
export function createWriter() {
 const pending=new Map<string,string>();
 return async function<T>(path:string,method:string,body?:unknown):Promise<T>{
  const fingerprint=method+path+requestFingerprint(body);
  const key=pending.get(fingerprint)??operationKey();pending.set(fingerprint,key);
  try{const result=await write<T>(path,method,body,key);pending.delete(fingerprint);return result;}
  catch(e){if(!(e instanceof ApiError)||e.status!==0)pending.delete(fingerprint);throw e;}
 };
}
export const roleNames: Record<string,string> = { plan: '学习计划', tutor: '答疑讲解', exercise: '练习生成', explanation: '答案解析', feedback: '作答反馈' };
export const answerOriginNames: Record<string,string> = { template: '模板预置', derived: '学迹推导', user_edited: '用户编辑', original: '资料答案' };
export const learningStates: Record<string,string> = { queued: '等待中', waiting: '等待中', pending: '等待中', running: '处理中', processing: '处理中', ready: '成功', succeeded: '成功', completed: '完成', failed: '失败', stopped: '已停止', partial_failed: '部分失败', partial_failure: '部分失败', skipped: '已跳过', draft: '草稿', published: '已发布', offline: '已下架', deleted: '已删除' };
