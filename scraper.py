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
        context = browser.new_context()
        page = context.new_page()

        # Acelera MUITO: não baixa imagens nem fontes
        page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ["image", "font"]
            else route.continue_()
        )

        last_err = None
        for attempt in range(3):
            try:
                # 'commit' não fica esperando o site "terminar"
                page.goto(url, wait_until="commit", timeout=180000)

                # Espera o body existir (critério simples e confiável)
                page.wait_for_selector("body", timeout=180000)

                # dá um tempo pro conteúdo renderizar
                page.wait_for_timeout(2500)

                text = page.inner_text("body")
                nums = [int(m.group(1)) for m in NUM_RE.finditer(text)]

                browser.close()
                return unique_sorted(nums)

            except Exception as e:
                last_err = e
                page.wait_for_timeout(2000)

        browser.close()
        raise last_err

# =========================
# Notion API
# =========================
def normalize_notion_id(raw: str) -> str:
    raw = (raw or "").strip()
    raw = raw.replace("-", "")
    # Se vier com 32 chars, transforma em UUID com hífens (8-4-4-4-12)
    if len(raw) == 32:
        return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"
    return raw

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = normalize_notion_id(os.getenv("NOTION_DATABASE_ID"))

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}" if NOTION_TOKEN else "",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

def notion_enabled() -> bool:
    return bool(NOTION_TOKEN and DATABASE_ID)

def notion_query_page_id_by_url(url_value: str) -> Optional[str]:
    """Busca a página (linha) na database onde a propriedade 'URL' == url_value."""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "URL",
            "url": {
                "equals": url_value
            }
        }
    }
    r = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=30)

    if not r.ok:
        print("Notion error status:", r.status_code)
        print("Notion error body:", r.text)

    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0]["id"] if results else None

def notion_update_page(page_id: str, props: Dict[str, Any]) -> None:
    url = f"https://api.notion.com/v1/pages/{page_id}"
    r = requests.patch(url, headers=NOTION_HEADERS, json={"properties": props}, timeout=30)

    if not r.ok:
        print("Notion update error status:", r.status_code)
        print("Notion update error body:", r.text)

    r.raise_for_status()

def to_rich_text(s: str) -> Dict[str, Any]:
    return {"rich_text": [{"text": {"content": s}}]}

def format_ranges(nums: List[int]) -> str:
    """Converte [1,2,3,7,8,10] -> '1–3, 7–8, 10' """
    if not nums:
        return ""
    nums = sorted(nums)
    parts: List[str] = []
    start = prev = nums[0]

    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(f"{start}" if start == prev else f"{start}–{prev}")
        start = prev = n

    parts.append(f"{start}" if start == prev else f"{start}–{prev}")
    return ", ".join(parts)

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
            page_id = notion_query_page_id_by_url(url)
            if not page_id:
                print(f"⚠️ Notion: não achei nenhuma linha com URL == '{url}' (coluna URL).")
            else:
                status_txt = "OK" if len(faltantes) == 0 else "❌ Faltam volumes"
                faltantes_txt = format_ranges(faltantes)

                props = {
                    "URL": {"url": url},

                    # ✅ Sua coluna "Faltantes" é número (quantidade)
                    "Faltantes": {"number": len(faltantes)},

                    # ✅ mantém também (se existir como Number)
                    "Qtde faltante": {"number": len(faltantes)},

                    # ✅ NOVA coluna com a lista de volumes faltantes (texto)
                    "Volumes faltantes": to_rich_text(faltantes_txt),

                    # ✅ Texto (rich_text)
                    "Última verificação": to_rich_text(datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")),
                    "Status": to_rich_text(status_txt),

                    # Texto (se existirem)
                    "Tenho": to_rich_text(", ".join(map(str, tenho))),
                    "Existentes": to_rich_text(", ".join(map(str, existentes))),
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
