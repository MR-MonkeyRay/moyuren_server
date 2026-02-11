module.exports = async ({ github, context, core }) => {
  const branch = context.ref.replace('refs/heads/', '');
  const { data } = await github.rest.actions.listWorkflowRuns({
    owner: context.repo.owner,
    repo: context.repo.repo,
    workflow_id: 'ci.yml',
    branch,
    event: 'push',
    status: 'completed',
    per_page: 1,
  });
  if (!data.workflow_runs.length) {
    core.setFailed(`No completed CI run found on branch ${branch}. Re-run with force_publish=true to override.`);
    return;
  }
  const latest = data.workflow_runs[0];
  if (latest.conclusion !== 'success') {
    core.setFailed(`Latest CI run is ${latest.conclusion}. Re-run with force_publish=true to override. ${latest.html_url}`);
    return;
  }
  if (latest.head_sha !== context.sha) {
    core.setFailed(`Latest CI run (${latest.head_sha.slice(0, 7)}) does not match current commit (${context.sha.slice(0, 7)}). Push first or re-run with force_publish=true. ${latest.html_url}`);
    return;
  }
  core.notice(`Latest CI run passed: ${latest.html_url}`);
};
