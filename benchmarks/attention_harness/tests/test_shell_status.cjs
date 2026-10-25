// The cover and Memory task rail (shell.js) use the same run status words and attempt counts as the main UI's task list.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const shell = require('../ui/shell.js');

test('run outcomes use the main UI words', () => {
  const cases = [
    [{state: 'completed', solved: true}, ['ok', 'Solved']],
    [{state: 'completed', solved: false}, ['bad', 'Not solved']],
    [{state: 'completed', all_failed: true}, ['bad', 'Failed']],
    [{state: 'needs_review', system: true}, ['bad', 'System problem']],
    [{state: 'needs_review'}, ['', 'Needs review']],
    [{state: 'condition_invalid'}, ['bad', 'Condition invalid']],
    [{state: 'cancelled'}, ['', 'Stopped']],
    [{state: 'running'}, ['run', 'Running']],
  ];
  for (const [row, expected] of cases) assert.deepEqual(shell.outcome(row), expected, JSON.stringify(row));
});

test('attempts count runs out of the limit, like the main UI badge', () => {
  assert.equal(shell.attemptsText({state: 'needs_review', attempts: 0, attempts_limit: 5}), '0/5 attempts');
  assert.equal(shell.attemptsText({state: 'completed', attempts: 1, attempts_limit: 1}), '1/1 attempt');
  assert.equal(shell.attemptsText({state: 'running', attempts: 2, attempts_limit: 5}), 'Attempt 3/5');
  assert.equal(shell.attemptsText({state: 'completed', attempts: 1}), '1 attempt');
});

test('Memory recent run records label r3 only', () => {
  const render=profile=>{const list={},meta={};shell.taskRail({list,meta,status:{today:{runs:1},running:[],recent:[{experiment_id:'e',at:1,task:'Task',policy:'autonomous',execution_profile:profile}]}});return list.innerHTML;};
  assert.match(render('r3-20261025'),/Execution settings r3 · not comparable with earlier runs/);
  assert.doesNotMatch(render(undefined),/Execution settings r3/);
});
