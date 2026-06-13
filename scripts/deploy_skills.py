import shutil
import os
from pathlib import Path

SRC = Path("skills")
DEST = Path.home() / ".hermes/skills/custom"

def deploy():
    DEST.mkdir(parents=True, exist_ok=True)

    for skill_file in SRC.rglob("*.md"):
        target = DEST / skill_file.relative_to(SRC)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_file, target)
        print(f"✅ Deployed: {skill_file} → {target}")

    # 部署 Python scripts
    scripts_src = Path("scripts/agent")
    scripts_dest = Path.home() / ".hermes/scripts"
    if scripts_src.exists():
        scripts_dest.mkdir(exist_ok=True)
        for py in scripts_src.glob("*.py"):
            shutil.copy2(py, scripts_dest / py.name)
            print(f"✅ Script: {py.name}")

    print("🚀 Deploy complete")

if __name__ == "__main__":
    deploy()