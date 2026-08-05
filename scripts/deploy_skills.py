import shutil
from pathlib import Path
import subprocess

SRC = Path(__file__).parent.parent / "skills"
DEST = Path.home() / ".hermes/skills/custom"

SCRIPTS_SRC = Path(__file__).parent  # scripts/ folder
SCRIPTS_DEST = Path.home() / ".hermes/scripts"

IGNORE_PATTERNS = shutil.ignore_patterns(
    '__pycache__', '*.pyc', '.git', 'pgdata', '.venv', 'venv', 'node_modules', '.DS_Store'
)

def install_requirement():
    requirements_file = SCRIPTS_SRC / "requirements.txt"
    if requirements_file.exists():
        print(f"📦 Installing requirements from {requirements_file}...")
        subprocess.run(["pip", "install", "-r", str(requirements_file)], check=True)
    else:
        print("⚠️ No requirements.txt found.")

def deploy_skills():
    DEST.mkdir(parents=True, exist_ok=True)

    for category_dir in SRC.iterdir():
        if category_dir.name.startswith('.') or not category_dir.is_dir():
            continue

        for skill_dir in category_dir.iterdir():
            if skill_dir.name.startswith('.') or not skill_dir.is_dir():
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            target = DEST / category_dir.name / skill_dir.name
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(skill_md, target / "SKILL.md")
            print(f"✅ Skill deployed: {category_dir.name}/{skill_dir.name}")



def deploy_scripts():
    SCRIPTS_DEST.mkdir(parents=True, exist_ok=True)

    for agent_dir in SCRIPTS_SRC.iterdir():
        if agent_dir.name.startswith('.') or agent_dir.name == '__pycache__':
            continue
        if not agent_dir.is_dir():
            continue

        dest_dir = SCRIPTS_DEST / agent_dir.name
        shutil.copytree(agent_dir, dest_dir, ignore=IGNORE_PATTERNS, dirs_exist_ok=True)
        print(f"✅ Agent deployed: {agent_dir.name} → {dest_dir}")

def main():
    print("🚀 Starting deploy...")
    install_requirement()
    deploy_skills()
    deploy_scripts()
    print("🎉 Deploy complete!")

if __name__ == "__main__":
    main()