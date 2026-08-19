-- ============================================================
-- Binbin — KURULUM (db/01_setup.sql)
--
-- TEK kurulum dosyasıdır. Sırayla birden fazla script çalıştırmak gerekmez:
-- pgAdmin Query Tool'da `binbin` veritabanı seçili iken tamamını yapıştır.
--
-- !!! DİKKAT: public şemasındaki HER ŞEYİ SİLER. Yalnız temiz kurulumda çalıştır.
--
-- Bölümler:
--   1) Çekirdek şema  — enum'lar, coğrafi hiyerarşi, vehicle, ride (partition'lı), feedback
--   2) Sahte arıza    — false_fault_assessment, ops_cost_model, view'ler
--   3) Durum defteri  — fleet_status_code/reason/event + kural kitabı seed'i
--   4) Bakım + konum  — damage_sub_type/maintenance_event, geofence/ride_geo
--
-- Yeni tablo/kolon BU DOSYAYA eklenir; ayrı migration dosyası açılmaz.
-- Operasyonel veri sıfırlama betikleri: db/reset/ (kurulumun parçası DEĞİL).
-- ============================================================



-- ============================================================
-- BÖLÜM 1/4 — ÇEKİRDEK ŞEMA
-- ============================================================

-- ============================================================
-- Enum'lar, coğrafi hiyerarşi, vehicle, partition'lı ride, feedback, staging.
--
-- ride PARTITION'lıdır; PostgreSQL bir tabloyu ALTER ile partition'lı yapamaz,
-- bu yüzden kurulum şemayı sıfırdan kurar.
-- ============================================================

DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO CURRENT_USER;
GRANT ALL ON SCHEMA public TO public;

BEGIN;

-- ------------------------------------------------------------
-- 1) ENUM TİPLERİ
-- ------------------------------------------------------------

CREATE TYPE vehicle_status AS ENUM ('AVAILABLE','ON_TRIP','REMOVED','MAINTENANCE');

CREATE TYPE rule_type AS ENUM (
    'NO_RIDE','SLOW_ZONE','NO_PARKING','MANDATORY_PARKING',
    'OPERATING_HOUR','CITY_BOUNDARY','SPEED_LIMIT');

CREATE TYPE enforcement_action AS ENUM (
    'MOTOR_CUTOFF','SPEED_THROTTLE','BLOCK_END_RIDE','BLOCK_START','AUDIBLE_WARNING');

CREATE TYPE ride_outcome AS ENUM ('BASARILI','BASARISIZ_HARD','DEGRADED','IPTAL');

CREATE TYPE failure_category AS ENUM ('TEKNIK','REGULASYON','KULLANICI','ODEME','SISTEM',
                                      'ARAC_TARAFI','KULLANICI_TARAFI');
-- Bilinçli karar: 'BILINMIYOR' YOK. Sınıflandırılamayan başarısızlık → NULL.

CREATE TYPE payment_status AS ENUM ('OK','DECLINED','INSUFFICIENT_BALANCE','PREAUTH_FAILED');

CREATE TYPE failure_reason AS ENUM (
    'UNLOCK_ACK_TIMEOUT','GPS_NO_FIX','CONNECTION_LOST','IOT_FAULT','LOW_BATTERY',
    'BMS_FAULT','MOTOR_ERROR','LOCK_JAM','QR_SCAN_FAIL','BLE_PAIR_FAIL','NO_RIDE_ZONE',
    'SLOW_ZONE_THROTTLE','NO_PARK_BLOCK','OPERATING_HOUR_BLOCK','CITY_BOUNDARY_CUTOFF',
    'USER_CANCELLED','PARKING_PHOTO_FAIL','PAYMENT_DECLINED','INSUFFICIENT_BALANCE',
    'PREAUTH_FAILED','BACKEND_ERROR');

CREATE TYPE classification_source AS ENUM (
    'FIELD_SIGNAL','REASON_CODE','TEXT_MESSAGE','TEXT_COMMENT','NEIGHBOR_RIDE',
    'MAINTENANCE','NONE');


-- ------------------------------------------------------------
-- 2) COĞRAFİ HİYERARŞİ:  country -> city -> sub_region
-- ------------------------------------------------------------
-- Kaynak: country_id -> region_id -> sub_region_id
-- DİKKAT: region_id şu an ülkeler arası çakışmıyor ama buna GÜVENİLMEZ.
--         sub_region_id ise ZATEN çakışıyor: 591 / 599 / 605 / 623 kodları
--         birden fazla bölgede geçiyor (örn. 599 hem İstanbul Avrupa'da 14.514,
--         hem İstanbul Anadolu'da 258 sürüşte). Benzersizlik daima BİLEŞİK.

CREATE TABLE country (
    country_id        bigserial PRIMARY KEY,
    source_country_id int NOT NULL UNIQUE,     -- CSV country_id (1, 28, 123)
    name              varchar(80) NOT NULL UNIQUE,
    iso_code          char(2),
    currency          char(3) NOT NULL,        -- TRY, BAM, MKD
    timezone          text NOT NULL,           -- IANA: 'Europe/Istanbul'
    active            boolean NOT NULL DEFAULT true
);

COMMENT ON COLUMN country.timezone IS
    'IANA saat dilimi — GÖRÜNTÜLEME/ANALİZ için. DOĞRULANDI (lead): kaynak sistemdeki '
    'TÜM start_date_tr / end_date_tr değerleri ülkeden BAĞIMSIZ olarak TR saatiyle '
    '(UTC+3, DST yok) kaydedilir. Yani ingest, ham timestamp''i DAİMA '
    '''Europe/Istanbul'' olarak yorumlayıp UTC''ye çevirir (country.timezone''a göre DEĞİL). '
    'Bu kolon yalnızca SONRADAN yerel saatte göstermek/analiz etmek için kullanılır: '
    'start_time AT TIME ZONE c.timezone. NOT: Haziran 2026''da Balkan ülkeleri yaz '
    'saatinde (UTC+2), yani yerel saat = TR saati - 1. Bu düzeltme K. Makedonya''nın '
    'gece yarısı sürüş zirvesini (00:00, %7,12) YALNIZCA 23:00''e kaydırıyor — '
    'tuhaflığı ÇÖZMÜYOR. Yani bu saat dilimi kayması DEĞİL, ayrı ve gerçek bir '
    'davranışsal bulgu; analiz aşamasında böyle raporlanmalı, saat hatası gibi atlanmamalı.';


CREATE TABLE city (
    city_id          bigserial PRIMARY KEY,
    country_id       bigint NOT NULL REFERENCES country(country_id),
    source_region_id int NOT NULL,             -- CSV region_id
    name             varchar(80) NOT NULL,
    admin_authority  varchar(80),              -- İstanbul: 'UKOME'
    is_test          boolean NOT NULL DEFAULT false,
    active           boolean NOT NULL DEFAULT true,
    CONSTRAINT uq_city_source UNIQUE (country_id, source_region_id),
    CONSTRAINT uq_city_name   UNIQUE (country_id, name)
);

COMMENT ON COLUMN city.is_test IS
    'Veride region_id=8, adı literal olarak "Test". Gerçek sürüş değildir. '
    'Analiz sorguları DAİMA is_test = false filtreler.';


CREATE TABLE sub_region (
    sub_region_id        bigserial PRIMARY KEY,
    city_id              bigint NOT NULL REFERENCES city(city_id),
    source_sub_region_id int NOT NULL,
    name                 varchar(80),
    CONSTRAINT uq_sub_region_source UNIQUE (city_id, source_sub_region_id)
);

COMMENT ON TABLE sub_region IS
    'Doğal anahtar (city_id, source_sub_region_id) çiftidir — source_sub_region_id '
    'tek başına benzersiz DEĞİLDİR. Alt bölge, geofence bölgesi için mekânsal PROXY.';


-- ------------------------------------------------------------
-- 3) REFERANS TABLOLARI
-- ------------------------------------------------------------

-- Sürüş sonlandırma kodları. Anlamları BİLİNMİYOR.
-- Ingest tarafından DİNAMİK doldurulur (staging'deki distinct reason_id).
-- Sabit liste seed EDİLMEZ: yeni ülke/yeni kod gelince kırılmasın.
CREATE TABLE end_reason (
    reason_id     int PRIMARY KEY,
    label         varchar(120),
    category_hint failure_category,
    reason_hint   failure_reason,
    verified      boolean NOT NULL DEFAULT false,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    notes         text
);

COMMENT ON TABLE end_reason IS
    'Haziran 2026: 28 farklı kod (TR 15, Bosna 16, K.Makedonya 9; kümeler kesişiyor). '
    'label/category_hint saha ekibi doğrulayana kadar NULL, verified=false. TAHMİN YAZILMAZ.';


CREATE TABLE vehicle (
    vehicle_id       bigserial PRIMARY KEY,
    source_ref       varchar(40) NOT NULL UNIQUE,   -- CSV vehicle_id
    external_code    varchar(40) UNIQUE,            -- plaka
    model            varchar(60),
    firmware_version varchar(40),
    iot_box_id       varchar(60),
    status           vehicle_status NOT NULL DEFAULT 'AVAILABLE'
);


CREATE TABLE regulation (
    regulation_id      bigserial PRIMARY KEY,
    city_id            bigint NOT NULL REFERENCES city(city_id),
    sub_region_id      bigint REFERENCES sub_region(sub_region_id),
    rule_type          rule_type NOT NULL,
    enforcement_action enforcement_action NOT NULL,
    zone_name          varchar(120),
    speed_limit_kmh    smallint CHECK (speed_limit_kmh BETWEEN 0 AND 100),
    start_hour         smallint CHECK (start_hour BETWEEN 0 AND 23),
    end_hour           smallint CHECK (end_hour   BETWEEN 0 AND 23),
    fine_amount        numeric(12,2) CHECK (fine_amount >= 0),
    fine_currency      char(3),
    active             boolean NOT NULL DEFAULT true,
    effective_from     date,
    effective_to       date,
    source_ref         varchar(200),
    CONSTRAINT ck_operating_hour_needs_hours CHECK (
        rule_type <> 'OPERATING_HOUR' OR (start_hour IS NOT NULL AND end_hour IS NOT NULL)),
    CONSTRAINT ck_speed_rule_needs_limit CHECK (
        rule_type NOT IN ('SLOW_ZONE','SPEED_LIMIT') OR speed_limit_kmh IS NOT NULL),
    CONSTRAINT ck_effective_range CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

COMMENT ON TABLE regulation IS
    'Regülasyon matrisi: şehir x (alt bölge) x kural tipi x yaptırım. YAPI sabit, '
    'DEĞERLER satır olarak durur. Ceza tutarı ülkeye göre para birimi değiştirir → '
    'fine_currency ayrı (v1''deki fine_amount_try çok-ülkede yanlıştı).';


-- ------------------------------------------------------------
-- 4) ANA TABLO: ride  — AYLIK PARTITION
-- ------------------------------------------------------------
-- Neden: aylık ~1M satır. 3 yıl ≈ 36M, 10 yıl ≈ 120M.
-- Sorgular zaman + şehir filtreli → partition pruning taramayı tek aya indirir.
-- Eski ayı arşivlemek: DROP TABLE ride_2026_06  (anlık, VACUUM gerekmez).
-- MALİYET: PK/UNIQUE partition anahtarını (start_time) İÇERMEK ZORUNDA.
--          Bu yüzden ride'a bağlanan tablolar (ride_id, start_time) çiftiyle FK verir.

CREATE TABLE ride (
    ride_id                 bigserial,
    source_ref              varchar(40) NOT NULL,
    vehicle_id              bigint NOT NULL REFERENCES vehicle(vehicle_id),
    city_id                 bigint NOT NULL REFERENCES city(city_id),
    sub_region_id           bigint REFERENCES sub_region(sub_region_id),
    triggered_regulation_id bigint REFERENCES regulation(regulation_id),
    user_ref                varchar(40) NOT NULL,

    start_time   timestamptz NOT NULL,   -- PARTITION ANAHTARI
    end_time     timestamptz,
    duration_sec numeric(10,2) CHECK (duration_sec >= 0),
    distance_m   numeric(12,2) CHECK (distance_m >= 0),

    outcome               ride_outcome NOT NULL,
    failure_category      failure_category,
    failure_reason        failure_reason,
    classification_source classification_source NOT NULL DEFAULT 'NONE',
    classified_at         timestamptz,
    classifier_version    varchar(20),

    end_reason_id int REFERENCES end_reason(reason_id),
    end_message   text,

    -- Telemetri: mevcut CSV'de YOK -> hepsi NULL. Kod NULL'a dayanıklı olmalı.
    unlock_ack        boolean,
    ack_latency_ms    int CHECK (ack_latency_ms >= 0),
    start_battery_pct smallint CHECK (start_battery_pct BETWEEN 0 AND 100),
    connection_lost   boolean,
    gps_fix_ok        boolean,
    motor_error_code  varchar(40),
    bms_error_code    varchar(40),
    lock_state_ok     boolean,
    parking_photo_ok  boolean,
    user_cancelled    boolean,
    payment_status    payment_status,

    gross_amount numeric(12,2) CHECK (gross_amount >= 0),
    currency     char(3),

    data_quality_flags text[] NOT NULL DEFAULT '{}',
    data_load_id       bigint,
    ingested_at        timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (ride_id, start_time),
    CONSTRAINT uq_ride_source UNIQUE (source_ref, start_time),
    CONSTRAINT ck_end_after_start CHECK (end_time IS NULL OR end_time >= start_time),
    CONSTRAINT ck_category_needs_source CHECK (
        failure_category IS NULL OR classification_source <> 'NONE'),
    -- Kategori taşıyabilen satır = Mevcut Kural'ın başarısız saydığı satır.
    -- Eşikler core/scenario_analysis.CURRENT_DURATION_SEC/CURRENT_DISTANCE_M ile
    -- aynı olmalı; senkron tests/test_persisted_alignment.py ile kilitli.
    CONSTRAINT ck_success_has_no_failure CHECK (
        outcome <> 'BASARILI'
        OR (duration_sec IS NOT NULL AND distance_m IS NOT NULL
            AND duration_sec < 120 AND distance_m < 60)
        OR (failure_category IS NULL AND failure_reason IS NULL))
) PARTITION BY RANGE (start_time);

COMMENT ON COLUMN ride.duration_sec IS
    'end_time - start_time farkından HESAPLANIR. CSV''deki duration (dakika) kolonu '
    'tutarsız yuvarlanır (%74,6 ceil / %25,3 floor) — KULLANILMAZ. Başarısız '
    'sürüşlerin medyanı 40,6 saniyedir; dakika çözünürlüğü yetersizdir.';
COMMENT ON COLUMN ride.distance_m IS
    'Kanonik kaynak CSV mongo_distance_meters alanıdır. distance_meters ve distance '
    'alanları analiz kararlarında kullanılmaz; mongo alanı boşsa değer NULL kalır. '
    'Saçma büyük değerler (>20km) NULL''lanmaz; OUT_OF_CONTENT ile işaretlenip analizde dışlanır.';
COMMENT ON COLUMN ride.data_quality_flags IS
    'OUT_OF_CONTENT (mesafe>20km VEYA süre>=6sa; IoT/telemetri hatası, analizde dışlanır), '
    'DISTANCE_NULL, TEST_REGION. Satır SİLİNMEZ, işaretlenir; analizde filtrelenir.';

-- Aylık partition'lar. Ingest, eksik ayı otomatik CREATE etmelidir.
CREATE TABLE ride_2026_05 PARTITION OF ride FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE ride_2026_06 PARTITION OF ride FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE ride_2026_07 PARTITION OF ride FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE ride_2026_08 PARTITION OF ride FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
-- Aralık dışına düşen satırlar buraya gider. DAİMA BOŞ olmalı; doluysa partition eksiktir.
CREATE TABLE ride_default PARTITION OF ride DEFAULT;


-- ------------------------------------------------------------
-- 5) feedback
-- ------------------------------------------------------------
CREATE TABLE feedback (
    feedback_id     bigserial PRIMARY KEY,
    ride_id         bigint NOT NULL,
    ride_start_time timestamptz NOT NULL,   -- partition'lı ride'a FK için zorunlu
    rating          smallint CHECK (rating BETWEEN 1 AND 5),
    comment_text    text,
    created_at      timestamptz,
    CONSTRAINT uq_feedback_ride UNIQUE (ride_id, ride_start_time),
    CONSTRAINT fk_feedback_ride FOREIGN KEY (ride_id, ride_start_time)
        REFERENCES ride (ride_id, start_time) ON DELETE CASCADE,
    CONSTRAINT ck_feedback_not_empty CHECK (rating IS NOT NULL OR comment_text IS NOT NULL)
);

COMMENT ON TABLE feedback IS
    'Puan veya yorumdan en az biri varsa satır açılır. Başarısız sürüşlerde puan '
    'verenlerin %94,3''ü 1 yıldız vermiştir (İstanbul, Haziran 2026).';


-- ------------------------------------------------------------
-- 6) VERİ YÜKLEME DENETİMİ
-- ------------------------------------------------------------
CREATE TABLE data_load (
    data_load_id  bigserial PRIMARY KEY,
    file_name     text NOT NULL,
    file_bytes    bigint,
    period_start  date,
    period_end    date,
    rows_read     bigint,
    rows_inserted bigint,
    rows_skipped  bigint,
    rows_flagged  bigint,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        varchar(20) NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING','SUCCESS','FAILED')),
    notes         text
);

COMMENT ON TABLE data_load IS
    'Her CSV yüklemesi bir satır. Tekrar yükleme ride.uq_ride_source ile zaten '
    'engellenir; bu tablo "hangi dönem yüklendi, kaç satır atlandı" sorusunu cevaplar.';


-- ------------------------------------------------------------
-- 7) İNDEKSLER  (partition'lı tabloda tüm partition'lara yayılır)
-- ------------------------------------------------------------
CREATE INDEX idx_ride_vehicle_time ON ride (vehicle_id, start_time);
CREATE INDEX idx_ride_city_time    ON ride (city_id, start_time);

CREATE INDEX idx_ride_failed_vehicle  ON ride (vehicle_id, start_time)
    WHERE outcome = 'BASARISIZ_HARD';
CREATE INDEX idx_ride_failed_category ON ride (city_id, failure_category)
    WHERE outcome = 'BASARISIZ_HARD';
-- classify_all'ın LIMIT'li batch döngüsü buradan okur; predikat Mevcut Kural'la
-- aynı kalmalı, yoksa her batch tam tabloya düşer.
CREATE INDEX idx_ride_unclassified    ON ride (city_id, start_time)
    WHERE failure_category IS NULL AND classified_at IS NULL
      AND (outcome = 'BASARISIZ_HARD'
           OR (duration_sec IS NOT NULL AND distance_m IS NOT NULL
               AND duration_sec < 120 AND distance_m < 60));

CREATE INDEX idx_ride_subregion  ON ride (sub_region_id, outcome);
CREATE INDEX idx_ride_end_reason ON ride (end_reason_id) WHERE end_reason_id IS NOT NULL;
CREATE INDEX idx_ride_user_time  ON ride (user_ref, start_time);
CREATE INDEX idx_ride_load       ON ride (data_load_id);
-- failure_criteria_check(): süre<120sn VE mesafe<60m eşik taraması
CREATE INDEX idx_ride_duration_distance ON ride (duration_sec, distance_m);

CREATE INDEX idx_regulation_city ON regulation (city_id, rule_type) WHERE active;

-- ride.data_load_id -> data_load FK. ALTER olarak eklenir çünkü data_load
-- tablosu ride'dan SONRA tanımlanıyor (inline REFERENCES mümkün değil).
ALTER TABLE ride ADD CONSTRAINT fk_ride_data_load
    FOREIGN KEY (data_load_id) REFERENCES data_load(data_load_id);


-- ------------------------------------------------------------
-- 8) STAGING — ham CSV aynası
-- ------------------------------------------------------------
-- Tüm kolonlar text: COPY sırasında tip hatası yüklemeyi durdurmasın.
-- Her yükleme öncesi TRUNCATE edilir. UNLOGGED = WAL yazmaz, hızlıdır.
CREATE UNLOGGED TABLE stg_rental_raw (
    rental_id text, user_id text, vehicle_id text, plate text, vehicle_type_id text,
    country_id text, country_name text, region_id text, region_name text, sub_region_id text,
    rental_status text, status_label text, start_date_tr text, end_date_tr text,
    checkout_date_tr text, gross_amount text, net_amount text, total_discount_amount text,
    refund_total text, is_refunded text, currency text, reason_id text, message text,
    distance text, duration text, minute_fee text, start_fee text, insurance_fee text,
    is_rental_insuranced text, source_id text, device_id text, is_group_rental text,
    created_on_tr text, updated_on_tr text, mongo_distance_meters text,
    distance_meters text, distance_source text, rental_rate_id text,
    ride_rating text, ride_comment text, rating_created_at_tr text
);


-- ------------------------------------------------------------
-- 9) SEED — yalnızca ülkeler
-- ------------------------------------------------------------
-- Şehir / alt bölge / end_reason ingest tarafından DİNAMİK oluşturulur.
-- Sabit liste seed edilmez: yeni şehir veya ülke geldiğinde proje kırılmamalı.
-- Ülkeler seed edilir, çünkü saat dilimi ve para birimi veriden TÜRETİLEMEZ.

INSERT INTO country (source_country_id, name, iso_code, currency, timezone) VALUES
    (1,   'Türkiye',                'TR', 'TRY', 'Europe/Istanbul'),
    (28,  'Bosnia and Herzegovina', 'BA', 'BAM', 'Europe/Sarajevo'),
    (123, 'Kuzey Makedonya',        'MK', 'MKD', 'Europe/Skopje');

COMMIT;

-- ============================================================
-- DOĞRULAMA
--   SELECT * FROM country;                                   -- 3 satır
--   SELECT count(*) FROM city;                               -- 0 (ingest dolduracak)
--   SELECT relname FROM pg_class WHERE relname LIKE 'ride_2%';
--   SELECT count(*) FROM ride_default;                       -- daima 0 olmalı
-- ============================================================


-- ============================================================
-- BÖLÜM 2/4 — SAHTE ARIZA (FALSE FAULT) MODÜLÜ
-- ============================================================

-- ============================================================
-- "Çalışan cihaz arızalı bildirildi -> 3 boşa görev" zincirini ölçülebilir
-- kılar ve maliyetini PARAMETRİK hesaplar.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) Hüküm ve hipotez tipleri
-- ------------------------------------------------------------
-- "SAHTE" demiyoruz — "ŞÜPHELİ" diyoruz. Mevcut veri kesin hüküm üretemez.
CREATE TYPE fault_verdict AS ENUM (
    'GERCEK_ARIZA_SUPHESI',   -- arıza bildirimi var, araç toparlanmadı
    'SAHTE_ALARM_SUPHESI',    -- arıza bildirimi var, araç sağlam olduğunu kanıtladı
    'BILDIRIM_YOK',           -- başarısız ama arıza bildirimi yok (KONTROL GRUBU)
    'DEGERLENDIRILEMEDI'      -- aracın sonraki sürüşü yok (dönem sonu vb.)
);

CREATE TYPE false_fault_hypothesis AS ENUM (
    'REGULASYON_SUPHESI',     -- 0 m hareket + araç sağlam -> geofence block/cutoff şüphesi
    'GECICI_TEKNIK',          -- unlock/ACK/bağlantı; araç sağlam
    'KULLANICI_HATASI',
    'BELIRSIZ'
);


-- ------------------------------------------------------------
-- 2) Türetilmiş değerlendirme tablosu
-- ------------------------------------------------------------
-- ride PARTITION'lı olduğu için FK bileşik: (ride_id, ride_start_time).
CREATE TABLE false_fault_assessment (
    ride_id         bigint NOT NULL,
    ride_start_time timestamptz NOT NULL,

    -- Kanıt zinciri (sonradan denetlenebilsin)
    fault_reported  boolean NOT NULL,
    report_evidence classification_source NOT NULL,
    vehicle_moved   boolean,                 -- ride.distance_m > 0 ?

    -- "Araç sağlam" kanıtı: AYNI aracın bir sonraki sürüşü
    next_ride_id         bigint,
    next_ride_start_time timestamptz,
    next_ride_gap_min    numeric(10,2),
    next_ride_ok         boolean,
    next_ride_distance_m numeric(12,2),
    healthy_proof        boolean NOT NULL,   -- next_ok AND dist>200m AND gap<=360dk

    verdict    fault_verdict NOT NULL,
    hypothesis false_fault_hypothesis NOT NULL DEFAULT 'BELIRSIZ',

    -- Boşa giden operasyon zinciri (saha görev verisi bağlanınca doldurulur)
    ops_pickup_task_id   varchar(40),
    ops_workshop_task_id varchar(40),
    ops_redeploy_task_id varchar(40),
    wasted_missions      smallint NOT NULL DEFAULT 0 CHECK (wasted_missions BETWEEN 0 AND 3),

    assessed_at      timestamptz NOT NULL DEFAULT now(),
    assessor_version varchar(20) NOT NULL,

    PRIMARY KEY (ride_id, ride_start_time),
    CONSTRAINT fk_ffa_ride FOREIGN KEY (ride_id, ride_start_time)
        REFERENCES ride (ride_id, start_time) ON DELETE CASCADE,

    CONSTRAINT ck_verdict_consistency CHECK (
        (verdict = 'SAHTE_ALARM_SUPHESI'  AND fault_reported AND healthy_proof)
     OR (verdict = 'GERCEK_ARIZA_SUPHESI' AND fault_reported AND NOT healthy_proof)
     OR (verdict = 'BILDIRIM_YOK'         AND NOT fault_reported)
     OR (verdict = 'DEGERLENDIRILEMEDI')),

    -- Regülasyon hipotezi YALNIZCA hareket etmeyen + sağlam araçlar için kurulabilir
    CONSTRAINT ck_regulation_hypothesis CHECK (
        hypothesis <> 'REGULASYON_SUPHESI'
        OR (vehicle_moved = false AND healthy_proof = true))
);

COMMENT ON TABLE false_fault_assessment IS
    'Haziran 2026 İstanbul temel çizgisi: arıza bildirimli 1.731 başarısız sürüşün '
    '%29,5''i sağlam-kanıtı taşır (511 olay / 472 araç). KONTROL GRUBU (bildirimsiz '
    'başarısızlıklar) %42,2 sağlam-kanıtı taşır — yani bildirimler GERÇEK sinyal '
    'içerir, hepsi sahte değildir. Bu tabloya "SAHTE" değil "ŞÜPHELİ" yazılır.';

COMMENT ON COLUMN false_fault_assessment.healthy_proof IS
    'Aracın bir sonraki sürüşü 6 saat içinde, >200m mesafeyle başarılıysa true. '
    'Eşikler analysis katmanında parametriktir; burada karar anındaki değer donar.';

COMMENT ON COLUMN false_fault_assessment.hypothesis IS
    'REGULASYON_SUPHESI yalnızca vehicle_moved=false + healthy_proof=true iken atanır: '
    'yasak bölgede motor kesilmiş, araç 0m gitmiş, ama araç sağlamdır. Veri desteği: '
    'hiç hareket etmeyen arıza bildirimlerinde sağlam-kanıtı %42,8, hareket edenlerde '
    '%27,3. DİKKAT: bu bir HİPOTEZDİR. Kesin kanıt için geofence poligonu + sürüş '
    'başlangıç koordinatı gerekir; mevcut CSV''de ikisi de yoktur.';

CREATE INDEX idx_ffa_verdict    ON false_fault_assessment (verdict);
CREATE INDEX idx_ffa_hypothesis ON false_fault_assessment (hypothesis)
    WHERE verdict = 'SAHTE_ALARM_SUPHESI';


-- ------------------------------------------------------------
-- 3) Operasyon maliyet modeli — PARAMETRİK, değerler koda gömülmez
-- ------------------------------------------------------------
-- BOŞ kurulur. Gerçek maliyetler (saha ekibi ücreti, yakıt, atölye iş gücü)
-- operasyon ekibinden alınıp INSERT edilir. RAKAM UYDURULMAZ.
-- Para birimi ülkeye göre değişir -> currency kolonu zorunlu.
CREATE TABLE ops_cost_model (
    cost_model_id   bigserial PRIMARY KEY,
    country_id      bigint REFERENCES country(country_id),
    city_id         bigint REFERENCES city(city_id),
    mission_type    varchar(20) NOT NULL
        CHECK (mission_type IN ('PICKUP','WORKSHOP','REDEPLOY')),
    labor_cost      numeric(12,2) CHECK (labor_cost >= 0),
    fuel_cost       numeric(12,2) CHECK (fuel_cost  >= 0),
    currency        char(3) NOT NULL,
    avg_minutes     smallint CHECK (avg_minutes >= 0),
    opportunity_cost numeric(12,2),   -- araç sahada olsa üreteceği gelir (opsiyonel)
    effective_from  date NOT NULL DEFAULT current_date,
    source_note     text,             -- örn. "Ops ekibi görüşmesi, 2026-07"
    CONSTRAINT uq_cost_model UNIQUE (city_id, mission_type, effective_from),
    CONSTRAINT ck_scope CHECK (country_id IS NOT NULL OR city_id IS NOT NULL)
);

COMMENT ON TABLE ops_cost_model IS
    'BOŞ KURULUR. Sahte alarm maliyeti = wasted_missions x ilgili mission_type maliyeti. '
    'Parametre gelene kadar analiz "N boşa görev" raporlar, "Y TL" DEMEZ.';


-- ------------------------------------------------------------
-- 4) Raporlama görünümleri  (test bölgeleri daima dışlanır)
-- ------------------------------------------------------------

CREATE VIEW v_false_fault_summary AS
WITH per_ride AS (
    SELECT
        co.name AS ulke,
        ci.name AS sehir,
        a.verdict,
        a.hypothesis,
        a.fault_reported,
        r.vehicle_id,
        a.wasted_missions
    FROM false_fault_assessment a
    JOIN ride    r  ON r.ride_id = a.ride_id AND r.start_time = a.ride_start_time
    JOIN city    ci ON ci.city_id = r.city_id
    JOIN country co ON co.country_id = ci.country_id
    WHERE ci.is_test = false
),
grouped AS (
    SELECT ulke, sehir, verdict, hypothesis,
           count(*)                   AS olay_sayisi,
           count(DISTINCT vehicle_id) AS arac_sayisi,
           sum(wasted_missions)       AS toplam_bosa_gorev
    FROM per_ride
    GROUP BY ulke, sehir, verdict, hypothesis
),
reported_totals AS (
    SELECT ulke, sehir, count(*) AS toplam_bildirim
    FROM per_ride
    WHERE fault_reported
    GROUP BY ulke, sehir
)
SELECT
    g.ulke, g.sehir, g.verdict, g.hypothesis,
    g.olay_sayisi, g.arac_sayisi,
    round(100.0 * g.olay_sayisi / NULLIF(rt.toplam_bildirim, 0), 1) AS bildirimler_icinde_yuzde,
    g.toplam_bosa_gorev
FROM grouped g
LEFT JOIN reported_totals rt ON rt.ulke = g.ulke AND rt.sehir = g.sehir
ORDER BY g.ulke, g.sehir, g.verdict, g.hypothesis;

COMMENT ON VIEW v_false_fault_summary IS
    'Lead sunumunun ana tablosu. Maliyet kolonu YOK — ops_cost_model dolana kadar '
    'görev sayısı raporlanır, para birimi/tutar raporlanmaz.';


CREATE VIEW v_false_fault_by_subregion AS
SELECT
    co.name  AS ulke,
    ci.name  AS sehir,
    sr.source_sub_region_id                                      AS alt_bolge_kodu,
    sr.name                                                      AS alt_bolge_adi,
    count(*)                                                     AS toplam_basarisiz,
    count(*) FILTER (WHERE a.verdict = 'SAHTE_ALARM_SUPHESI')    AS sahte_alarm_suphesi,
    count(*) FILTER (WHERE a.hypothesis = 'REGULASYON_SUPHESI')  AS regulasyon_suphesi
FROM ride r
JOIN false_fault_assessment a
     ON a.ride_id = r.ride_id AND a.ride_start_time = r.start_time
JOIN sub_region sr ON sr.sub_region_id = r.sub_region_id
JOIN city    ci ON ci.city_id = r.city_id
JOIN country co ON co.country_id = ci.country_id
-- outcome daraltması YOK: kümeyi false_fault_assessment ile yapılan INNER JOIN
-- tanımlar; o tablo zaten Mevcut Kural'a göre doldurulur (bkz. db/08).
WHERE ci.is_test = false
GROUP BY co.name, ci.name, sr.source_sub_region_id, sr.name;

COMMENT ON VIEW v_false_fault_by_subregion IS
    'Geofence bölge şüphelisi tespiti. Alt bölge daima (şehir, kod) çifti ile '
    'gruplanır — source_sub_region_id tek başına benzersiz DEĞİLDİR (591/599/605/623 '
    'birden fazla bölgede geçer). Yasak bölge eşlemesi lead''den gelince bu görünüm '
    'hipotezi kanıta çevirir.';

COMMIT;

-- ============================================================
-- DOĞRULAMA
--   SELECT count(*) FROM pg_type WHERE typname IN
--     ('fault_verdict','false_fault_hypothesis');            -- 2
--   SELECT count(*) FROM information_schema.views
--     WHERE table_name LIKE 'v_false_fault%';                -- 2
--   SELECT count(*) FROM ops_cost_model;                     -- 0 (bilerek boş)
-- ============================================================


-- ============================================================
-- BÖLÜM 3/4 — ARAÇ DURUM-DEĞİŞİM DEFTERİ (FLEET STATUS)
-- ============================================================

-- ============================================================
-- Amaç: classify_ride'ın kategorileyemediği başarısız sürüşlere,
-- araç telemetrisinden (IoT durum makinesi) gerçek bir sinyal kaynağı vermek.
-- Kaynak veri: data_raw/Haziran_2026_Status_Change_Log_Kayitlari.csv
-- (4.172.070 satır, 21.917 araç) + data_raw/VehicleStatus.txt (18 değer) +
-- data_raw/VehicleStatusReason.txt (58 değer).
--
-- ADLANDIRMA: Bölüm 1'de zaten `vehicle_status` adında bir ENUM TİPİ var (domain'in
-- basitleştirilmiş AVAILABLE/ON_TRIP/REMOVED/MAINTENANCE durumu). Bu dosyadaki
-- tablolar KASIT olarak `fleet_status_*` önekiyle adlandırılır — hem isim
-- çakışmasını (CREATE TABLE aynı adda örtük bir composite type açar) önler hem
-- de iki farklı kavramı (basit domain durumu vs. IoT'nin 18/58 değerli saha
-- durum makinesi) okura görsel olarak ayırır.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) REFERANS: fleet_status_code  (VehicleStatus.txt, 18 değer)
-- ------------------------------------------------------------
-- Sabit, tam ve küçük bir sözlük (country tablosu gibi) — end_reason'ın
-- aksine dinamik keşfe gerek yok, doğrudan SEED edilir.

CREATE TABLE fleet_status_code (
    status_id   smallint PRIMARY KEY,
    enum_name   varchar(60) NOT NULL,
    description varchar(120) NOT NULL
);

COMMENT ON TABLE fleet_status_code IS
    'Aracın O ANKİ durumu (VehicleStatus.txt). Haziran 2026 dağılımı: Hazır %49,4, '
    'Kullanımda %27,1, Batarya %12,1, Çalıntı %5,3, Şüpheli %1,2.';

INSERT INTO fleet_status_code (status_id, enum_name, description) VALUES
    (0,  'NotDefined',          'Tanımlanmadı'),
    (1,  'LoadedOnVehicle',     'Araca Yüklendi'),
    (2,  'InMaintenance',       'Bakımda'),
    (3,  'Battery',             'Batarya'),
    (4,  'Stolen',              'Çalıntı'),
    (5,  'ShouldBeDistributed', 'Dağıtılmalı'),
    (6,  'Fota',                'FOTA'),
    (7,  'Ready',                'Hazır'),
    (8,  'OutOfUse',            'Kullanım Dışı'),
    (9,  'InUse',               'Kullanımda'),
    (10, 'MobileService',       'Mobil Servis'),
    (11, 'Reserved',            'Rezerve'),
    (12, 'Suspicious',          'Şüpheli'),
    (13, 'MustBeCollected',     'Toplanmalı'),
    (14, 'InManufacturing',     'Üretimde'),
    (15, 'Missing',             'Kayıp'),
    (16, 'Helmet',              'Kask'),
    (17, 'Transfer',            'Transfer');


-- ------------------------------------------------------------
-- 2) REFERANS: fleet_status_reason  (VehicleStatusReason.txt, 58 değer)
--    ↓ KURAL KİTABI ↓ — Kategori-Sonuç Matrisi'nin veri kaynağı.
-- ------------------------------------------------------------
-- category_hint/reason_hint yalnızca AÇIK, tek anlamlı teknik arıza
-- sinyallerine atanır (is_fault_signal=true). Davranışsal/belirsiz kodlar
-- (ör. "BinBin açık" spontane, "Yaya hareketi", yaşam-döngüsü olayları)
-- NULL bırakılır — ŞÜPHELİ≠SAHTE disiplini: yorum yürütülmez.
--
-- priority: aynı zaman penceresinde birden çok arıza-sinyali düşerse en
-- yüksek öncelikli (ör. operatörün elle "Arızalı" işaretlemesi) kazanır.
-- verified=false: bu eşleme mühendis önerisidir, saha ekibi doğrulayana
-- kadar geçicidir. UPDATE ile düzeltilebilir; kod DEĞİŞMEZ (SSoT burada).

CREATE TABLE fleet_status_reason (
    reason_id       smallint PRIMARY KEY,
    enum_name       varchar(60) NOT NULL,
    description     varchar(120) NOT NULL,
    category_hint   failure_category,
    reason_hint     failure_reason,
    is_fault_signal boolean NOT NULL DEFAULT false,
    priority        smallint NOT NULL DEFAULT 0,
    verified        boolean NOT NULL DEFAULT false,
    notes           text,
    CONSTRAINT ck_fault_signal_needs_category CHECK (
        NOT is_fault_signal OR category_hint IS NOT NULL)
);

COMMENT ON TABLE fleet_status_reason IS
    'Durum değişikliğinin SEBEBİ (VehicleStatusReason.txt) + sinyal→kategori '
    'kural kitabı. Haziran 2026 EDA: Şüpheli(12) geçişlerinin 47.570''i (%99) '
    'otomatik job tarafından atanır (created_by=1) — insan gözlemi değil, '
    'algoritma alarmıdır; bu yüzden yalnız kesin teknik kodlar sinyal sayılır.';

COMMENT ON COLUMN fleet_status_reason.verified IS
    'false = mühendis önerisi (bu script tarafından seed edildi). Saha ekibi '
    'doğruladıkça true''ya çekilir; classify_ride kodu bundan ETKİLENMEZ, '
    'yalnız bu tablo güncellenir (end_reason.verified ile aynı desen).';

INSERT INTO fleet_status_reason
    (reason_id, enum_name, description, category_hint, reason_hint, is_fault_signal, priority, notes) VALUES
    (0,  'NotDefined',                                  'Tanımlanmadı',              NULL,      NULL,               false,  0, NULL),
    (1,  'LoadedOntoVehicle',                            'Araca yüklendi',            NULL,      NULL,               false,  0, 'Lojistik/yaşam-döngüsü.'),
    (2,  'RideEnded',                                    'Sürüş bitti',               NULL,      NULL,               false,  0, 'Yaşam-döngüsü; en sık görülen kod (%26,5).'),
    (3,  'BatteryCharged',                                'Batarya doldu',             NULL,      NULL,               false,  0, 'Normal şarj döngüsü.'),
    (4,  'UnderMaintenance',                              'Bakımda',                   NULL,      NULL,               false,  0, 'Zaten operasyon eylemi; sürüş-sinyali değil.'),
    (5,  'FinalCheck',                                    'Son kontrol',               NULL,      NULL,               false,  0, NULL),
    (6,  'OutOfMaintenance',                              'Bakım çıkışı',              NULL,      NULL,               false,  0, NULL),
    (7,  'NoCommunicationFor30Min',                       '30 dk. iletişim yok',       'TEKNIK',  'CONNECTION_LOST',  true,  70, 'Uzamış haberleşme kaybı — açık teknik sinyal. Haziran 2026 ölçümünde hiçbir sürüş penceresine düşmedi (lift hesaplanamadı).'),
    (8,  'LowBattery',                                    'Batarya az',                NULL,      NULL,               false,  0, 'SİNYAL DEĞİL. Ölçüm (2026-07-21): başarısızda %9,34 · başarılıda %12,35 → lift 0,8x. Başarısızlıkla TERS korelasyonlu; batarya normal kullanımda azalır. Arıza sayılırsa kategori uydurulmuş olur.'),
    (9,  'BatteryDepleted',                                'Batarya bitti',             'TEKNIK',  'LOW_BATTERY',      true,  90, 'Ölçüm (2026-07-21): lift 0,5x — istatistiksel ayırt ediciliği YOK. Buna rağmen İŞ KARARIYLA sinyal tutuldu: batarya bitmesi saha ekibini değişim görevine çıkarır, yani gerçek bir operasyon doğurur (bir çeşit tamir). Sahte alarm sayılmamalı.'),
    (10, 'BatteryGood',                                    'Batarya iyi',               NULL,      NULL,               false,  0, 'Sağlıklı sinyal, arıza değil.'),
    (11, 'BatteryCoverOpen',                               'Batarya kapak açık',        NULL,      NULL,               false,  0, 'Bakım erişimi olabilir; tek başına arıza kanıtı değil.'),
    (12, 'BatteryCoverClosed',                             'Batarya kapak kapatıldı',   NULL,      NULL,               false,  0, NULL),
    (13, 'BatteryFull',                                    'Batarya tam',               NULL,      NULL,               false,  0, NULL),
    (14, 'Open',                                           'BinBin açık',               NULL,      NULL,               false,  0, 'Şüpheli(12) geçişlerinin en büyük tetikleyicisi (30.158) ama spontane/belirsiz — kategori UYDURULMAZ.'),
    (15, 'Closed',                                         'BinBin kapatıldı',          NULL,      NULL,               false,  0, NULL),
    (16, 'BMS',                                            'BMS',                       NULL,      NULL,               false,  0, 'Bileşen adı, olay tanımı değil — tek başına yorumlanamaz.'),
    (17, 'BMSFirmwareUpdateCompleted',                     'BMS Fota bitti',            NULL,      NULL,               false,  0, NULL),
    (18, 'InCar',                                          'Araç içinde',               NULL,      NULL,               false,  0, 'Lojistik (nakliye).'),
    (19, 'TheftLockActivated',                             'Çalıntı kapatıldı',         NULL,      NULL,               false,  0, 'Çalıntı-akışına ait; cihaz arızası iddiası değil.'),
    (20, 'PedestrianMovement',                             'Yaya hareketi',             NULL,      NULL,               false,  0, 'Belirsiz — geofence/regülasyon kanıtı için koordinat gerekir (yok).'),
    (21, 'EnvironmentalSensor',                            'Çevrebirim',                NULL,      NULL,               false,  0, 'Bileşen adı, olay değil.'),
    (22, 'EnvironmentalSensorFirmwareUpdateCompleted',     'Çevrebirim Fota bitti',     NULL,      NULL,               false,  0, NULL),
    (23, 'Deposited',                                      'Depoya indirildi',          NULL,      NULL,               false,  0, 'Lojistik.'),
    (24, 'Driver',                                         'Driver',                    NULL,      NULL,               false,  0, 'Bileşen adı, olay değil.'),
    (25, 'DriverFirmwareUpdateCompleted',                  'Driver Fota bitti',         NULL,      NULL,               false,  0, NULL),
    (26, 'FirmwareUpdateCompleted',                        'FOTA bitti',                NULL,      NULL,               false,  0, NULL),
    (27, 'IoT',                                            'IoT',                       NULL,      NULL,               false,  0, 'Bileşen adı, olay değil.'),
    (28, 'IoTFirmwareUpdate',                               'IoT FOTA',                  NULL,      NULL,               false,  0, NULL),
    (29, 'IoTFirmwareUpdateCompleted',                     'IoT Fota bitti',            NULL,      NULL,               false,  0, NULL),
    (30, 'IoTCableUnplugged',                              'IoT kablo söküldü',         'TEKNIK',  'IOT_FAULT',        true,  65, 'Fiziksel donanım kopukluğu. Ölçüm (2026-07-21): lift 2,6x — sınırda ama fiziksel kopukluk saha müdahalesi doğurur; iş kararıyla tutuldu.'),
    (31, 'IoTCablePlugged',                                 'IoT kablo takıldı',         NULL,      NULL,               false,  0, 'Toparlanma sinyali, arıza değil.'),
    (32, 'CommunicationEstablished',                        'İletişim geldi',            NULL,      NULL,               false,  0, 'Toparlanma sinyali.'),
    (33, 'NoCommunication',                                 'İletişim yok',              'TEKNIK',  'CONNECTION_LOST',  true,  75, 'Ölçüm (2026-07-21): başarısızda %1,02 · başarılıda %0,03 → lift 34,2x. En güçlü ikinci sinyal.'),
    (34, 'Lost',                                            'Kayboldu',                  'TEKNIK',  'CONNECTION_LOST',  true,  80, 'GPS/haberleşme kaybı; çalıntıdan (19) ayrı kod. Haziran 2026''da hiçbir sürüş penceresine düşmedi.'),
    (35, 'LockOpen',                                        'Kilit açık',                'TEKNIK',  'LOCK_JAM',         true,  60, 'Şüpheli(12) geçişinde 2. en sık tetikleyici (8.285). Ölçüm (2026-07-21): lift 2,3x — sınırda ama saha müdahalesi doğurur; iş kararıyla tutuldu.'),
    (36, 'LockEngaged',                                     'Kilit takıldı',             NULL,      NULL,               false,  0, 'Normal/toparlanma.'),
    (37, 'LocationReceived',                                'Konum geldi',               NULL,      NULL,               false,  0, 'Toparlanma sinyali.'),
    (38, 'Status1',                                         'Durum 1',                   NULL,      NULL,               false,  0, 'Anlamı opak; yorum yürütülmez.'),
    (39, 'LocationUnavailable',                             'Konumsuz',                  'TEKNIK',  'GPS_NO_FIX',       true,  80, 'Ölçüm (2026-07-21): lift 184,9x — en keskin sinyal, ama hacmi çok küçük (9 sürüş).'),
    (40, 'OutOfUse',                                        'Kullanım dışı',             NULL,      NULL,               false,  0, 'İdari durum, arıza tanımı değil.'),
    (41, 'CustomerRide',                                    'Müşteri sürüşü',            NULL,      NULL,               false,  0, 'Yaşam-döngüsü; sürüş başlangıcını işaretler (%26,3).'),
    (42, 'Reserved',                                        'Rezerve',                   NULL,      NULL,               false,  0, NULL),
    (43, 'ReservationEnded',                                'Rezerve sonlandı',          NULL,      NULL,               false,  0, NULL),
    (44, 'DeployedToField',                                 'Sahaya dağıtıldı',          NULL,      NULL,               false,  0, 'Lojistik.'),
    (45, 'TestRide',                                        'Test sürüşü',               NULL,      NULL,               false,  0, 'Gerçek müşteri sürüşü değil.'),
    (46, 'Faulty',                                          'Arızalı',                   'TEKNIK',  'IOT_FAULT',        true, 100, 'Operatörün elle işaretlediği en kesin sinyal; her zaman Toplanmalı(13) ile eşleşir. Ölçüm (2026-07-21): başarısızda %0,52 · başarılıda %0,03 → lift 19,3x.'),
    (47, 'Transfer',                                        'Transfer',                  NULL,      NULL,               false,  0, 'Lojistik.'),
    (48, 'InManufacturing',                                 'Üretimde',                  NULL,      NULL,               false,  0, NULL),
    (49, 'OutOfManufacturing',                              'Üretimden çıktı',           NULL,      NULL,               false,  0, NULL),
    (50, 'DefectsRemoved',                                  'Kusurludan çıktı',          NULL,      NULL,               false,  0, 'Toparlanma (arıza giderildi).'),
    (51, 'Defective',                                       'Kusurlu',                   'TEKNIK',  'IOT_FAULT',        true,  95, 'Faulty(46) ile birlikte en kesin operatör sinyali. Haziran 2026''da hiçbir sürüş penceresine düşmedi.'),
    (52, 'Status2',                                         'Durum 2',                   NULL,      NULL,               false,  0, 'Anlamı opak; yorum yürütülmez.'),
    (53, 'Missing',                                         'Kayıp',                     NULL,      NULL,               false,  0, 'Çalıntı/kayıp akışına yakın; cihaz arızası iddiası değil.'),
    (54, 'BLE',                                             'BLE',                       NULL,      NULL,               false,  0, 'Bileşen adı, olay değil.'),
    (55, 'BLEFirmwareUpdateCompleted',                     'BLE Fota bitti',            NULL,      NULL,               false,  0, NULL),
    (56, 'NoHelmet',                                        'Kask yok',                  NULL,      NULL,               false,  0, 'Araç arızasıyla ilgisiz.'),
    (57, 'IsIdle',                                          'Atıl',                      NULL,      NULL,               false,  0, 'Davranışsal/beklemede; belirsiz.');

CREATE INDEX idx_fleet_status_reason_signal ON fleet_status_reason (reason_id)
    WHERE is_fault_signal;


-- ------------------------------------------------------------
-- 3) ANA TABLO: fleet_status_event  — AYLIK PARTITION
-- ------------------------------------------------------------
-- ride ile birebir desen: partition anahtarı (created_on) PK'ya dahil.
-- event_id SERIAL DEĞİL — kaynak sistemin kendi 'id' kolonu (zaten
-- benzersiz bigint); bu idempotent yeniden-yüklemeyi ON CONFLICT DO NOTHING
-- ile basitleştirir.

CREATE TABLE fleet_status_event (
    event_id                  bigint NOT NULL,           -- kaynak CSV 'id'
    vehicle_id                bigint NOT NULL REFERENCES vehicle(vehicle_id),
    status_id                 smallint NOT NULL REFERENCES fleet_status_code(status_id),
    status_reason_id          smallint REFERENCES fleet_status_reason(reason_id),
    previous_status_id        smallint REFERENCES fleet_status_code(status_id),
    previous_status_reason_id smallint REFERENCES fleet_status_reason(reason_id),
    description                text,                      -- kaynak job adı (ör. UpdateReadyStatusJob)
    created_by                 smallint NOT NULL,          -- kaynak aktör kodu (1/2/3); anlamı doğrulanmadı
    created_on                 timestamptz NOT NULL,       -- PARTITION ANAHTARI, kaynakta zaten +03
    data_load_id                bigint REFERENCES data_load(data_load_id),
    PRIMARY KEY (event_id, created_on)
) PARTITION BY RANGE (created_on);

COMMENT ON TABLE fleet_status_event IS
    'Haziran 2026: 4.172.070 satır, 21.917 araç (sürülen araçların %100''ü + '
    '1.436 hiç sürülmemiş araç). Sürüşlerle vehicle_id+zaman üzerinden ~%99 '
    'eşleşir (başlangıç %99,9 / bitiş %99,3, ±3sn pencerede).';
COMMENT ON COLUMN fleet_status_event.created_by IS
    'Gözlemlenen değerler: 1=otomatik job (Şüpheli/Çalıntı/Batarya''nın %99''u), '
    '2=sürüş akışı (Kullanımda↔Hazır), 3=saha/lojistik (Araca Yüklendi, Bakımda).';

CREATE TABLE fleet_status_event_2026_06 PARTITION OF fleet_status_event
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE fleet_status_event_2026_07 PARTITION OF fleet_status_event
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
-- Aralık dışına düşen satırlar buraya gider. DAİMA BOŞ olmalı; doluysa partition eksiktir.
CREATE TABLE fleet_status_event_default PARTITION OF fleet_status_event DEFAULT;

-- Sinyal-join (queries.analysis_timeline) bu indeksi tarar: verilen aracın
-- verilen zaman penceresindeki olaylarını bulur.
CREATE INDEX idx_fleet_status_event_vehicle_time ON fleet_status_event (vehicle_id, created_on);
CREATE INDEX idx_fleet_status_event_reason ON fleet_status_event (status_reason_id)
    WHERE status_reason_id IS NOT NULL;
CREATE INDEX idx_fleet_status_event_load ON fleet_status_event (data_load_id);


-- ------------------------------------------------------------
-- 4) STAGING — ham CSV aynası
-- ------------------------------------------------------------
CREATE UNLOGGED TABLE stg_status_raw (
    id text, vehicle_id text, status_id text, status_reason_id text,
    previous_status_id text, previous_status_reason_id text,
    description text, created_by text, created_on text
);


-- ------------------------------------------------------------
-- 5) RAPORLAMA GÖRÜNÜMÜ — Kategori-Sonuç Matrisi'nin SQL tarafı
-- ------------------------------------------------------------
-- CLI'daki asıl Kategori-Sonuç Matrisi (kategori × verdict, scenario_analysis
-- motorunda hesaplanır) için ham girdi değildir; ad-hoc SQL keşfi ve kural
-- kitabının sahaya sunumu içindir.
CREATE VIEW v_fleet_status_signal_matrix AS
SELECT
    fsr.reason_id, fsr.enum_name, fsr.description,
    fsr.category_hint, fsr.reason_hint, fsr.is_fault_signal, fsr.verified,
    count(e.event_id)          AS event_count,
    count(DISTINCT e.vehicle_id) AS vehicle_count
FROM fleet_status_reason fsr
LEFT JOIN fleet_status_event e ON e.status_reason_id = fsr.reason_id
GROUP BY fsr.reason_id, fsr.enum_name, fsr.description,
         fsr.category_hint, fsr.reason_hint, fsr.is_fault_signal, fsr.verified
ORDER BY event_count DESC;

COMMENT ON VIEW v_fleet_status_signal_matrix IS
    'Kural kitabının olay sayılarıyla birlikte görünümü. is_fault_signal=true '
    'satırları classify_ride''ın yeni REASON_CODE adımını besler.';

COMMIT;

-- ============================================================
-- DOĞRULAMA
--   SELECT count(*) FROM fleet_status_code;                  -- 18
--   SELECT count(*) FROM fleet_status_reason;                -- 58
--   SELECT count(*) FROM fleet_status_reason WHERE is_fault_signal; -- 9
--   SELECT count(*) FROM fleet_status_event_default;         -- daima 0 olmalı
--   SELECT * FROM v_fleet_status_signal_matrix WHERE is_fault_signal LIMIT 20;
-- ============================================================

-- ============================================================
-- BÖLÜM 4/4 — BAKIM GEÇMİŞİ + KOORDİNAT/GEOFENCE
-- ============================================================

-- ------------------------------------------------------------
-- Arıza tipi boyutu — RAPOR içindir, sınıflandırma sinyali DEĞİLDİR.
-- Ölçüm tip bazında atıf yapılamayacağını gösterdi: aynı ziyarette ortalama 3 tip
-- kaydediliyor ve kozmetik olan gerçek arızaya biniyor ('Sticker (Yıpranmış)'
-- tek-tipli ziyaretlerde bile 3,29x). Sınıflandırma AGREGE sinyali kullanır:
-- "arıza-canlı penceresinde bakım bildirimi var" (24sa, lift 4,94x).
-- ------------------------------------------------------------
CREATE TABLE damage_sub_type (
    damage_sub_type_id  integer PRIMARY KEY,
    name                text NOT NULL,
    measured_lift       numeric(5,2),
    verified            boolean NOT NULL DEFAULT false,
    notes               text,
    first_seen_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE maintenance_event (
    maintenance_id   bigint NOT NULL,
    vehicle_id       bigint NOT NULL REFERENCES vehicle(vehicle_id),
    damage_sub_type_id integer REFERENCES damage_sub_type(damage_sub_type_id),
    reported_at      timestamptz NOT NULL,
    warehouse_entry_at timestamptz,
    repaired_at      timestamptz,
    result_code      smallint,
    part_count       integer,
    data_load_id     bigint REFERENCES data_load(data_load_id),
    PRIMARY KEY (maintenance_id, reported_at)
) PARTITION BY RANGE (reported_at);

COMMENT ON TABLE maintenance_event IS
    'Düzeltici bakım defteri. Zaman damgaları İŞ AKIŞI DEĞİL veri girişidir: '
    'bildirim→bakım medyanı 0,01 saat (~36 sn), depo girişi bildirimden önce '
    'olabiliyor. Süreç metriği çıkarılmaz; yalnız reported_at güvenilirdir.';

CREATE TABLE maintenance_event_2026_05 PARTITION OF maintenance_event
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE maintenance_event_2026_06 PARTITION OF maintenance_event
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE maintenance_event_2026_07 PARTITION OF maintenance_event
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE maintenance_event_default PARTITION OF maintenance_event DEFAULT;

CREATE INDEX idx_maintenance_vehicle_time ON maintenance_event (vehicle_id, reported_at);
CREATE INDEX idx_maintenance_load ON maintenance_event (data_load_id);

-- ------------------------------------------------------------
-- Geofence boyutu. area_type/type anlamları BİLİNMİYOR (kaynak sistemde enum
-- sözlüğü yok) → category_hint NULL, verified=false. Ölçüm area_type=4'ü
-- ayırt edici buldu (lift 4,02x) ama NE OLDUĞU doğrulanana kadar kategori
-- ATANMAZ; sözlük gelince yalnız bu tablo UPDATE edilir, kod değişmez.
-- ------------------------------------------------------------
CREATE TABLE geofence (
    geofence_id    bigint PRIMARY KEY,
    name           text,
    geofence_type  smallint,
    area_type      smallint,
    sqr_km         numeric(12,2),
    category_hint  failure_category,
    verified       boolean NOT NULL DEFAULT false,
    notes          text
);

-- Sürüşün fiziksel konum kanıtı. ride ile bileşik FK (partition anahtarı dahil).
CREATE TABLE ride_geo (
    ride_id         bigint NOT NULL,
    ride_start_time timestamptz NOT NULL,
    start_lat       double precision,
    start_lon       double precision,
    end_lat         double precision,
    end_lon         double precision,
    displacement_m  double precision,
    end_geofence_id bigint REFERENCES geofence(geofence_id),
    data_load_id    bigint REFERENCES data_load(data_load_id),
    PRIMARY KEY (ride_id, ride_start_time),
    FOREIGN KEY (ride_id, ride_start_time) REFERENCES ride(ride_id, start_time)
);

COMMENT ON COLUMN ride_geo.displacement_m IS
    'Başlangıç→bitiş kuş uçuşu mesafe (haversine, ingest''te hesaplanır). '
    'Odometreden BAĞIMSIZ ölçümdür: vehicle_moved artık çıkarım değil kanıttır.';

CREATE INDEX idx_ride_geo_geofence ON ride_geo (end_geofence_id)
    WHERE end_geofence_id IS NOT NULL;

-- ------------------------------------------------------------
-- STAGING — ham CSV aynası
-- ------------------------------------------------------------
CREATE UNLOGGED TABLE stg_maintenance_raw (
    bakim_id text, scooter_id text, vehicle_id text, plate text,
    haziran_2026_basarili_surus_sayisi text, region_id text, region_name text,
    damage_sub_type_id text, damage_sub_type_name text,
    bakim_durumu text, bakim_durumu_aciklamasi text,
    bakim_sonucu text, bakim_sonucu_aciklamasi text,
    ariza_bildirim_zamani_istanbul text, depo_giris_zamani_istanbul text,
    onarim_baslangic_zamani_istanbul text, parca_bekleme_zamani_istanbul text,
    bakim_zamani_istanbul text, sonuc_kontrol_zamani_istanbul text,
    warehouse_id text, parca_kalemi text, parca_adedi text, toplam_parca_maliyeti text
);

CREATE UNLOGGED TABLE stg_geo_raw (
    rental_id text, user_id text, vehicle_id text, plate text, vehicle_type_id text,
    country_id text, country_name text, region_id text, region_name text,
    sub_region_id text, rental_status text, status_label text,
    start_date_tr text, end_date_tr text, distance text, duration text,
    mongo_match text, mongo_vehicle_id text, mongo_is_completed text,
    start_longitude text, start_latitude text, end_longitude text, end_latitude text,
    end_geofence_id text, end_geofence_name text, end_geofence_type text,
    end_geofence_area_type text, end_geofence_sqr_km text, end_geofence_match_count text,
    end_geofence_all_ids text, end_geofence_all_names text, end_geofence_all_types text
);

-- ============================================================
-- DOĞRULAMA
--   SELECT count(*) FROM damage_sub_type;                        -- 64 (rapor boyutu)
--   SELECT count(*) FROM maintenance_event_default;              -- daima 0 olmalı
--   SELECT count(*) FROM geofence WHERE verified;                -- 0 (sözlük yok)
-- ============================================================
