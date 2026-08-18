
import shutil
import subprocess
from pathlib import Path

# ── 路徑設定 ──────────────────────────────────────────────
SRC         = Path(__file__).parent.parent / "skills"
SCRIPTS_SRC = Path(__file__).parent  # scripts/ folder

# Skills → host 機直接寫（agent 讀 SKILL.md 定義用）
SKILLS_DEST = Path("/root/.hermes/skills/custom")

# Scripts → host 機直接寫（俾 code-execution sandbox 用 docker_volumes 讀到）
SCRIPTS_HOST_DEST = Path("/root/.hermes/scripts")

# Scripts → docker cp 入 Flask API container（HTTP endpoint 類 skill 專用）
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
    requirements_file = Path(__file__).parent.parent / "requirements.txt"
    if requirements_file.exists():
        print("📦 Installing requirements...")
        docker_cp(requirements_file, "/tmp/requirements.txt")
        docker_exec("/opt/hermes/.venv/bin/python3 -m pip install -r /tmp/requirements.txt")
    else:
        print("⚠️  No requirements.txt found, skipping.")


# ── Deploy Skills → host ───────────────────────────────────
def deploy_skills():
    print("\n📂 Deploying skills to host...")
    SKILLS_DEST.mkdir(parents=True, exist_ok=True)

    for category_dir in SRC.iterdir():
        if category_dir.name.startswith('.') or not category_dir.is_dir():
            continue

        dest_category = SKILLS_DEST / category_dir.name
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


# ── Deploy Scripts → host（sandbox docker_volumes 用）──────
def deploy_scripts_to_host():
    print("\n📂 Deploying scripts to host (for sandbox mount)...")
    SCRIPTS_HOST_DEST.mkdir(parents=True, exist_ok=True)

    for agent_dir in SCRIPTS_SRC.iterdir():
        if agent_dir.name.startswith('.') or agent_dir.name == '__pycache__':
            continue
        if not agent_dir.is_dir():
            continue

        dest = SCRIPTS_HOST_DEST / agent_dir.name
        if dest.exists():
            shutil.rmtree(dest)

        shutil.copytree(agent_dir, dest, ignore=IGNORE_PATTERNS)
        print(f"  ✅ Host copy: {agent_dir.name}")


# ── Deploy Scripts → Flask API container ───────────────────
def deploy_scripts_to_container():
    print("\n📂 Deploying scripts to Flask API container...")

    for agent_dir in SCRIPTS_SRC.iterdir():
        if agent_dir.name.startswith('.') or agent_dir.name == '__pycache__':
            continue
        if not agent_dir.is_dir():
            continue

        docker_exec(f"rm -rf {CONTAINER_SCRIPTS_DEST}/{agent_dir.name}")
        docker_cp(agent_dir, CONTAINER_SCRIPTS_DEST)
        print(f"  ✅ Container copy: {agent_dir.name}")


def deploy_env():
    env_file = SCRIPTS_SRC / "financial_assist_agent" / "financial_news_article" / ".env"
    if env_file.exists():
        docker_cp(env_file, f"{CONTAINER_SCRIPTS_DEST}/financial_assist_agent/financial_news_article/.env")
        print("✅ .env deployed to container")


# ── Recycle sandbox container 等新 mount 生效 ───────────────
def restart_sandbox_if_running():
    print("\n🔄 Checking code-execution sandbox...")
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", "label=hermes-agent=1",
         "--format", "{{.Names}}"],
        capture_output=True, text=True
    )
    names = [n for n in result.stdout.splitlines() if n.strip()]
    if not names:
        print("  ℹ️  No running sandbox found, nothing to recycle.")
        return
    for name in names:
        print(f"  🔄 Removing sandbox container: {name}")
        subprocess.run(["docker", "rm", "-f", name], check=True)
    print("  ✅ Sandbox will be recreated with latest docker_volumes on next execute_code call.")


# ── Main ──────────────────────────────────────────────────
def main():
    print("🚀 Starting deploy...")
    print(f"   Skills  → host:                 {SKILLS_DEST}")
    print(f"   Scripts → host (mount 入兩個 container): {SCRIPTS_HOST_DEST}")

    install_requirements()   # 呢個保留 —— 淨係裝 container 嘅 venv dependency,同 script 位置無關
    deploy_skills()
    deploy_scripts_to_host()
    restart_sandbox_if_running()

    print("\n🎉 Deploy complete!")