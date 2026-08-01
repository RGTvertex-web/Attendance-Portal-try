import os
import glob

def fix_search():
    base_dir = "c:/Users/Dhairyakant/Desktop/RGTVertex/intern_portal/templates"
    count = 0
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.endswith(".html"):
                path = os.path.join(root, file)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                if "{{ search }}" in content:
                    content = content.replace("{{ search }}", "{{ search | default('') }}")
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)
                    count += 1
                    print(f"Fixed {path}")
    print(f"Total files fixed: {count}")

if __name__ == "__main__":
    fix_search()
