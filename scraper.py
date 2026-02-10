import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import requests
from playwright.sync_api import sync_playwright

# =========================
# Scraping
# =========================
# Pega volumes tipo "#24". (Depois a gente expande pra Panini.)
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

# =========================
# Notion API
# =========================
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}" if NOTION_TOKEN else "",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

def notion_enabled() -> bool:
    return bool(NOTION_TOKEN and DATABASE_ID)

def notion_query_page_id_by_title(title: str) -> Optional[str]:
    """Busca a página (linha) na database onde a propriedade 'Mangá' (title) == title."""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "Mangá",
            "title": {"equals": title}
        }
    }
    r = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0]["id"] if results else None

def notion_update_page(page_id: str, props: Dict[str, Any]) -> None:
    url = f"https://api.notion.com/v1/pages/{page_id}"
    r = requests.patch(url, headers=NOTION_HEADERS, json={"properties": props}, timeout=30)
    r.raise_for_status()

def to_rich_text(s: str) -> Dict[str, Any]:
    return {"rich_text": [{"text": {"content": s}}]}

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

        # Atualiza Notion (se secrets estiverem configurados)
        if notion_enabled():
            page_id = notion_query_page_id_by_title(nome)
            if not page_id:
                print(f"⚠️ Notion: não achei o mangá '{nome}' na database (coluna Mangá).")
            else:
                props = {
                    "URL": {"url": url},
                    "Tenho": to_rich_text(", ".join(map(str, tenho))),
                    "Existentes": to_rich_text(", ".join(map(str, existentes))),
                    "Faltantes": to_rich_text(", ".join(map(str, faltantes))),
                    "Qtde faltante": {"number": len(faltantes)},
                    "Última verificação": {"date": {"start": datetime.utcnow().isoformat()}},
                    "Status": {"select": {"name": "OK" if len(faltantes) == 0 else "❌ Faltam volumes"}},
                }
                notion_update_page(page_id, props)

    # Arquivos no repo (continua como antes)
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
