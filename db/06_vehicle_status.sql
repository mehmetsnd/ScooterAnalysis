-- ============================================================
-- Binbin — EK ŞEMA: Araç Durum-Değişim Defteri (Fleet Status)
-- db/06_vehicle_status.sql
--
-- Amaç: classify_ride'ın SINYALSIZ bıraktığı başarısız sürüşlere (%91,5),
-- araç telemetrisinden (IoT durum makinesi) gerçek bir sinyal kaynağı vermek.
-- Kaynak veri: data_raw/Haziran_2026_Status_Change_Log_Kayitlari.csv
-- (4.172.070 satır, 21.917 araç) + data_raw/VehicleStatus.txt (18 değer) +
-- data_raw/VehicleStatusReason.txt (58 değer).
--
-- ADLANDIRMA: 01'de zaten `vehicle_status` adında bir ENUM TİPİ var (domain'in
-- basitleştirilmiş AVAILABLE/ON_TRIP/REMOVED/MAINTENANCE durumu). Bu dosyadaki
-- tablolar KASIT olarak `fleet_status_*` önekiyle adlandırılır — hem isim
-- çakışmasını (CREATE TABLE aynı adda örtük bir composite type açar) önler hem
-- de iki farklı kavramı (basit domain durumu vs. IoT'nin 18/58 değerli saha
-- durum makinesi) okura görsel olarak ayırır.
--
-- ÖNKOŞUL: db/01_reset_ve_kurulum.sql VE db/02_false_fault.sql çalıştırılmış
-- olmalı (failure_category/failure_reason enum'larını buradan devralır).
-- Çalıştırma: pgAdmin Query Tool, binbin veritabanı seçili, tamamını yapıştır.
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
--    ↓ KURAL KİTABI ↓ — Regülasyon Matrisi'nin veri kaynağı.
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
-- 5) RAPORLAMA GÖRÜNÜMÜ — Regülasyon Matrisi'nin SQL tarafı
-- ------------------------------------------------------------
-- CLI'daki asıl Regülasyon Matrisi (kategori × verdict, scenario_analysis
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
