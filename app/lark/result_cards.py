from __future__ import annotations


def build_result_card(title: str, body: str) -> dict[str, object]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}},
        "elements": [
            {"tag": "markdown", "content": body},
        ],
    }


def build_job_result_card(job_id: str, result: dict[str, object]) -> dict[str, object]:
    body = _format_result(result)
    return build_result_card(f"Project Echo Result {job_id}", body)


def build_job_error_card(job_id: str, error_message: str) -> dict[str, object]:
    return build_result_card(f"Project Echo Error {job_id}", f"```text\n{error_message}\n```")


def _format_result(result: dict[str, object]) -> str:
    lines: list[str] = []
    for key, value in result.items():
        lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines) or "- No result"
