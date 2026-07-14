-- ============================================================
-- Binbin — SIFIRLA ve KUR  (db/01_reset_ve_kurulum.sql)
--
-- !!! DIKKAT: Bu script public semasindaki HER SEYI SILER.
--     Yalnizca tablolar BOSKEN calistir (once 00_durum_tespiti.sql).
--
-- Neden reset: v1 -> v2 gecisinde ride tablosu PARTITION'li hale geliyor.
-- PostgreSQL bir tabloyu ALTER ile partition'li yapamaz. Tablolar bos
-- oldugu icin sifirlamak sifir risklidir ve en temiz yoldur.
--
-- Calistirma (pgAdmin Query Tool, binbin veritabani SECILI):
--   1) ROLLBACK;                  -- varsa kirli transaction'i temizle
--   2) bu dosyanin tamami (F5)
--   3) db/02_false_fault.sql      -- ikinci dosya
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

CREATE TYPE failure_category AS ENUM ('TEKNIK','REGULASYON','KULLANICI','ODEME','SISTEM');
-- Bilinçli karar: 'BILINMIYOR' YOK. Sınıflandırılamayan başarısızlık → NULL.

CREATE TYPE payment_status AS ENUM ('OK','DECLINED','INSUFFICIENT_BALANCE','PREAUTH_FAILED');

CREATE TYPE failure_reason AS ENUM (
    'UNLOCK_ACK_TIMEOUT','GPS_NO_FIX','CONNECTION_LOST','IOT_FAULT','LOW_BATTERY',
    'BMS_FAULT','MOTOR_ERROR','LOCK_JAM','QR_SCAN_FAIL','BLE_PAIR_FAIL','NO_RIDE_ZONE',
    'SLOW_ZONE_THROTTLE','NO_PARK_BLOCK','OPERATING_HOUR_BLOCK','CITY_BOUNDARY_CUTOFF',
    'USER_CANCELLED','PARKING_PHOTO_FAIL','PAYMENT_DECLINED','INSUFFICIENT_BALANCE',
    'PREAUTH_FAILED','BACKEND_ERROR');

CREATE TYPE classification_source AS ENUM (
    'FIELD_SIGNAL','REASON_CODE','TEXT_MESSAGE','TEXT_COMMENT','NONE');


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
    CONSTRAINT ck_success_has_no_failure CHECK (
        outcome <> 'BASARILI' OR (failure_category IS NULL AND failure_reason IS NULL))
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
CREATE INDEX idx_ride_unclassified    ON ride (city_id, start_time)
    WHERE outcome = 'BASARISIZ_HARD' AND failure_category IS NULL;

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
