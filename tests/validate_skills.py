import os
import yaml

SKILLS_DIR = "skills"

def test_all_skills():
    errors = []
    for root, dirs, files in os.walk(SKILLS_DIR):
        for f in files:
            if f.endswith(".md"):
                path = os.path.join(root, f)
                with open(path) as fh:
                    content = fh.read()

                # 檢查有無 YAML frontmatter
                if not content.startswith("---"):
                    errors.append(f"{path}: 缺少 YAML frontmatter")
                    continue

                # 解析 frontmatter
                try:
                    parts = content.split("---", 2)
                    meta = yaml.safe_load(parts[1])
                    assert "name" in meta, f"{path}: 缺少 name"
                    assert "description" in meta, f"{path}: 缺少 description"
                except Exception as e:
                    errors.append(f"{path}: {e}")

    if errors:
        for e in errors:
            print(f"❌ {e}")
        raise SystemExit(1)

    print(f"✅ 所有 skills 驗證通過")

if __name__ == "__main__":
    test_all_skills()