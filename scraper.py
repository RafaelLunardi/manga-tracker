import json
import re
from pathlib import Path
from typing import List, Dict, Any
from playwright.sync_api import sync_playwright

NUM_RE = re.compile(r"#\s*(\d+)\b")

def unique_sorted(nums: List[int]) -> List[int]:
    return sorted(set(nums))

def fetch_numbers(url: str) -> List[int]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        text = page.inner_text("body")
        browser.close()

    nums = [int(m.group(1)) for m in NUM_RE.finditer(text)]
    return unique_sorted(nums)

def main() -> None:
    mangas = json.loads(Path("mangas.json").read_text(encoding="utf-8"))
    results: Dict[str, Any] = {}

    for m in mangas:
        nome = m["nome"]
        url = m["url"]
        tenho = unique_sorted([int(x) for x in m.get("tenho", [])])

        existentes = fetch_numbers(url)
        faltantes = [v for v in existentes if v not in set(tenho)]

        results[nome] = {
            "url": url,
            "tenho": tenho,
            "existentes": existentes,
            "faltantes": faltantes,
            "faltam_qtd": len(faltantes),
        }

    Path("results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    lines = ["# Manga Tracker\n"]
    for nome, r in results.items():
        lines.append(f"## {nome}")
        lines.append(f"- Tenho: {r['tenho']}")
        lines.append(f"- Existentes na página: {r['existentes']}")
        lines.append(f"- ❌ Faltantes ({r['faltam_qtd']}): {r['faltantes']}\n")
    Path("results.md").write_text("\n".join(lines), encoding="utf-8")

if __name__ == "__main__":
    main()
