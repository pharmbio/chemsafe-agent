
def read_skills(skill_name: str):
    """Load and read skills content"""
    try:
        with open(f"core/skills/{skill_name}/{skill_name}.md", "r", encoding="utf-8") as file:
            skill_content = file.read().strip()
        return skill_content
    except FileNotFoundError:
        return f"Error: Skill {skill_name} does not not exist."
