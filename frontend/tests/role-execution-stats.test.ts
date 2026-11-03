import { mount } from '@vue/test-utils';
import { expect, it } from 'vitest';
import RoleExecutionStats from '../src/components/RoleExecutionStats.vue';

it('keeps user rounds distinct from role executions and physical calls', () => {
  const view = mount(RoleExecutionStats, { props: { report: {
    by_kind: { chat: 2, prompt: 3 }, total_role_runs: 5,
    role_execution_counts: { plan: 2, tutor: 1, exercise: 1, explanation: 1 },
  } } });
  const entries = view.findAll('dt').map((label, index) => [label.text(), view.findAll('dd')[index].text()]);
  expect(entries).toEqual([
    ['学习请求（用户整轮）', '2'], ['专业角色执行总数', '5'],
    ['学习计划', '2'], ['答疑讲解', '1'], ['练习生成', '1'], ['答案解析', '1'], ['作答反馈', '0'],
  ]);
  expect(view.text()).toContain('物理调用分别统计');
  expect(view.text()).toContain('角色成功不代表整轮结果已保存');
});
