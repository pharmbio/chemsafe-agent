from pathlib import Path
from langchain.tools import tool

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def _get_skill_path(skill_name: str) -> Path:
    return SKILLS_DIR / skill_name / f"{skill_name}.md"


def _parse_frontmatter(markdown_text: str) -> dict[str, str]:
    lines = markdown_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    metadata: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata

def read_skill_metadata(skill_name: str) -> dict[str, str]:
    """Extract frontmatter metadata from a skill markdown file."""
    skill_path = _get_skill_path(skill_name)
    try:
        metadata = _parse_frontmatter(skill_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "skill_name": skill_name,
            "name": skill_name,
            "description": f"Error: Skill {skill_name} does not exist.",
        }

    return {
        "skill_name": skill_name,
        "name": metadata.get("name", skill_name),
        "description": metadata.get("description", "No description provided."),
    }


def format_skill_summaries(skill_names: list[str]) -> str:
    """Render skill names and descriptions for prompt injection."""
    lines = []
    for skill_name in skill_names:
        metadata = read_skill_metadata(skill_name)
        lines.append(
            f'- `{metadata["skill_name"]}`: {metadata["description"]}'
        )
    return "\n".join(lines)


@tool
def read_skills(skill_name: str):
    """Load and read full skill content."""
    skill_path = _get_skill_path(skill_name)
    try:
        return skill_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return f"Error: Skill {skill_name} does not exist."