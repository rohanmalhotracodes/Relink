from __future__ import annotations

from pathlib import Path

from repair_agent.drift.detect import declare_drift
from repair_agent.falkordb_io.ids import dataset_id
from repair_agent.models import ImpactReport, PullRequestResult, RepairRun
from repair_agent.pr.github_comment import GitHubCommentProvider, render_issue_comment, render_pr_comment


def _run() -> RepairRun:
    drift = declare_drift(
        kind="RENAME",
        dataset_urn=dataset_id("shop_prod.raw.orders"),
        old_column="order_placed_at",
        new_column="order_created_at",
    )
    return RepairRun(
        id="run-comment-test",
        drift=drift,
        impact=ImpactReport(
            drift=drift,
            stats={"requires_patch": 2, "downstream_unaffected": 1, "skipped": 3},
        ),
        pr=PullRequestResult(
            mode="dry-run",
            url=".relink/pr_bodies/test.md",
            branch="repair/test",
        ),
    )


def test_github_comment_dry_run_writes_reviewable_artifact(tmp_path: Path) -> None:
    provider = GitHubCommentProvider(tmp_path / "comments", repo_root=tmp_path)
    result = provider.deliver(
        _run(),
        repository="shopflow/analytics",
        target_number=42,
        public_url="https://repairs.example.test",
        live=False,
        kind="issue",
    )

    assert result.mode == "dry-run" and result.ok
    assert result.target_url == "https://github.com/shopflow/analytics/issues/42"
    assert result.kind == "issue"
    assert result.artifact_path is not None
    artifact = tmp_path / result.artifact_path
    assert artifact.exists()
    body = artifact.read_text(encoding="utf-8")
    assert "2 direct patch target(s)" in body
    assert "1 alias-insulated asset(s)" in body
    assert "https://repairs.example.test/demo/impact?run=run-comment-test" in body


def test_issue_and_pr_comments_are_distinct(tmp_path: Path) -> None:
    run = _run()
    issue = render_issue_comment(run, "http://localhost:3002")
    pull_request = render_pr_comment(run, "http://localhost:3002")

    assert "Relink impact assessment" in issue
    assert "direct references" in issue
    assert "Relink repair review" in pull_request
    assert issue != pull_request

    provider = GitHubCommentProvider(tmp_path / "comments", repo_root=tmp_path)
    issue_result = provider.deliver(
        run,
        repository="shopflow/analytics",
        target_number=41,
        public_url="http://localhost:3002",
        live=False,
        kind="issue",
    )
    pr_result = provider.deliver(
        run,
        repository="shopflow/analytics",
        target_number=42,
        public_url="http://localhost:3002",
        live=False,
        kind="pull_request",
    )
    assert issue_result.artifact_path != pr_result.artifact_path
    assert (tmp_path / str(issue_result.artifact_path)).exists()
    assert (tmp_path / str(pr_result.artifact_path)).exists()


def test_github_comment_is_disabled_without_explicit_target(tmp_path: Path) -> None:
    provider = GitHubCommentProvider(tmp_path / "comments", repo_root=tmp_path)
    result = provider.deliver(
        _run(),
        repository="",
        target_number=None,
        public_url="http://localhost:3002",
        live=False,
    )

    assert result.mode == "disabled" and result.ok
    assert not (tmp_path / "comments").exists()
