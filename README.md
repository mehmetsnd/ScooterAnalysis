# BinBin — Başarısız Sürüş Analizi ve Şüpheli Arıza Alarmı

Paylaşımlı e-scooter sürüşlerinde **başarısızlık nedenlerini** sınıflandıran ve projenin
ana çıktısı olan **şüpheli (sahte) arıza alarmı** ölçümünü üreten katmanlı Python CLI'ı.

**İş problemi.** Çalışan bir cihaz "arızalı" bildirildiğinde üç fiziksel operasyon
tetiklenir: sahadan toplama → atölye kontrolü → sahaya geri bırakma. Bildirim yersizse
bu üç görev boşa çalışır ve o sırada gerçekten arızalı araçlar sahada müşteri bekletir.
Bu proje o kaybı ölçer.

**Adlandırma disiplini.** Çıktılar "SAHTE" değil **"ŞÜPHELİ"** der. Mevcut veri kesin
hüküm veremez; sistem bir *iddia* üretir, bir *yargı* değil.

---

## Hızlı başlangıç

```powershell
# 1) Sanal ortam (proje kurulmaz; src-layout + PYTHONPATH ile çalışır)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2) Veritabanı: PostgreSQL 18, tek dosyayla kurulur
#    pgAdmin Query Tool'da hedef veritabanı seçili iken db/01_setup.sql'in TAMAMINI çalıştır.

# 3) Bağlantı
Copy-Item .env.example .env       # DATABASE_URL'i doldur

# 4) Ham CSV'leri data_raw/ altına koy (git-ignored)

# 5) Uçtan uca pipeline
.\run.ps1 -WiDuration 150 -WiDistance 70
```

> ⚠️ Python komutları **daima** `.venv` ile çalıştırılır:
> `.\.venv\Scripts\python.exe`. Sistem Python'ında `pytest` "no tests collected"
> deyip testleri koşmadan sessizce geçer.

---

## Veri kaynakları

`ingest` CSV türünü **başlık satırından** tanır ve her türü kendi dönüştürücüsüne
yönlendirir. Tanımadığı dosyayı atlar; tanıdığı bir dosyanın başlığı bozuksa **durur**.

| # | Kaynak | Hedef tablolar | Kanıt katkısı |
|---|---|---|---|
| 1 | Sürüş kaydı (~1,03M) | `ride`, `feedback` | Telemetri + serbest metin |
| 2 | Araç durum-değişim defteri (4,17M) | `fleet_status_event`, `fleet_status_reason` | `REASON_CODE` — açık teknik arıza sinyali |
| 3 | Bakım geçmişi (112.787) | `maintenance_event`, `damage_sub_type` | `MAINTENANCE` — sürüşten sonraki 24 sa içinde bakım kaydı |
| 4 | Koordinat / geofence (991.709) | `ride_geo`, `geofence` | `displacement_m` — odometreden bağımsız hareket kanıtı |

Bunlara ek olarak **komşu sürüş** sinyali (`NEIGHBOR_RIDE`) aynı aracın veya aynı
kullanıcının komşu sürüşünden türetilir; problemin *tarafını* verir, alt türünü değil.

**Ölçülmüş sınır.** (2) numaralı defter bir araç-tarafı IoT durum makinesidir ve
taksonomideki beş kategoriden yalnız TEKNIK'i görebilir. Başarısız sürüşlerin %100'ünün
penceresinde olay vardır ama %88'inde bu olaylar yalnızca muhasebe kaydıdır. Kategori
atanamayan kütleyi asıl düşürecek olan bu defter değil, (1)'deki NULL telemetri
kolonlarının doldurulması ve uygulama/ödeme loglarıdır.

---

## Komutlar

```powershell
$env:PYTHONPATH = "src"

python -m pytest tests/ -q                     # 319 test, tamamı DB'siz

python -m binbin.cli ingest                    # CSV'leri türüne göre yükler
python -m binbin.cli classify --refresh        # başarısız sürüşleri sınıflandırır
python -m binbin.cli assess   --refresh        # şüpheli arıza değerlendirmesi
python -m binbin.cli loads                     # yükleme denetim kaydı

python -m binbin.cli analyze --false-fault --detay --derin `
    --sinyal-denetimi --esik-taramasi --kelime-denetimi `
    --charts out --wi-duration 150 --wi-distance 70
```

`analyze` **yazmaz**; her çağrıda timeline'ı okuyup Python'da toplar.

### `analyze` bayrakları

| Bayrak | Ne yapar |
|---|---|
| `--false-fault` | Şüpheli arıza özeti + Kategori-Sonuç Matrisi + Teknik Arıza Kırılımı |
| `--detay` | Araç ve alt bölge sıcak noktaları |
| `--derin` | Saatlik yerel dağılım (şehir başına küçük çoklular) |
| `--esik-taramasi` | 5×5 süre/mesafe ızgarasını F1 ile tarar, iki eşik senaryosu çıkarır |
| `--sinyal-denetimi` | Kural kitabındaki 58 kodun ayırt ediciliğini (lift) ölçer |
| `--kelime-denetimi` | Anahtar kelime kümesinin ayırt ediciliğini (lift) ölçer |
| `--charts DIR` | PNG grafikleri üretir |
| `--wi-duration` + `--wi-distance` | Özel Kural senaryosu ekler (**birlikte** verilmeli) |

---

## Kapsam (scope)

Kapsam koda gömülü değildir; `config.py:DEFAULT_SCOPE` tek kaynaktır. Tüm komutlar
aynı semantiği paylaşır:

- bayrak yok → `DEFAULT_SCOPE` (Türkiye + İstanbul Avrupa/Anadolu) — **tüm veri değil**
- `--country AD` / `--city AD` (tekrarlanabilir) → verilen kapsam; birlikte VE olarak uygulanır
- `--all` → filtre yok; `--country`/`--city` ile birlikte verilemez

Çözülemeyen ad **hata verir** (exit 1), sessizce boş sonuç üretmez. Kısmî eşleşme de
hatadır: iki şehirden biri tutmazsa veri sessizce yarıya inerdi. Yazım hatalarında
`difflib` en yakın adı önerir.

> `ingest_status` kapsamı **yok sayar** — `stg_status_raw`'da ülke/şehir kolonu yoktur;
> defter daima filo genelinde yüklenir. `run.ps1` bunu uyarır.

---

## Başarısızlık senaryoları

Analiz aynı sürüş kümesini iki kuralla değerlendirir:

- **Mevcut Kural** — kaynak `BASARISIZ_HARD` **veya** (süre < 120 sn **VE** mesafe < 60 m)
- **Özel Kural** — kaynak etiketi yok sayılır; yalnız CLI'daki eşik uygulanır

Kural **daima AND**'dir: kısa ama mesafeli bir sürüş başarısız sayılmaz. Özel Kural'da
ölçümü eksik sürüşler başarılı sayılmaz, `değerlendirilemedi` olarak ayrı raporlanır.

`--esik-taramasi` bu kuralın çevresinde dar bir ızgarayı (süre 90–150 sn × mesafe
40–80 m) F1 ile tarar ve iki alternatif senaryo çıkarır. Tarama **eşik önermez** —
isabet ızgaranın tamamında neredeyse sabittir, dolayısıyla hiçbir eşik teşhisi
iyileştirmez; yalnız neye bakıldığının kapsamını ve hacmini değiştirir. Seçim bir
maliyet dengesi sorusudur ve `ops_cost_model` dolana kadar TL'ye çevrilemez.

---

## Mimari

Functional core / imperative shell. Bağımlılık yönü: **`cli / reporting → core → data → domain`**.
`core/` saftır (I/O yok); DB ve dosya yan etkileri yalnız shell'dedir.

```
src/binbin/
├── config.py             # DEFAULT_SCOPE, pencere sabitleri, sürüm damgaları
├── domain/               # saf DTO'lar: enums.py, models.py
├── core/                 # SAF çekirdek — tek karar yeri
│   ├── classifier.py         # classify_ride — kategori ataması (öncelik zinciri)
│   ├── false_fault.py        # assess_ride  — verdict + hipotez
│   ├── scenario_analysis.py  # canlı analiz motoru (yazmasız, tek geçiş)
│   ├── threshold_scan.py     # eşik taraması (F1 ızgarası)
│   ├── keywords.py           # çok dilli anahtar kelime kural kitabı
│   ├── signal_audit.py       # kod ayırt ediciliği (lift)
│   ├── keyword_audit.py      # kelime ayırt ediciliği (lift)
│   └── ratios.py             # iki motorun paylaştığı saf yardımcılar
├── data/                 # Repository deseni (DIP)
│   ├── repository.py         # Protocol'ler + AnalysisScope
│   ├── engine.py             # Engine, scope derleyici, paylaşılan join SQL'leri
│   ├── queries.py            # okuma tarafı
│   ├── classify.py           # yazma: sınıflandırma
│   ├── assess.py             # yazma: şüpheli arıza değerlendirmesi
│   ├── ingest.py             # sürüş + bakım + geo CSV → Postgres (COPY ETL)
│   ├── ingest_status.py      # durum-değişim CSV → Postgres
│   └── postgres_repo.py      # ince Protocol implementasyonu
├── reporting/            # charts.py (matplotlib PNG), format.py (ortak biçimleyiciler)
└── cli/main.py           # argparse — TEK giriş noktası
```

**Bu proje yalnızca CLI'dır.** Erken safhadaki web katmanı fikri (FastAPI + Plotly)
terk edildi ve silindi; çıktı terminal + opsiyonel PNG'dir.

### Sınıflandırma öncelik zinciri

Telemetri (1–5) → durum defteri sinyali (6) → sürüş mesajı (7) → kullanıcı yorumu (8)
→ bakım kaydı (9) → komşu sürüş (10) → **KANIT_YOK**.

Kategori **tahmin edilmez**. Atanamayan başarısızlık NULL kalır ve "Kanıt Bulunmayan"
olarak şeffaf raporlanır.

---

## Veritabanı

Şema Python'da **kurulmaz**; PostgreSQL'de elle çalıştırılan SQL'de yaşar.

```
db/
├── 01_setup.sql          # TEK kurulum dosyası — dört bölüm, sıra kararı yok
└── reset/                # kurulumun parçası DEĞİL: operasyonel veri sıfırlama
    ├── 01_pre_data_reset_check.sql    # salt okunur mevcut durum
    ├── 02_reset_operational_data.sql  # TRUNCATE (tablo/partition SİLMEZ, CASCADE yok)
    └── 03_post_data_reset_check.sql   # şema/partition/config sağlam mı
```

- **Ayrı migration dosyası açılmaz.** Yeni tablo/kolon veya kural kitabı revizyonu
  doğrudan `01_setup.sql`'e işlenir; kuran kişi daima projenin son hâlini alır.
- **Partition:** `ride`, `fleet_status_event` ve `maintenance_event` aylık RANGE
  partition'lıdır. PK partition anahtarını içerir → bağlı tablolar bileşik FK kullanır.
  `*_default` partition **daima boş** olmalıdır.
- **CASCADE bilinçli kullanılmaz.** Bunun bedeli: `ride`/`data_load`'a FK ile bağlı her
  tablo TRUNCATE listesinde bulunmalıdır, yoksa Postgres komutu tamamen reddeder.

Reset sonrası sırayla: `ingest` → `classify --refresh` → `assess --refresh`.

---

## Konvansiyonlar

- **`duration_sec` hesaplanır** (end − start). CSV'deki `duration` (dakika) tutarsız
  yuvarlanır, kullanılmaz.
- **`distance_m` kanonik kaynağı `mongo_distance_meters`**'dir. `distance_meters` ve
  `distance` analiz kararında kullanılmaz (test bunu korur).
- **`is_test` filtresi:** `region_id=8` ("Test") gerçek sürüş değildir; tüm analiz
  daima `ci.is_test = false` filtreler.
- **Out-of-content:** mesafe > 20 km veya süre ≥ 6 sa → analizden dışlanır, ayrı kovada sayılır.
- **Timezone:** ham timestamp daima `Europe/Istanbul`; analiz yerel saate
  `AT TIME ZONE country.timezone` ile çevirir.
- **SQL güvenlik sözleşmesi:** değerler daima `:param` bind; identifier'lar sabit
  literal veya allowlist.
- **Dil kuralı:** tanımlayıcılar İngilizce, yorum ve kullanıcıya görünen metin Türkçe.
- **`ops_cost_model` boştur:** maliyet parametresi gelene kadar analiz "N boşa görev"
  der, "Y TL" **demez**.

---

## Test

```powershell
$env:PYTHONPATH = "src"; python -m pytest tests/ -q     # 319 test
```

Test paketi **veritabanı gerektirmez** — Repository Protocol'ü fake/mock ile
karşılanır. Bu, `core/`'un saf tutulmasının doğrudan karşılığıdır.

Testler yalnız davranışı değil sözleşmeleri de kilitler: CSV şema sözleşmesi
`db/01_setup.sql` DDL'ine karşı, 120/60 eşiği Python sabiti ile SQL kısıtı arasında,
reset scriptleri `db/` klasör yapısına karşı doğrulanır.

---

## Performans

Ölçekleme ekseni satır sayısı değil **veri kaynağı sayısıdır**: her yeni CSV, tüm veri
kütlesi üzerinde bir join daha demektir. Bakım ve geofence kaynakları eklendiğinde ana
sorgu 2,8 → 17 sn'ye çıktı.

Uygulanan düzeltmeler (sıra önemlidir):

1. **Kapsam predikatı `ride.city_id`'ye taşındı.** `city` join'i üzerinden verildiğinde
   planlayıcı satır sayısını 79 kat şaşırıp nested loop seçiyordu.
2. **`DB_WORK_MEM = 128MB`** — pencere sıralamalarının disk dökümünü kaldırır. Bu ayar
   tek başına işe yaramaz; bağlayıcı kısıt önce (1) ile düzelmelidir.

Kalan darboğaz sunucuda değil, satırların Python'a taşınmasındadır. Ayrıntılı ölçümler
ve bir sonraki adımın gerekçesi `CLAUDE.md`'nin performans bölümündedir.

---

## Belgeler

- **`CLAUDE.md`** — ayrıntılı mimari, altın kurallar, ölçüm kayıtları ve tuzaklar.
- **`docs/`** — teknik rapor (HTML kaynak + PDF). Gerçek operasyonel rakamlar içerdiği
  için repoya dahil edilmez; `build_report.ps1` ile PDF basılır.
