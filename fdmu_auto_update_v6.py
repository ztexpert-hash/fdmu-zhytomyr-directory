#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ФДМУ Житомир — Auto Update v6.0
Автоматичне завантаження ZIP з ФДМУ, пошук Obekty_Zhytlovoi_neruxomosti,
фільтр міста Житомир та збірка ZIP для GitHub Pages.
"""
from __future__ import annotations
import io, json, re, zipfile, shutil, hashlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

try:
    import pandas as pd
except Exception:
    print('ПОМИЛКА: потрібен pandas. Встановіть: pip install pandas openpyxl')
    raise

APP_VERSION='6.0'
SPFU_PAGE='https://www.spfu.gov.ua/ua/content/spf-estimate-basereport-dani-z-edinoi-bazi.html'
TARGET_PREFIX='Obekty_Zhytlovoi_neruxomosti'
ACCEPTED_STATUSES={'ЗАРЕЄСТРОВАНО','ПЕРЕВІРЕНО'}
CITY_DISTRICTS={'Богунський','Корольовський','Житомир (без району)'}
BAD_ADDRESS_TOKENS=['БЕРДИЧ','НОВОГРАД','ЗВЯГ','КОРОСТ','МАЛИН','ОВРУЧ','ЧУДНІВ','АНДРУШ','РАДОМИШЛ']
PRIORITY_FIELD_PATTERNS=['матеріал','стін','стіни','тип буд','клас','стан','ремонт','ліфт','балкон','лодж','опал','перекрит','паркінг','гараж','поверховість','серія','новобуд','комунікац','санвуз','газ','вода']

ROOT=Path(__file__).resolve().parent
DOWNLOADS=ROOT/'downloads_fdmu'
OUTPUT=ROOT/'output_update'
PROCESSED=ROOT/'processed_files.json'
SCHEMA=ROOT/'fdmu_schema_columns.json'
CALC_JSON=ROOT/'calc_data.json'
README=ROOT/'README.txt'
INDEX=ROOT/'index.html'
SW=ROOT/'sw.js'


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else default
    except Exception:
        return default

def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

def fetch_text(url):
    req=Request(url,headers={'User-Agent':'Mozilla/5.0 FDMU-Zhytomyr-Updater/6.0'})
    with urlopen(req,timeout=60) as r: raw=r.read()
    for enc in ('utf-8','windows-1251','cp1251'):
        try: return raw.decode(enc)
        except Exception: pass
    return raw.decode('utf-8',errors='ignore')

def find_zip_links(html, base_url):
    hrefs=re.findall(r'href=["\']([^"\']+\.zip(?:\?[^"\']*)?)["\']', html, flags=re.I)
    links=[]
    for h in hrefs:
        u=urljoin(base_url,h)
        if u not in links: links.append(u)
    return links

def download_file(url,dst):
    dst=Path(dst); dst.parent.mkdir(parents=True,exist_ok=True)
    req=Request(url,headers={'User-Agent':'Mozilla/5.0 FDMU-Zhytomyr-Updater/6.0'})
    with urlopen(req,timeout=180) as r, open(dst,'wb') as f: shutil.copyfileobj(r,f)
    return dst

def find_target_csv_in_zip(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        candidates=[n for n in z.namelist() if TARGET_PREFIX.lower() in Path(n).name.lower() and Path(n).suffix.lower() in ('.csv','.txt')]
        if not candidates: return None,None
        candidates.sort(key=lambda n:z.getinfo(n).file_size, reverse=True)
        name=candidates[0]
        return Path(name).name, z.read(name)

def read_csv_bytes(raw):
    last=None
    for enc in ('utf-8-sig','cp1251','windows-1251','utf-8'):
        try: return pd.read_csv(io.BytesIO(raw),sep=';',encoding=enc,dtype=str,keep_default_na=False)
        except Exception as e: last=e
    raise RuntimeError(f'Не вдалося прочитати CSV: {last}')

def norm_header(h): return re.sub(r'\s+',' ',str(h or '').strip().lower())

def find_col(cols, patterns, required=True):
    ncols=[(c,norm_header(c)) for c in cols]
    for p in patterns:
        p=p.lower()
        for orig,n in ncols:
            if p in n: return orig
    if required: raise KeyError(f'Не знайдена колонка: {patterns}')
    return None

def norm_status(v): return re.sub(r'\s+',' ',str(v or '').strip().upper())

def norm_district(town):
    t=re.sub(r'\s+',' ',str(town or '').strip().upper())
    if 'БОГУНСЬК' in t: return 'Богунський'
    if 'КОРОЛЬОВСЬК' in t or 'КОРОЛЕВ' in t: return 'Корольовський'
    if 'ЖИТОМИРСЬК' in t and 'РАЙОН' in t: return None
    if re.search(r'(^|\b)(М\.?\s*)?ЖИТОМИР(\b|$)',t): return 'Житомир (без району)'
    return None

def to_float(v):
    s=str(v or '').replace('\xa0',' ').replace(' ','').replace(',','.').strip()
    if not s: return None
    try: return float(s)
    except Exception: return None

def parse_int(v):
    m=re.search(r'\d{1,4}',str(v or ''))
    return int(m.group(0)) if m else None

def room_cat(area):
    if area<40: return 1
    if area<65: return 2
    if area<90: return 3
    return 4

def is_dorm(type_text,address):
    s=(str(type_text or '')+' '+str(address or '')).upper()
    return any(x in s for x in ['ГУРТОЖ','КІМНАТ','КОМУНАЛ'])

def category_name(area,obj_type):
    if obj_type!='Квартира': return 'Кімната/гуртожиток'
    return {1:'1-кімнатна',2:'2-кімнатна',3:'3-кімнатна',4:'4-кімнатна'}[room_cat(area)]

def parse_executor(sod):
    s=re.sub(r'\s+',' ',str(sod or '').strip())
    if not s: return ''
    m=re.match(r'^\d{6,12}\s*,\s*(.+)$',s)
    return m.group(1).strip() if m else s

def normalize_address(addr):
    s=str(addr or '').upper()
    for x in ['ВУЛИЦЯ','ВУЛ.','ВУЛ ','ПРОСПЕКТ','ПРОСП.','ПР-Т','ПЛОЩА','ПЛ.','ПРОВУЛОК','ПРОВ.','ПРОЇЗД','БУЛЬВАР','БУЛ.']:
        s=s.replace(x,' ')
    s=re.sub(r'[^А-ЯІЇЄҐA-Z0-9\s\-/]',' ',s)
    synonyms={'МИХАЙЛА ГРУШЕВСЬКОГО':'ГРУШЕВСЬКОГО','ГРУШЕВСЬКОГО МИХАЙЛА':'ГРУШЕВСЬКОГО','ІВАНА МАЗЕПИ':'МАЗЕПИ','МАЗЕПИ ІВАНА':'МАЗЕПИ','В БЕРДИЧІВСЬКА':'ВЕЛИКА БЕРДИЧІВСЬКА','В. БЕРДИЧІВСЬКА':'ВЕЛИКА БЕРДИЧІВСЬКА','НЕБЕСНОЇ СОТНИ':'НЕБЕСНОЇ СОТНІ'}
    s=re.sub(r'\s+',' ',s).strip()
    for k,v in synonyms.items(): s=s.replace(k,v)
    return re.sub(r'\s+',' ',s).strip()

def valid_city_address(addr):
    ad=str(addr or '').upper()
    return not any(tok in ad for tok in BAD_ADDRESS_TOKENS)

def record_key(r):
    return '|'.join([str(r.get('ад_норм') or normalize_address(r.get('ад',''))), str(round(float(r.get('пл') or 0),1)), str(int(round(float(r.get('в') or 0)))), str(r.get('дата') or ''), str(r.get('к') or '')])

def extract_records(df):
    cols=list(df.columns)
    col_region=find_col(cols,['Регіон'])
    col_town=find_col(cols,['Населений пункт'])
    col_status=find_col(cols,['Статус'])
    col_type=find_col(cols,['Вид об','нерухомості'])
    col_area=find_col(cols,['Загальна площа'])
    col_floor=find_col(cols,['Поверх'],False)
    col_year=find_col(cols,['введення','Рік'],False)
    col_value=find_col(cols,['Оціночна вартість об','Вартість'])
    col_street=find_col(cols,['Вулиця'],False)
    col_street_type=find_col(cols,['Тип вулиці'],False)
    col_date=find_col(cols,['Дата оцінки','Дата реєстрації','Дата'],False)
    col_sod=find_col(cols,['СОД','Виконавець'],False)
    status_counts={}; records=[]; region_count=0; raw_city_count=0
    for _,row in df.iterrows():
        status=norm_status(row.get(col_status,'')); status_counts[status]=status_counts.get(status,0)+1
        if status not in ACCEPTED_STATUSES: continue
        if 'ЖИТОМИР' not in str(row.get(col_region,'')).upper(): continue
        region_count+=1
        district=norm_district(row.get(col_town,''))
        if not district: continue
        raw_city_count+=1
        stype=str(row.get(col_street_type,'')) if col_street_type else ''
        street=str(row.get(col_street,'')) if col_street else ''
        address=re.sub(r'\s+',' ',(stype+' '+street).strip() or street).strip()
        if not valid_city_address(address): continue
        area=to_float(row.get(col_area)); value=to_float(row.get(col_value))
        if not area or not value or area<=5: continue
        ppm=value/area
        if ppm<3000 or ppm>80000: continue
        type_text=str(row.get(col_type,'')); obj_type='Кімната/гуртожиток' if is_dorm(type_text,address) else 'Квартира'
        rec={'р':district,'к':room_cat(area),'пл':round(float(area),1),'пов':parse_int(row.get(col_floor,'')) if col_floor else None,'пх':None,'рік':parse_int(row.get(col_year,'')) if col_year else None,'цкв':round(float(ppm)),'в':round(float(value)),'ад':address[:90],'тип':obj_type,'категорія':category_name(area,obj_type),'дата':str(row.get(col_date,'')) if col_date else '', 'вик':parse_executor(row.get(col_sod,'')) if col_sod else '', 'файл':''}
        rec['ад_норм']=normalize_address(rec['ад']); records.append(rec)
    return records, {'status_counts':status_counts,'zhytomyr_region_accepted':region_count,'raw_city_count':raw_city_count}, cols

def detect_new_fields(cols):
    old=read_json(SCHEMA,{'columns':[]}); old_cols=set(old.get('columns') or [])
    new_cols=[c for c in cols if c not in old_cols]
    priority=[c for c in new_cols if any(p in norm_header(c) for p in PRIORITY_FIELD_PATTERNS)]
    write_json(SCHEMA,{'updated':datetime.now().isoformat(timespec='seconds'),'columns':cols})
    return {'new_columns':new_cols,'priority_columns':priority,'previous_count':len(old_cols),'current_count':len(cols)}

def median(vals):
    vals=sorted([float(v) for v in vals if v is not None])
    if not vals: return 0
    n=len(vals); m=n//2
    return round(vals[m] if n%2 else (vals[m-1]+vals[m])/2)

def compute_stats(records):
    ppms=[r['цкв'] for r in records if r.get('цкв')]
    districts={}; ex=set(); dates=[]
    for r in records:
        districts[r['р']]=districts.get(r['р'],0)+1
        if r.get('вик'): ex.add(r['вик'])
        if r.get('дата'): dates.append(r['дата'])
    dorms=sum(1 for r in records if r.get('тип')=='Кімната/гуртожиток')
    return {'apartments':len(records)-dorms,'dorms':dorms,'districts':districts,'executors':len(ex),'avg_ppm':round(sum(ppms)/len(ppms)) if ppms else 0,'median_ppm':median(ppms),'unique_normalized_addresses':len(set(r.get('ад_норм','') for r in records)),'date_period':(min(dates)+' – '+max(dates)) if dates else '—'}

def build_analytics(records):
    def add(bucket,key,ppm): bucket.setdefault(key or '—',[]).append(ppm)
    buckets={k:{} for k in ['months','streets_top','executors_top','districts','rooms']}
    for r in records:
        ppm=r.get('цкв') or 0; d=str(r.get('дата','')); m='—'
        mm=re.search(r'(\d{2})\.(\d{2})\.(\d{4})',d)
        if mm: m=f'{mm.group(3)}-{mm.group(2)}'
        add(buckets['months'],m,ppm); add(buckets['streets_top'],r.get('ад_норм'),ppm); add(buckets['executors_top'],r.get('вик'),ppm); add(buckets['districts'],r.get('р'),ppm); add(buckets['rooms'],r.get('категорія'),ppm)
    def pack(bucket,limit=None):
        arr=[]
        for k,vals in bucket.items():
            vals=[v for v in vals if v]
            if vals: arr.append({'name':k,'count':len(vals),'median_ppm':median(vals),'avg_ppm':round(sum(vals)/len(vals)),'min_ppm':min(vals),'max_ppm':max(vals)})
        arr.sort(key=lambda x:(-x['count'],x['name']))
        return arr[:limit] if limit else arr
    return {'months':pack(buckets['months']),'streets_top':pack(buckets['streets_top'],30),'executors_top':pack(buckets['executors_top'],30),'districts':pack(buckets['districts']),'rooms':pack(buckets['rooms'])}

def build_readme(data):
    st=data.get('stats',{}); dist=st.get('districts',{})
    return f'''Довідник зареєстрованих оцінок ФДМУ — Житомир

Версія сайту: {APP_VERSION}
Оновлено базу: {data.get('updated','—')}
Джерело: ФДМУ evaluation.spfu.gov.ua, файл {TARGET_PREFIX}

Правило міста Житомир:
Місто Житомир = Житомир + Богунський район + Корольовський район.
Житомирський район не включається.

Статуси, які включаються в базу:
- Зареєстровано
- Перевірено

Робоча база калькулятора: {data.get('count',len(data.get('records',[])))} записів
Квартири: {st.get('apartments','—')}
Кімнати/гуртожитки: {st.get('dorms','—')}
Райони:
- Житомир без району: {dist.get('Житомир (без району)','—')}
- Богунський: {dist.get('Богунський','—')}
- Корольовський: {dist.get('Корольовський','—')}

Контроль бази:
- Всього записів міста Житомир у джерелі: {data.get('raw_city_count','—')}
- Придатних до калькулятора до видалення дублікатів: {data.get('eligible_city_count_before_duplicates','—')}
- Видалено дублікатів: {data.get('duplicates_removed','—')}
- Виконавців у робочій базі: {st.get('executors','—')}
- Медіана: {st.get('median_ppm','—')} грн/м²
- Період дат оцінок: {st.get('date_period','—')}

Зміни v6.0 Auto Update:
- автоматичне завантаження ZIP з сайту ФДМУ;
- всередині ZIP шукається файл {TARGET_PREFIX};
- у базу включаються статуси Зареєстровано та Перевірено;
- автоматично фільтрується місто Житомир: Житомир + Богунський + Корольовський;
- контролюється поява нових колонок ФДМУ: матеріал стін, тип будинку, клас житла, стан, ремонт тощо;
- автоматично формується ZIP для GitHub Pages.

ТОВ «ЕКСПЕРТНА ДУМКА»
Оцінка майна
тел. 097 921 37 72
'''

def update_site_files(data):
    data['version']=APP_VERSION; data['updated']=datetime.now().strftime('%Y-%m-%d'); data['stats']=compute_stats(data['records']); data['v5_analytics']=build_analytics(data['records'])
    data['auto_update']={'source_page':SPFU_PAGE,'target_file_prefix':TARGET_PREFIX,'accepted_statuses':['Зареєстровано','Перевірено'],'city_rule':'Житомир + Богунський + Корольовський','new_fields_monitoring':True,'last_run':datetime.now().isoformat(timespec='seconds')}
    write_json(CALC_JSON,data); README.write_text(build_readme(data),encoding='utf-8')
    if INDEX.exists():
        html=INDEX.read_text(encoding='utf-8')
        html=re.sub(r"const APP_VERSION='[^']+'",f"const APP_VERSION='{APP_VERSION}'",html)
        html=re.sub(r'id="versionTop">v[^<]+<',f'id="versionTop">v{APP_VERSION}<',html)
        html=re.sub(r'id="st-version">[^<]+<',f'id="st-version">{APP_VERSION}<',html)
        INDEX.write_text(html,encoding='utf-8')
    if SW.exists():
        sw=SW.read_text(encoding='utf-8'); sw=re.sub(r"fdmu-zhytomyr-v[0-9_]+-cache",'fdmu-zhytomyr-v6_0-cache',sw); SW.write_text(sw,encoding='utf-8')

def make_site_zip():
    out=OUTPUT/'fdmu_zhytomyr_directory_update.zip'; OUTPUT.mkdir(exist_ok=True)
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for f in ['index.html','calc_data.json','README.txt','sw.js','manifest.json','logo.jpg','icon-192.png','icon-512.png']:
            p=ROOT/f
            if p.exists(): z.write(p,f)
    return out

def main():
    DOWNLOADS.mkdir(exist_ok=True); OUTPUT.mkdir(exist_ok=True)
    processed=read_json(PROCESSED,{'processed':[]}); processed_ids=set(processed.get('processed') or [])
    report=[f'ФДМУ Auto Update v{APP_VERSION} — {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',f'Сторінка: {SPFU_PAGE}']
    html=fetch_text(SPFU_PAGE); links=find_zip_links(html,SPFU_PAGE); report.append(f'Знайдено ZIP-посилань: {len(links)}')
    if not links: raise RuntimeError('На сторінці не знайдено ZIP-посилань')
    base=read_json(CALC_JSON,{'records':[]}); existing=base.get('records',[])
    for r in existing: r['ад_норм']=r.get('ад_норм') or normalize_address(r.get('ад',''))
    existing_keys={record_key(r) for r in existing}; added_all=[]; duplicates=0; processed_new=[]; field_reports=[]; scanned=0
    for url in links:
        fname=re.sub(r'[^A-Za-zА-Яа-яІіЇїЄєҐґ0-9_.-]+','_',Path(url.split('?')[0]).name or 'fdmu.zip')
        if url in processed_ids: continue
        zpath=DOWNLOADS/fname
        try:
            if not zpath.exists(): download_file(url,zpath)
            scanned+=1; csv_name,raw=find_target_csv_in_zip(zpath)
            if not raw: continue
            df=read_csv_bytes(raw); fr=detect_new_fields(list(df.columns)); field_reports.append({'archive':fname,'csv':csv_name,**fr})
            records,meta,cols=extract_records(df)
            for r in records:
                r['файл']=csv_name or fname; k=record_key(r)
                if k in existing_keys: duplicates+=1; continue
                existing_keys.add(k); added_all.append(r)
            processed_new.append(url); report.append(f'Оброблено: {fname} / {csv_name}; місто Житомир у файлі: {meta.get("raw_city_count")}; придатних: {len(records)}')
        except Exception as e: report.append(f'ПОМИЛКА архіву {url}: {e}')
    existing.extend(added_all); clean=[]; seen=set()
    for r in existing:
        if r.get('р') not in CITY_DISTRICTS: continue
        if not valid_city_address(r.get('ад','')): continue
        r['ад_норм']=r.get('ад_норм') or normalize_address(r.get('ад',''))
        k=record_key(r)
        if k in seen: duplicates+=1; continue
        seen.add(k); clean.append(r)
    base['records']=clean; base['count']=len(clean); base['duplicates_removed']=int(base.get('duplicates_removed') or 0)+duplicates; base['eligible_city_count_before_duplicates']=len(clean)+duplicates; base['raw_city_count']=max(int(base.get('raw_city_count') or 0), len(clean)+duplicates)
    report += ['', 'ПІДСУМОК', f'Перевірено нових архівів: {scanned}', f'Додано нових записів: {len(added_all)}', f'Пропущено/видалено дублікатів: {duplicates}', f'Робоча база після оновлення: {len(clean)}', '', 'НОВІ / КОРИСНІ ПОЛЯ']
    if not field_reports: report.append('Нових архівів із цільовим CSV не оброблено.')
    for fr in field_reports:
        report.append(f"{fr['csv']}: колонок {fr['current_count']}; нових {len(fr['new_columns'])}; пріоритетних {len(fr['priority_columns'])}")
        if fr['priority_columns']: report.append('  Пріоритетні поля: '+', '.join(fr['priority_columns']))
    report_text='\n'.join(report); (OUTPUT/'update_report.txt').write_text(report_text,encoding='utf-8')
    update_site_files(base); out_zip=make_site_zip()
    processed.setdefault('processed',[]); processed['processed'].extend(processed_new); processed['last_run']=datetime.now().isoformat(timespec='seconds'); write_json(PROCESSED,processed)
    print(report_text); print('\nГотовий ZIP для GitHub:', out_zip)

if __name__=='__main__': main()
