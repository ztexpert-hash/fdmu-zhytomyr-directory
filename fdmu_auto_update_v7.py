#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ФДМУ Житомир — Auto Update v7.8-beta
Архів з 01.01.2022 + робоча база останні 12 місяців + аналітика виконавців.
"""
from __future__ import annotations
import io, json, re, zipfile, shutil
from datetime import datetime, date, timedelta
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

try:
    import pandas as pd
except Exception:
    print('ПОМИЛКА: потрібен pandas. Встановіть: pip install pandas openpyxl')
    raise

APP_VERSION = '7.8-beta'
START_DATE = date(2022, 1, 1)
WORKING_DAYS = 365
SPFU_PAGE = 'https://www.spfu.gov.ua/ua/content/spf-estimate-basereport-dani-z-edinoi-bazi.html'
TARGET_PREFIX = 'Obekty_Zhytlovoi_neruxomosti'
ACCEPTED_STATUSES = {'ЗАРЕЄСТРОВАНО', 'ПЕРЕВІРЕНО'}
CITY_DISTRICTS = {'Богунський', 'Корольовський', 'Житомир (без району)', 'Оліївка новобудови'}
OLIIVKA_MIN_YEAR = 2018
BAD_ADDRESS_TOKENS = ['БЕРДИЧ','НОВОГРАД','ЗВЯГ','КОРОСТ','МАЛИН','ОВРУЧ','ЧУДНІВ','АНДРУШ','РАДОМИШЛ']
PRIORITY_FIELD_PATTERNS = ['матеріал','стін','стіни','тип буд','клас','стан','ремонт','ліфт','балкон','лодж','опал','перекрит','паркінг','гараж','поверховість','серія','новобуд','комунікац','санвуз','газ','вода']

ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / 'downloads_fdmu'
OUTPUT = ROOT / 'output_update'
PROCESSED = ROOT / 'processed_files.json'
SCHEMA = ROOT / 'fdmu_schema_columns.json'
ARCHIVE_JSON = ROOT / 'archive_calc_data.json'
CALC_JSON = ROOT / 'calc_data.json'
README = ROOT / 'README.txt'
INDEX = ROOT / 'index.html'
SW = ROOT / 'sw.js'


def read_json(path, default):
    try:
        p = Path(path)
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default
    except Exception:
        return default


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def fetch_text(url):
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0 FDMU-Zhytomyr-Updater/7.8-beta'})
    with urlopen(req, timeout=90) as r:
        raw = r.read()
    for enc in ('utf-8', 'windows-1251', 'cp1251'):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode('utf-8', errors='ignore')


def find_zip_links(html, base_url):
    hrefs = re.findall(r'href=["\']([^"\']+\.zip(?:\?[^"\']*)?)["\']', html, flags=re.I)
    links = []
    for h in hrefs:
        u = urljoin(base_url, h)
        if u not in links:
            links.append(u)
    return links


def parse_dates_from_text(text):
    s = str(text)
    dates = []
    for d, m, y in re.findall(r'(\d{1,2})[._-](\d{1,2})[._-](20\d{2})', s):
        try:
            dates.append(date(int(y), int(m), int(d)))
        except Exception:
            pass
    for y, m, d in re.findall(r'(20\d{2})[._-](\d{1,2})[._-](\d{1,2})', s):
        try:
            dates.append(date(int(y), int(m), int(d)))
        except Exception:
            pass
    # fallback: year only
    for y in re.findall(r'(20\d{2})', s):
        try:
            dates.append(date(int(y), 12, 31))
        except Exception:
            pass
    return dates


def link_is_from_2022_or_newer(url):
    dates = parse_dates_from_text(url)
    if not dates:
        return True  # якщо дату не видно в URL, перевіримо ZIP всередині
    return max(dates) >= START_DATE


def download_file(url, dst):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0 FDMU-Zhytomyr-Updater/7.8-beta'})
    with urlopen(req, timeout=240) as r, open(dst, 'wb') as f:
        shutil.copyfileobj(r, f)
    return dst


def find_target_csv_in_zip(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        candidates = []
        for n in z.namelist():
            base = Path(n).name
            if TARGET_PREFIX.lower() in base.lower() and Path(base).suffix.lower() in ('.csv', '.txt'):
                # відсіяти старі періоди до 2022, якщо дата є в назві CSV
                ds = parse_dates_from_text(base)
                if ds and max(ds) < START_DATE:
                    continue
                candidates.append(n)
        if not candidates:
            return None, None
        candidates.sort(key=lambda n: z.getinfo(n).file_size, reverse=True)
        name = candidates[0]
        return Path(name).name, z.read(name)


def read_csv_bytes(raw):
    last = None
    for enc in ('utf-8-sig', 'cp1251', 'windows-1251', 'utf-8'):
        try:
            return pd.read_csv(io.BytesIO(raw), sep=';', encoding=enc, dtype=str, keep_default_na=False)
        except Exception as e:
            last = e
    raise RuntimeError(f'Не вдалося прочитати CSV: {last}')


def norm_header(h):
    return re.sub(r'\s+', ' ', str(h or '').strip().lower())


def find_col(cols, patterns, required=True):
    ncols = [(c, norm_header(c)) for c in cols]
    for p in patterns:
        p = p.lower()
        for orig, n in ncols:
            if p in n:
                return orig
    if required:
        raise KeyError(f'Не знайдена колонка: {patterns}')
    return None


def norm_status(v):
    return re.sub(r'\s+', ' ', str(v or '').strip().upper())


def is_oliivka_town(town):
    t = re.sub(r'\s+', ' ', str(town or '').strip().upper())
    return any(x in t for x in ['ОЛІЇВК', 'ОЛИЕВК', 'ОЛІЕВК'])

def norm_district(town, year=None):
    t = re.sub(r'\s+', ' ', str(town or '').strip().upper())
    if 'БОГУНСЬК' in t:
        return 'Богунський'
    if 'КОРОЛЬОВСЬК' in t or 'КОРОЛЕВ' in t:
        return 'Корольовський'
    if is_oliivka_town(town):
        return 'Оліївка новобудови' if year and int(year) >= OLIIVKA_MIN_YEAR else None
    if 'ЖИТОМИРСЬК' in t and 'РАЙОН' in t:
        return None
    if re.search(r'(^|\b)(М\.?\s*)?ЖИТОМИР(\b|$)', t):
        return 'Житомир (без району)'
    return None


def to_float(v):
    s = str(v or '').replace('\xa0', ' ').replace(' ', '').replace(',', '.').strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def parse_int(v):
    m = re.search(r'\d{1,4}', str(v or ''))
    return int(m.group(0)) if m else None


def room_cat(area):
    if area < 40:
        return 1
    if area < 65:
        return 2
    if area < 90:
        return 3
    return 4


def is_dorm(type_text, address):
    s = (str(type_text or '') + ' ' + str(address or '')).upper()
    return any(x in s for x in ['ГУРТОЖ', 'КІМНАТ', 'КОМУНАЛ'])


def category_name(area, obj_type):
    if obj_type != 'Квартира':
        return 'Кімната/гуртожиток'
    return {1:'1-кімнатна', 2:'2-кімнатна', 3:'3-кімнатна', 4:'4-кімнатна'}[room_cat(area)]


def parse_executor(sod):
    s = re.sub(r'\s+', ' ', str(sod or '').strip())
    if not s:
        return ''
    m = re.match(r'^\d{6,12}\s*,\s*(.+)$', s)
    return m.group(1).strip() if m else s


def normalize_address(addr):
    s = str(addr or '').upper()
    for x in ['ВУЛИЦЯ','ВУЛ.','ВУЛ ','ПРОСПЕКТ','ПРОСП.','ПР-Т','ПЛОЩА','ПЛ.','ПРОВУЛОК','ПРОВ.','ПРОЇЗД','БУЛЬВАР','БУЛ.']:
        s = s.replace(x, ' ')
    s = re.sub(r'[^А-ЯІЇЄҐA-Z0-9\s\-/]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    synonyms = {
        'МИХАЙЛА ГРУШЕВСЬКОГО':'ГРУШЕВСЬКОГО',
        'ГРУШЕВСЬКОГО МИХАЙЛА':'ГРУШЕВСЬКОГО',
        'ІВАНА МАЗЕПИ':'МАЗЕПИ',
        'МАЗЕПИ ІВАНА':'МАЗЕПИ',
        'В БЕРДИЧІВСЬКА':'ВЕЛИКА БЕРДИЧІВСЬКА',
        'В. БЕРДИЧІВСЬКА':'ВЕЛИКА БЕРДИЧІВСЬКА',
        'НЕБЕСНОЇ СОТНИ':'НЕБЕСНОЇ СОТНІ',
    }
    for k, v in synonyms.items():
        s = s.replace(k, v)
    return re.sub(r'\s+', ' ', s).strip()


def valid_city_address(addr):
    ad = str(addr or '').upper()
    return not any(tok in ad for tok in BAD_ADDRESS_TOKENS)


def parse_date_value(v):
    s = str(v or '').strip()
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except Exception:
            pass
    m = re.search(r'(\d{1,2})[._/-](\d{1,2})[._/-](20\d{2})', s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except Exception:
            return None
    return None


def record_key(r):
    return '|'.join([
        str(r.get('ад_норм') or normalize_address(r.get('ад', ''))),
        str(round(float(r.get('пл') or 0), 1)),
        str(int(round(float(r.get('в') or 0)))),
        str(r.get('дата') or ''),
        str(r.get('к') or '')
    ])


def extract_records(df):
    cols = list(df.columns)
    col_region = find_col(cols, ['Регіон'])
    col_town = find_col(cols, ['Населений пункт'])
    col_status = find_col(cols, ['Статус'])
    col_type = find_col(cols, ['Вид об', 'нерухомості'])
    col_area = find_col(cols, ['Загальна площа'])
    col_floor = find_col(cols, ['Поверх'], False)
    col_year = find_col(cols, ['введення', 'Рік'], False)
    col_value = find_col(cols, ['Оціночна вартість об', 'Вартість'])
    col_street = find_col(cols, ['Вулиця'], False)
    col_street_type = find_col(cols, ['Тип вулиці'], False)
    col_date = find_col(cols, ['Дата оцінки', 'Дата реєстрації', 'Дата'], False)
    col_sod = find_col(cols, ['СОД', 'Виконавець'], False)

    status_counts = {}
    records = []
    region_count = 0
    raw_city_count = 0
    date_skipped = 0
    status_included = {'ЗАРЕЄСТРОВАНО': 0, 'ПЕРЕВІРЕНО': 0}

    for _, row in df.iterrows():
        status = norm_status(row.get(col_status, ''))
        status_counts[status] = status_counts.get(status, 0) + 1
        if status not in ACCEPTED_STATUSES:
            continue
        status_included[status] = status_included.get(status, 0) + 1
        if 'ЖИТОМИР' not in str(row.get(col_region, '')).upper():
            continue
        region_count += 1
        year = parse_int(row.get(col_year, '')) if col_year else None
        district = norm_district(row.get(col_town, ''), year)
        if not district:
            continue
        raw_city_count += 1
        stype = str(row.get(col_street_type, '')) if col_street_type else ''
        street = str(row.get(col_street, '')) if col_street else ''
        address = re.sub(r'\s+', ' ', (stype + ' ' + street).strip() or street).strip()
        if not valid_city_address(address):
            continue
        area = to_float(row.get(col_area))
        value = to_float(row.get(col_value))
        if not area or not value or area <= 5:
            continue
        ppm = value / area
        if ppm < 3000 or ppm > 80000:
            continue
        dstr = str(row.get(col_date, '')) if col_date else ''
        dval = parse_date_value(dstr)
        if dval and dval < START_DATE:
            date_skipped += 1
            continue
        type_text = str(row.get(col_type, ''))
        obj_type = 'Кімната/гуртожиток' if is_dorm(type_text, address) else 'Квартира'
        rec = {
            'р': district,
            'к': room_cat(area),
            'пл': round(float(area), 1),
            'пов': parse_int(row.get(col_floor, '')) if col_floor else None,
            'пх': None,
            'рік': year,
            'цкв': round(float(ppm)),
            'в': round(float(value)),
            'ад': address[:90],
            'тип': obj_type,
            'категорія': category_name(area, obj_type),
            'дата': dstr,
            'вик': parse_executor(row.get(col_sod, '')) if col_sod else '',
            'файл': '',
        }
        rec['ад_норм'] = normalize_address(rec['ад'])
        records.append(rec)

    return records, {
        'status_counts': status_counts,
        'status_included': status_included,
        'zhytomyr_region_accepted': region_count,
        'raw_city_count': raw_city_count,
        'date_skipped_before_2022': date_skipped,
    }, cols


def detect_new_fields(cols):
    old = read_json(SCHEMA, {'columns': []})
    old_cols = set(old.get('columns') or [])
    new_cols = [c for c in cols if c not in old_cols]
    priority = [c for c in new_cols if any(p in norm_header(c) for p in PRIORITY_FIELD_PATTERNS)]
    write_json(SCHEMA, {'updated': datetime.now().isoformat(timespec='seconds'), 'columns': cols})
    return {'new_columns': new_cols, 'priority_columns': priority, 'previous_count': len(old_cols), 'current_count': len(cols)}


def median(vals):
    vals = sorted([float(v) for v in vals if v is not None])
    if not vals:
        return 0
    n = len(vals)
    m = n // 2
    return round(vals[m] if n % 2 else (vals[m-1] + vals[m]) / 2)


def compute_stats(records):
    ppms = [r['цкв'] for r in records if r.get('цкв')]
    districts = {}
    ex = set()
    dates = []
    for r in records:
        districts[r['р']] = districts.get(r['р'], 0) + 1
        if r.get('вик'):
            ex.add(r['вик'])
        dv = parse_date_value(r.get('дата'))
        if dv:
            dates.append(dv)
    dorms = sum(1 for r in records if r.get('тип') == 'Кімната/гуртожиток')
    return {
        'apartments': len(records) - dorms,
        'dorms': dorms,
        'districts': districts,
        'executors': len(ex),
        'avg_ppm': round(sum(ppms) / len(ppms)) if ppms else 0,
        'median_ppm': median(ppms),
        'unique_normalized_addresses': len(set(r.get('ад_норм', '') for r in records)),
        'date_period': (min(dates).strftime('%d.%m.%Y') + ' – ' + max(dates).strftime('%d.%m.%Y')) if dates else '—',
        'min_date': min(dates).isoformat() if dates else '',
        'max_date': max(dates).isoformat() if dates else '',
    }


def build_market_analytics(records):
    def add(bucket, key, ppm):
        bucket.setdefault(key or '—', []).append(ppm)
    buckets = {k: {} for k in ['months', 'streets_top', 'districts', 'rooms']}
    for r in records:
        ppm = r.get('цкв') or 0
        dv = parse_date_value(r.get('дата'))
        m = f'{dv.year}-{dv.month:02d}' if dv else '—'
        add(buckets['months'], m, ppm)
        add(buckets['streets_top'], r.get('ад_норм'), ppm)
        add(buckets['districts'], r.get('р'), ppm)
        add(buckets['rooms'], r.get('категорія'), ppm)

    def pack(bucket, limit=None):
        arr = []
        for k, vals in bucket.items():
            vals = [v for v in vals if v]
            if vals:
                arr.append({'name': k, 'count': len(vals), 'median_ppm': median(vals), 'avg_ppm': round(sum(vals) / len(vals)), 'min_ppm': min(vals), 'max_ppm': max(vals)})
        arr.sort(key=lambda x: (-x['count'], x['name']))
        return arr[:limit] if limit else arr

    months = pack(buckets['months'])
    months.sort(key=lambda x: x['name'])
    return {
        'months': months,
        'streets_top': pack(buckets['streets_top'], 50),
        'districts': pack(buckets['districts']),
        'rooms': pack(buckets['rooms']),
    }


def build_executors_analytics(records):
    total = len(records) or 1
    exmap = {}
    for r in records:
        ex = (r.get('вик') or '').strip() or 'не вказано'
        dv = parse_date_value(r.get('дата'))
        y = str(dv.year) if dv else '—'
        m = f'{dv.year}-{dv.month:02d}' if dv else '—'
        item = exmap.setdefault(ex, {'name': ex, 'count': 0, 'years': {}, 'months': {}, 'ppms': [], 'dates': []})
        item['count'] += 1
        item['years'][y] = item['years'].get(y, 0) + 1
        item['months'][m] = item['months'].get(m, 0) + 1
        if r.get('цкв'):
            item['ppms'].append(r['цкв'])
        if dv:
            item['dates'].append(dv)
    performers = []
    all_years = sorted({y for e in exmap.values() for y in e['years'] if y != '—'})
    all_months = sorted({m for e in exmap.values() for m in e['months'] if m != '—'})
    for e in exmap.values():
        dates = e['dates']
        ppms = e['ppms']
        performers.append({
            'name': e['name'],
            'count': e['count'],
            'share_pct': round(e['count'] * 100 / total, 2),
            'first_date': min(dates).isoformat() if dates else '',
            'last_date': max(dates).isoformat() if dates else '',
            'years': e['years'],
            'months': e['months'],
            'median_ppm': median(ppms),
            'avg_ppm': round(sum(ppms) / len(ppms)) if ppms else 0,
        })
    performers.sort(key=lambda x: (-x['count'], x['name']))

    def top_for_period(predicate, limit=20):
        rows = []
        for p in performers:
            cnt = sum(v for k, v in p['months'].items() if predicate(k))
            if cnt:
                rows.append({'name': p['name'], 'count': cnt, 'share_pct': round(cnt * 100 / total, 2), 'last_date': p['last_date']})
        rows.sort(key=lambda x: (-x['count'], x['name']))
        return rows[:limit]

    current_year = str(date.today().year)
    top_current_year = []
    for p in performers:
        cnt = p['years'].get(current_year, 0)
        if cnt:
            top_current_year.append({'name': p['name'], 'count': cnt, 'share_pct': round(cnt * 100 / total, 2), 'last_date': p['last_date']})
    top_current_year.sort(key=lambda x: (-x['count'], x['name']))

    return {
        'period_start': START_DATE.isoformat(),
        'total_records': len(records),
        'executors_count': len(performers),
        'years': all_years,
        'months': all_months,
        'top_all': performers[:50],
        'top_current_year': top_current_year[:30],
        'top_last_12_months': performers[:30],
        'performers': performers,
    }


def clean_and_deduplicate(records):
    clean = []
    seen = set()
    duplicates = 0
    noncity = 0
    old = 0
    for r in records:
        if r.get('р') not in CITY_DISTRICTS:
            noncity += 1
            continue
        if r.get('р') == 'Оліївка новобудови' and (not r.get('рік') or int(r.get('рік')) < OLIIVKA_MIN_YEAR):
            noncity += 1
            continue
        if not valid_city_address(r.get('ад', '')):
            noncity += 1
            continue
        dv = parse_date_value(r.get('дата'))
        if dv and dv < START_DATE:
            old += 1
            continue
        r['ад_норм'] = r.get('ад_норм') or normalize_address(r.get('ад', ''))
        k = record_key(r)
        if k in seen:
            duplicates += 1
            continue
        seen.add(k)
        clean.append(r)
    return clean, duplicates, noncity, old


def build_calc_from_archive(archive):
    records = archive.get('records', [])
    dates = [parse_date_value(r.get('дата')) for r in records]
    dates = [d for d in dates if d]
    if dates:
        latest = max(dates)
        cutoff = latest - timedelta(days=WORKING_DAYS)
        work = [r for r in records if (parse_date_value(r.get('дата')) or latest) >= cutoff]
    else:
        latest = date.today()
        cutoff = latest - timedelta(days=WORKING_DAYS)
        work = records[:]
    calc = {
        'records': work,
        'count': len(work),
        'version': APP_VERSION,
        'updated': datetime.now().strftime('%Y-%m-%d'),
        'source': 'ФДМУ evaluation.spfu.gov.ua',
        'data_mode': 'working_last_12_months',
        'archive_period_start': START_DATE.isoformat(),
        'working_period_days': WORKING_DAYS,
        'working_period_from': cutoff.isoformat(),
        'working_period_to': latest.isoformat(),
        'archive_count': len(records),
    }
    calc['stats'] = compute_stats(work)
    calc['v5_analytics'] = build_market_analytics(work)
    calc['archive_analytics'] = {
        'market': build_market_analytics(records),
        'executors': build_executors_analytics(records),
        'stats': compute_stats(records),
    }
    calc['auto_update'] = {
        'source_page': SPFU_PAGE,
        'target_file_prefix': TARGET_PREFIX,
        'accepted_statuses': ['Зареєстровано', 'Перевірено'],
        'city_rule': 'Житомир + Богунський + Корольовський + Оліївка новобудови 2018+',
        'archive_from': START_DATE.isoformat(),
        'working_base': 'останні 12 місяців від найновішої дати в архіві',
        'last_run': datetime.now().isoformat(timespec='seconds'),
    }
    return calc


def build_readme(archive, calc):
    ast = archive.get('stats', {})
    cst = calc.get('stats', {})
    ex = calc.get('archive_analytics', {}).get('executors', {})
    return f'''Довідник зареєстрованих оцінок ФДМУ — Житомир

Версія сайту: {APP_VERSION}
Оновлено базу: {calc.get('updated','—')}
Джерело: ФДМУ evaluation.spfu.gov.ua, файл {TARGET_PREFIX}

Правило міста Житомир:
Житомир = Житомир + Богунський район + Корольовський район.
Оліївка включається тільки для квартир у багатоквартирних новобудовах 2018+.
Житомирський район загалом не включається.

Статуси, які включаються в базу:
- Зареєстровано
- Перевірено

Архівна база:
- Період: з 01.01.2022 до сьогодні
- Записів в архіві: {archive.get('count', len(archive.get('records', [])))}
- Квартири: {ast.get('apartments','—')}
- Кімнати/гуртожитки: {ast.get('dorms','—')}
- Виконавців: {ast.get('executors','—')}
- Період дат оцінок: {ast.get('date_period','—')}

Робоча база калькулятора:
- Період: останні 12 місяців від найновішої дати архіву
- Записів у робочій базі: {calc.get('count', len(calc.get('records', [])))}
- Квартири: {cst.get('apartments','—')}
- Кімнати/гуртожитки: {cst.get('dorms','—')}
- Медіана: {cst.get('median_ppm','—')} грн/м²
- Робочий період: {calc.get('working_period_from','—')} — {calc.get('working_period_to','—')}

Аналітика виконавців:
- Виконавців в архіві: {ex.get('executors_count','—')}
- Роки в аналізі: {', '.join(ex.get('years', [])) or '—'}
- Доступна статистика: всього, по роках, по місяцях, частка ринку, перша/остання дата, медіана грн/м².

Зміни v7.1:
- створено архівну базу archive_calc_data.json з 01.01.2022;
- calc_data.json формується як робоча база за останні 12 місяців;
- додано аналітику виконавців по роках і місяцях;
- автооновлення обробляє файли {TARGET_PREFIX} з 2022 року;
- статуси Зареєстровано і Перевірено включаються;
- контролюється поява нових колонок ФДМУ.

ТОВ «ЕКСПЕРТНА ДУМКА»
Оцінка майна
тел. 097 921 37 72
'''


def update_site_files(archive, calc):
    archive['version'] = APP_VERSION
    archive['updated'] = datetime.now().strftime('%Y-%m-%d')
    archive['count'] = len(archive.get('records', []))
    archive['stats'] = compute_stats(archive.get('records', []))
    archive['archive_analytics'] = calc.get('archive_analytics', {})
    write_json(ARCHIVE_JSON, archive)
    write_json(CALC_JSON, calc)
    README.write_text(build_readme(archive, calc), encoding='utf-8')
    if INDEX.exists():
        html = INDEX.read_text(encoding='utf-8')
        html = re.sub(r"const APP_VERSION='[^']+'", f"const APP_VERSION='{APP_VERSION}'", html)
        html = re.sub(r'id="versionTop">v[^<]+<', f'id="versionTop">v{APP_VERSION}<', html)
        html = re.sub(r'id="st-version">[^<]+<', f'id="st-version">{APP_VERSION}<', html)
        html = re.sub(r'Версія сайту: [0-9.]+[^<]*', 'Версія сайту: 7.8-beta · Оліївка 2018+ · тестовий іменний доступ', html)
        INDEX.write_text(html, encoding='utf-8')
    if SW.exists():
        sw = SW.read_text(encoding='utf-8')
        sw = re.sub(r"fdmu-zhytomyr-v[0-9_]+-cache", 'fdmu-zhytomyr-v7_8_beta-cache', sw)
        SW.write_text(sw, encoding='utf-8')


def make_site_zip():
    out = OUTPUT / 'fdmu_zhytomyr_directory_update.zip'
    OUTPUT.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in ['index.html','calc_data.json','archive_calc_data.json','README.txt','sw.js','manifest.json','logo.jpg','icon-192.png','icon-512.png']:
            p = ROOT / f
            if p.exists():
                z.write(p, f)
    return out


def seed_archive_from_existing():
    if ARCHIVE_JSON.exists():
        return read_json(ARCHIVE_JSON, {'records': []})
    calc = read_json(CALC_JSON, {'records': []})
    return {'records': calc.get('records', []), 'source': 'seed from existing calc_data.json'}


def main():
    DOWNLOADS.mkdir(exist_ok=True)
    OUTPUT.mkdir(exist_ok=True)
    processed = read_json(PROCESSED, {'processed': []})
    processed_ids = set(processed.get('processed') or [])
    archive = seed_archive_from_existing()
    existing = archive.get('records', [])
    for r in existing:
        r['ад_норм'] = r.get('ад_норм') or normalize_address(r.get('ад', ''))
    existing, dup0, non0, old0 = clean_and_deduplicate(existing)
    existing_keys = {record_key(r) for r in existing}

    report = [
        f'ФДМУ Auto Update v{APP_VERSION} — {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
        f'Сторінка: {SPFU_PAGE}',
        'Режим: архів з 01.01.2022 + робоча база 12 місяців',
        f'Стартова архівна база після очищення: {len(existing)}',
    ]

    added_all = []
    duplicates = dup0
    noncity_removed = non0
    old_removed = old0
    processed_new = []
    field_reports = []
    scanned = 0
    target_archives = 0

    try:
        html = fetch_text(SPFU_PAGE)
        links = find_zip_links(html, SPFU_PAGE)
        links = [u for u in links if link_is_from_2022_or_newer(u)]
        report.append(f'Знайдено ZIP-посилань 2022+: {len(links)}')
        if not links:
            report.append('На сторінці не знайдено ZIP-посилань з 2022 року. Перезібрано базу з наявного archive_calc_data.json.')
        for url in links:
            if url in processed_ids:
                continue
            fname = re.sub(r'[^A-Za-zА-Яа-яІіЇїЄєҐґ0-9_.-]+', '_', Path(url.split('?')[0]).name or 'fdmu.zip')
            zpath = DOWNLOADS / fname
            try:
                if not zpath.exists():
                    download_file(url, zpath)
                scanned += 1
                csv_name, raw = find_target_csv_in_zip(zpath)
                if not raw:
                    continue
                target_archives += 1
                df = read_csv_bytes(raw)
                fr = detect_new_fields(list(df.columns))
                field_reports.append({'archive': fname, 'csv': csv_name, **fr})
                records, meta, cols = extract_records(df)
                add_this = 0
                for r in records:
                    r['файл'] = csv_name or fname
                    k = record_key(r)
                    if k in existing_keys:
                        duplicates += 1
                        continue
                    existing_keys.add(k)
                    added_all.append(r)
                    add_this += 1
                processed_new.append(url)
                report.append(f'Оброблено: {fname} / {csv_name}; місто Житомир у файлі: {meta.get("raw_city_count")}; придатних: {len(records)}; додано: {add_this}')
            except Exception as e:
                report.append(f'ПОМИЛКА архіву {url}: {e}')
    except Exception as e:
        report.append(f'ПОМИЛКА завантаження сторінки ФДМУ: {e}')
        report.append('Базу перезібрано з наявних локальних JSON-файлів.')

    existing.extend(added_all)
    clean, dup2, non2, old2 = clean_and_deduplicate(existing)
    duplicates += dup2
    noncity_removed += non2
    old_removed += old2
    archive = {
        'records': clean,
        'count': len(clean),
        'version': APP_VERSION,
        'updated': datetime.now().strftime('%Y-%m-%d'),
        'source': 'ФДМУ evaluation.spfu.gov.ua',
        'archive_period_start': START_DATE.isoformat(),
        'stats': compute_stats(clean),
    }
    calc = build_calc_from_archive(archive)

    report += [
        '', 'ПІДСУМОК',
        f'Перевірено нових архівів: {scanned}',
        f'Архівів з потрібним CSV: {target_archives}',
        f'Додано нових записів до архіву: {len(added_all)}',
        f'Пропущено/видалено дублікатів: {duplicates}',
        f'Видалено не місто Житомир за адресою/районом: {noncity_removed}',
        f'Видалено до 01.01.2022: {old_removed}',
        f'Архівна база: {len(clean)}',
        f'Робоча база 12 місяців: {calc.get("count")}',
        f'Виконавців в архіві: {calc.get("archive_analytics",{}).get("executors",{}).get("executors_count")}',
        f'Робочий період: {calc.get("working_period_from")} — {calc.get("working_period_to")}',
        '', 'НОВІ / КОРИСНІ ПОЛЯ ФДМУ'
    ]
    if not field_reports:
        report.append('Нових архівів із цільовим CSV не оброблено або структура не змінилась.')
    for fr in field_reports:
        report.append(f"{fr['csv']}: колонок {fr['current_count']}; нових {len(fr['new_columns'])}; пріоритетних {len(fr['priority_columns'])}")
        if fr['priority_columns']:
            report.append('  Пріоритетні поля: ' + ', '.join(fr['priority_columns']))

    update_site_files(archive, calc)
    out_zip = make_site_zip()
    processed.setdefault('processed', [])
    processed['processed'].extend(processed_new)
    processed['last_run'] = datetime.now().isoformat(timespec='seconds')
    processed['mode'] = 'archive_from_2022'
    write_json(PROCESSED, processed)

    report_text = '\n'.join(report)
    (OUTPUT / 'update_report.txt').write_text(report_text, encoding='utf-8')
    print(report_text)
    print('\nГотовий ZIP для GitHub:', out_zip)


if __name__ == '__main__':
    main()
