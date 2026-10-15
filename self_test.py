"""Offline tests for the Liyana Shop catalog, Persian search, and intent matching."""
import ast
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
source = (ROOT / 'bot.py').read_text(encoding='utf-8')
tree = ast.parse(source)
needed = {
    'normalize_fa', 'search_products', 'product_name_matches',
    'intent_families', 'intent_products'
}
selected = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in needed:
        selected.append(node)
    elif isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == 'PRODUCT_INTENTS'
        for target in node.targets
    ):
        selected.append(node)

catalog = json.loads((ROOT / 'products.json').read_text(encoding='utf-8'))
namespace = {'re': re, 'DATA': catalog}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROOT / 'bot.py'), 'exec'), namespace)
normalize = namespace['normalize_fa']
search = namespace['search_products']
intent_products = namespace['intent_products']

assert isinstance(catalog, list) and len(catalog) == 381, f'unexpected catalog size: {len(catalog)}'
ids = [str(p.get('id')) for p in catalog]
assert len(ids) == len(set(ids)), 'duplicate product IDs'
assert all(p.get('name') for p in catalog), 'product missing name'
assert all(p.get('published', True) for p in search('ریمل', 8)), 'unpublished product returned'

# Every exact product name must find that exact catalog item, not a sibling.
for p in catalog:
    results = search(p['name'], 20)
    assert results and any(str(x.get('id')) == str(p['id']) for x in results), f"exact-name failure: {p['name']}"

for query in ('ریمل', 'ضدافتاب', 'ضد آفتاب', 'بادی اسپلش', 'رژلب', 'کرمپودر', 'مداد ابرو'):
    assert search(query, 8), f'no results for common query: {query}'
assert normalize('ضدافتاب') == normalize('ضد آفتاب')

# Full-message intent extraction should return products from multiple requested families.
combined = intent_products('برای خرید یه ضد آفتاب و ریمل می‌خوام', 8)
assert combined, 'intent extraction returned no products'
combined_names = [normalize(p.get('name', '')) for p in combined]
assert any('ضد آفتاب' in n or 'ضدافتاب' in n for n in combined_names), 'sunscreen intent not represented'
assert any('ریمل' in n for n in combined_names), 'mascara intent not represented'

# All catalog records should have valid product links for direct product buttons.
assert all(str(p.get('product_url', '')).startswith(('https://', 'http://')) for p in catalog), 'invalid/missing product URL'

# CSV is auxiliary and must not contain IDs missing from the catalog.
csv_path = ROOT / 'research_queue.csv'
if csv_path.exists():
    with csv_path.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    csv_ids = {str(row.get('شناسه', '')).strip() for row in rows if str(row.get('شناسه', '')).strip()}
    assert csv_ids <= set(ids), f'CSV contains IDs not in catalog: {sorted(csv_ids - set(ids))[:5]}'
    print(f'OK: CSV rows={len(rows)}; all CSV IDs are present in catalog.')

print(f'OK: {len(catalog)} products; every exact name, common spelling variants, multi-intent matching, and product URLs passed.')
