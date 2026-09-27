"""Regression tests for repository security configuration."""

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_dependabot_security_groups_apply_to_default_branch() -> None:
    config = (REPOSITORY_ROOT / ".github" / "dependabot.yml").read_text(
        encoding="utf-8"
    )

    assert "target-branch:" not in config
    assert config.count("applies-to: security-updates") == 4


def test_release_input_is_validated_before_writing_outputs() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    determine_version = workflow.split("- name: 确定版本号", 1)[1].split(
        "- name: 验证发布来源与版本一致性", 1
    )[0]

    validation = determine_version.index("版本号格式无效")
    output_write = determine_version.index('>> "$GITHUB_OUTPUT"')
    assert validation < output_write
