import shutil
from pathlib import Path
import subprocess

SRC = Path(__file__).parent.parent / "skills"
DEST = Path.home() / ".hermes/skills/custom"

SCRIPTS_SRC = Path(__file__).parent  # scripts/ folder
SCRIPTS_DEST = Path.home() / ".hermes/scripts"

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
        if agent_dir.name.startswith('.') or not agent_dir.is_dir():
            continue
        if agent_dir.name == "__pycache__":
            continue

        for py_file in agent_dir.rglob("*.py"):
            if "__pycache__" in py_file.parts:
                continue
            rel_path = py_file.relative_to(agent_dir)
            dest_file = SCRIPTS_DEST / agent_dir.name / rel_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(py_file, dest_file)
            print(f"   ✅ Script deployed: {agent_dir.name}/{rel_path} → {dest_file}")

def main():
    print("🚀 Starting deploy...")
    install_requirement()
    deploy_skills()
    deploy_scripts()
    print("🎉 Deploy complete!")

if __name__ == "__main__":
    main()