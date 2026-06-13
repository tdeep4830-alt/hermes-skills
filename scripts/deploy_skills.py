import shutil
from pathlib import Path

SRC = Path(__file__).parent.parent / "skills"
DEST = Path.home() / ".hermes/skills/custom"

def deploy():
    DEST.mkdir(parents=True, exist_ok=True)

    skill_files = list(SRC.rglob("*.md"))

    if not skill_files:
        print("⚠️  未有 skills，跳過部署")
        return

    for skill_file in skill_files:
        target = DEST / skill_file.relative_to(SRC)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_file, target)
        print(f"✅ Deployed: {skill_file.name}")

    # 部署 Python scripts
    scripts_src = Path(__file__).parent / "agent"
    scripts_dest = Path.home() / ".hermes/scripts"
    if scripts_src.exists():
        scripts_dest.mkdir(exist_ok=True)
        for py in scripts_src.glob("*.py"):
            shutil.copy2(py, scripts_dest / py.name)
            print(f"✅ Script: {py.name}")

    print("🚀 Deploy complete")

if __name__ == "__main__":
    deploy()