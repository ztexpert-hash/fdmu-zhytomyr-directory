from pathlib import Path
import sys

path = Path("index.html")
if not path.exists():
    print("ПОМИЛКА: у цій папці немає index.html")
    sys.exit(1)

text = path.read_text(encoding="utf-8")

# Захист від помилкового файлу: патч тільки для діючого довідника ФДМУ, не для старого калькулятора.
required = [
    "Довідник зареєстрованих оцінок ФДМУ",
    "analytics-table",
    "versionTop"
]
missing = [x for x in required if x not in text]
if missing:
    print("ПОМИЛКА: це не поточний index.html довідника ФДМУ.")
    print("Не знайдено:", ", ".join(missing))
    sys.exit(1)

backup = Path("index_backup_before_v7_3.html")
if not backup.exists():
    backup.write_text(text, encoding="utf-8")

# Версія сайту
text = text.replace("const APP_VERSION='7.2';", "const APP_VERSION='7.3';")
text = text.replace('const APP_VERSION="7.2";', 'const APP_VERSION="7.3";')
text = text.replace('id="versionTop">v7.2<', 'id="versionTop">v7.3<')
text = text.replace('id="st-version">7.2<', 'id="st-version">7.3<')
text = text.replace('Версія сайту: 7.2', 'Версія сайту: 7.3')
text = text.replace('v7.2 Auto Update', 'v7.3 Auto Update')

# Центрування числових колонок у таблицях аналітики.
old_variants = [
    ".analytics-table .num{text-align:right;font-weight:900;white-space:nowrap}",
    ".analytics-table .num{text-align:right;font-weight:900;white-space:nowrap}.analytics-table tr:last-child td{border-bottom:none}",
]
new_css = (
    ".analytics-table th:not(:first-child),.analytics-table td.num{text-align:center}"
    ".analytics-table .num{font-weight:900;white-space:nowrap}"
)

changed = False
for old in old_variants:
    if old in text:
        if old.endswith(".analytics-table tr:last-child td{border-bottom:none}"):
            text = text.replace(old, new_css + ".analytics-table tr:last-child td{border-bottom:none}")
        else:
            text = text.replace(old, new_css)
        changed = True

# Якщо CSS уже інший — додаємо правило перед </style>, без заміни.
if not changed and ".analytics-table td.num{text-align:center}" not in text:
    insert = """
/* v7.3: вирівнювання числових колонок аналітичних таблиць */
.analytics-table th:not(:first-child),
.analytics-table td.num{
  text-align:center;
}
.analytics-table .num{
  font-weight:900;
  white-space:nowrap;
}
"""
    text = text.replace("</style>", insert + "\n</style>")
    changed = True

path.write_text(text, encoding="utf-8")

print("ГОТОВО: index.html оновлено до v7.3")
print("Змінено тільки вирівнювання числових колонок аналітичних таблиць.")
print("Створено резервну копію: index_backup_before_v7_3.html")
