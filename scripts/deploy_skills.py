import shutil
import subprocess
from pathlib import Path

# ── 路徑設定 ──────────────────────────────────────────────
SRC         = Path(__file__).parent.parent / "skills"
SCRIPTS_SRC = Path(__file__).parent  # scripts/ folder

# Skills → host 機直接寫
SKILLS_DEST = Path("/root/.hermes/skills/custom")

# Scripts → docker cp 入 container
CONTAINER              = "hermes-agent-c05p-hermes-agent-1"
CONTAINER_SCRIPTS_DEST = "/opt/hermes/scripts"

IGNORE_PATTERNS = shutil.ignore_patterns(
    '__pycache__', '*.pyc', '.git', 'pgdata',
    '.venv', 'venv', 'node_modules', '.DS_Store'
)


# ── Helper ────────────────────────────────────────────────
def docker_cp(src: Path, dest: str):
    """Copy src (file or folder) into container."""
    subprocess.run(
        ["docker", "cp", str(src), f"{CONTAINER}:{dest}"],
        check=True
    )

def docker_exec(cmd: str):
    """Run a shell command inside container."""
    subprocess.run(
        ["docker", "exec", CONTAINER, "sh", "-c", cmd],
        check=True
    )


# ── Install Requirements ──────────────────────────────────
def install_requirements():
    requirements_file =  Path(__file__).parent.parent / "requirements.txt"
    if requirements_file.exists():
        print(f"📦 Installing requirements...")
        docker_cp(requirements_file, "/tmp/requirements.txt")
        docker_exec("pip install -r /tmp/requirements.txt")
    else:
        print("⚠️  No requirements.txt found, skipping.")


# ── Deploy Skills → host 機 ───────────────────────────────
def deploy_skills():
    print("\n📂 Deploying skills to host...")
    SKILLS_DEST.mkdir(parents=True, exist_ok=True)

    for category_dir in SRC.iterdir():
        if category_dir.name.startswith('.') or not category_dir.is_dir():
            continue

        dest_category = SKILLS_DEST / category_dir.name

        # 清走舊版本（解決 rename 殘留問題）
        if dest_category.exists():
            shutil.rmtree(dest_category)

        for skill_dir in category_dir.iterdir():
            if skill_dir.name.startswith('.') or not skill_dir.is_dir():
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            target = dest_category / skill_dir.name
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(skill_md, target / "SKILL.md")
            print(f"  ✅ Skill: {category_dir.name}/{skill_dir.name}")


# ── Deploy Scripts → container ────────────────────────────
def deploy_scripts():
    print("\n📂 Deploying scripts to container...")

    for agent_dir in SCRIPTS_SRC.iterdir():
        if agent_dir.name.startswith('.') or agent_dir.name == '__pycache__':
            continue
        if not agent_dir.is_dir():
            continue

        # 清走 container 舊版本（解決 rename 殘留問題）
        docker_exec(f"rm -rf {CONTAINER_SCRIPTS_DEST}/{agent_dir.name}")

        # Copy 整個 agent 資料夾（包括所有子層）入 container
        docker_cp(agent_dir, CONTAINER_SCRIPTS_DEST)
        print(f"  ✅ Agent: {agent_dir.name}")


# ── Main ──────────────────────────────────────────────────
def main():
    print("🚀 Starting deploy...")
    print(f"   Skills  → host: {SKILLS_DEST}")
    print(f"   Scripts → container: {CONTAINER}:{CONTAINER_SCRIPTS_DEST}")

    install_requirements()
    deploy_skills()
    deploy_scripts()

    print("\n🎉 Deploy complete!")


if __name__ == "__main__":
    main()