const assert = require('node:assert/strict');
const test = require('node:test');
const verifyCIStatus = require('./verify-ci-status.js');

const sha = '0123456789abcdef0123456789abcdef01234567';
const passedJobs = [
  { name: 'Test', conclusion: 'success' },
  { name: 'Security Scan', conclusion: 'success' },
];

for (const scenario of [
  { name: 'CI 全部成功时允许手动发布', conclusion: 'success', allowed: true },
  { name: '镜像构建失败但检查通过时允许重试', conclusion: 'failure', allowed: true },
  { name: '发布取消但检查通过时允许重试', conclusion: 'cancelled', allowed: true },
  {
    name: '测试失败时拒绝发布',
    jobs: [{ name: 'Test', conclusion: 'failure' }, passedJobs[1]],
    allowed: false,
  },
  {
    name: '安全扫描失败时拒绝发布',
    jobs: [passedJobs[0], { name: 'Security Scan', conclusion: 'failure' }],
    allowed: false,
  },
  { name: '缺少安全扫描时拒绝发布', jobs: [passedJobs[0]], allowed: false },
  { name: 'CI 提交不匹配时拒绝发布', headSHA: 'different-sha', allowed: false },
  { name: '没有已完成的 CI 时拒绝发布', noRuns: true, allowed: false },
]) {
  test(scenario.name, async () => {
    const failures = [];
    const notices = [];
    await verifyCIStatus({
      github: {
        rest: {
          actions: {
            listWorkflowRuns: async () => ({
              data: {
                workflow_runs: scenario.noRuns ? [] : [{
                  id: 123,
                  head_sha: scenario.headSHA ?? sha,
                  conclusion: scenario.conclusion ?? 'failure',
                  html_url: 'https://github.com/example/repo/actions/runs/123',
                }],
              },
            }),
            listJobsForWorkflowRun: () => {},
          },
        },
        paginate: async () => scenario.jobs ?? passedJobs,
      },
      context: { ref: 'refs/heads/develop', sha, repo: { owner: 'example', repo: 'repo' } },
      core: {
        setFailed: message => failures.push(message),
        notice: message => notices.push(message),
      },
    });
    assert.equal(failures.length, scenario.allowed ? 0 : 1);
    assert.equal(notices.length, scenario.allowed ? 1 : 0);
  });
}
