import shutil
import subprocess
from pathlib import Path

# ── 路徑設定 ──────────────────────────────────────────────
SRC         = Path(__file__).parent.parent / "skills"
SCRIPTS_SRC = Path(__file__).parent  # scripts/ folder

CONTAINER              = "hermes-agent-c05p-hermes-agent-1"
CONTAINER_SKILLS_DEST  = "/opt/hermes/optional-skills"
CONTAINER_SCRIPTS_DEST = "/opt/hermes/scripts"

IGNORE_PATTERNS = shutil.ignore_patterns(
    '__pycache__', '*.pyc', '.git', 'pgdata',
    '.venv', 'venv', 'node_modules', '.DS_Store'
)

TMP_SKILLS  = Path("/tmp/hermes_deploy/skills")
TMP_SCRIPTS = Path("/tmp/hermes_deploy/scripts")


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
    requirements_file = SCRIPTS_SRC / "requirements.txt"
    if requirements_file.exists():
        print(f"📦 Installing requirements...")
        # Copy requirements.txt 入 container 再 pip install
        docker_cp(requirements_file, "/tmp/requirements.txt")
        docker_exec("pip install -r /tmp/requirements.txt")
    else:
        print("⚠️  No requirements.txt found, skipping.")


# ── Deploy Skills ─────────────────────────────────────────
def deploy_skills():
    print("\n📂 Deploying skills...")

    # 先清空 container 入面你嘅 custom skill 類別（避免殘留舊 rename 檔案）
    # 只清走同 SRC 入面同名嘅 category，唔動 Hermes 原有嘅 optional-skills
    for category_dir in SRC.iterdir():
        if category_dir.name.startswith('.') or not category_dir.is_dir():
            continue

        # 清走 container 對應 category
        docker_exec(f"rm -rf {CONTAINER_SKILLS_DEST}/{category_dir.name}")

        # 逐個 skill 複製
        for skill_dir in category_dir.iterdir():
            if skill_dir.name.startswith('.') or not skill_dir.is_dir():
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            # 先喺 container 建好目錄
            docker_exec(
                f"mkdir -p {CONTAINER_SKILLS_DEST}/{category_dir.name}/{skill_dir.name}"
            )

            # Copy SKILL.md 入去
            docker_cp(
                skill_md,
                f"{CONTAINER_SKILLS_DEST}/{category_dir.name}/{skill_dir.name}/SKILL.md"
            )
            print(f"  ✅ Skill: {category_dir.name}/{skill_dir.name}")


# ── Deploy Scripts ────────────────────────────────────────
def deploy_scripts():
    print("\n📂 Deploying scripts...")

    # 先將整個 scripts 資料夾 copy 去 /tmp 做暫存
    if TMP_SCRIPTS.exists():
        shutil.rmtree(TMP_SCRIPTS)
    TMP_SCRIPTS.mkdir(parents=True)

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
    print("🚀 Starting deploy to Docker container...")
    print(f"   Container: {CONTAINER}")

    install_requirements()
    deploy_skills()
    deploy_scripts()

    print("\n🎉 Deploy complete!")
    print(f"   Skills deployed to: {CONTAINER_SKILLS_DEST}")
    print(f"   Scripts deployed to: {CONTAINER_SCRIPTS_DEST}")


if __name__ == "__main__":
    main()