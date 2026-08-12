-- ============================================================
-- Binbin — KALICI TABLOLARIN CANLI MOTORLA HİZALANMASI
-- db/08_align_persisted_with_current_rule.sql
--
-- NEDEN: Kalıcı yazma yolu (data/classify.py, data/assess.py) ile canlı analiz
-- motoru (core/scenario_analysis.py, `analyze`) FARKLI bir "başarısızlık"
-- tanımı kullanıyordu:
--
--   yol                      tanım                                      sürüş
--   -----------------------  ---------------------------------------  ------
--   analyze (Mevcut Kural)   outcome='BASARISIZ_HARD' VEYA            65.963
--                            (duration_sec<120 VE distance_m<60)
--   classify_all/assess_all  outcome='BASARISIZ_HARD'                 52.753
--   FARK (ÖLÇÜLDÜ)           outcome='BASARILI' + süre<120 + mesafe<60 13.210
--
-- Bu 13.210 sürüş raporun §9 bulgularının İÇİNDEDİR (canlı motordan gelirler),
-- ama ride.failure_category ve false_fault_assessment'ta YOKTU. §9 DEĞİŞMEZ;
-- düzelen kalıcı tablolardır.
--
-- DEĞİŞİKLİK:
--   1) ck_success_has_no_failure  -> Mevcut Kural istisnasını tanır
--   2) idx_ride_unclassified      -> aynı predikatla genişler
--   3) v_false_fault_by_subregion -> outcome daraltmasını bırakır
--
-- Yeni CHECK eskisinin ÜSTKÜMESİDİR (fazladan bir OR terimi), mevcut hiçbir
-- satır ihlal edemez — NOT VALID + VALIDATE gerekmez. 10M+ satıra çıkıldığında
-- yeniden değerlendirilmeli.
--
-- ⚠️ SIRA: bu betik `classify --refresh` ve `assess --refresh`ten ÖNCE
-- çalıştırılır. Aksi hâlde BASARILI satıra yazan UPDATE CheckViolation verir,
-- reset çalıştığı için kalıcı tablo BOŞ kalır.
--
-- ÖNKOŞUL: db/01, db/02 çalıştırılmış olmalı.
-- İDEMPOTENT: tekrar çalıştırılabilir (pg_constraint/pg_class sorgulanır;
--             `ADD CONSTRAINT IF NOT EXISTS` PostgreSQL'de YOKTUR).
-- Çalıştırma: pgAdmin Query Tool, binbin veritabanı seçili, tamamını yapıştır.
-- ============================================================

BEGIN;

SET LOCAL lock_timeout      = '10s';
SET LOCAL statement_timeout = '5min';

-- ------------------------------------------------------------
-- 1) ck_success_has_no_failure — Mevcut Kural istisnası
-- ------------------------------------------------------------
-- Kısıt PARENT `ride` üzerinde tanımlıdır ve tüm partition'lara miras iner;
-- partition'dan DROP "cannot drop inherited constraint" verir. Partition sayısı
-- sabit değildir (ingest.py çalışma zamanında ay ekler) — parent üzerinden
-- özyineleme bunu kendiliğinden kapsar.
--
-- IS NOT NULL guard'ı ŞARTTIR: guard olmadan ölçümsüz bir BASARILI satırda eşik
-- terimi NULL döner ve CHECK NULL'ı geçirir. Canlı motor da ölçümsüz satırı
-- başarısız SAYMAZ (FailureScenario.status) — iki taraf birebir aynıdır.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.ride'::regclass
          AND conname  = 'ck_success_has_no_failure'
          AND pg_get_constraintdef(oid) LIKE '%duration_sec%'
    ) THEN
        ALTER TABLE ride DROP CONSTRAINT IF EXISTS ck_success_has_no_failure;
        ALTER TABLE ride ADD CONSTRAINT ck_success_has_no_failure CHECK (
            outcome <> 'BASARILI'
            OR (duration_sec IS NOT NULL AND distance_m IS NOT NULL
                AND duration_sec < 120 AND distance_m < 60)
            OR (failure_category IS NULL AND failure_reason IS NULL));
    END IF;
END $$;

-- ------------------------------------------------------------
-- 2) idx_ride_unclassified — aynı predikatla genişler
-- ------------------------------------------------------------
-- classify_all'ın LIMIT'li batch döngüsü bu kısmî indeksten okur. Predikat dar
-- kalırsa yeni adaylar indeks dışında kalır ve her batch tam taramaya döner.
-- Partition'lı tabloda CREATE INDEX CONCURRENTLY desteklenmez.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = 'idx_ride_unclassified'
          AND n.nspname = 'public'
          AND pg_get_indexdef(c.oid) LIKE '%duration_sec%'
    ) THEN
        DROP INDEX IF EXISTS idx_ride_unclassified;
        CREATE INDEX idx_ride_unclassified ON ride (city_id, start_time)
            WHERE failure_category IS NULL AND classified_at IS NULL
              AND (outcome = 'BASARISIZ_HARD'
                   OR (duration_sec IS NOT NULL AND distance_m IS NOT NULL
                       AND duration_sec < 120 AND distance_m < 60));
    END IF;
END $$;

-- ------------------------------------------------------------
-- 3) v_false_fault_by_subregion — Mevcut Kural'ı görsün
-- ------------------------------------------------------------
-- Eski WHERE'deki r.outcome='BASARISIZ_HARD' daraltması kaldırıldı: kümeyi
-- zaten false_fault_assessment ile yapılan INNER JOIN tanımlar. 120/60'ı buraya
-- da kopyalamak üçüncü bir senkron noktası yaratırdı.
CREATE OR REPLACE VIEW v_false_fault_by_subregion AS
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
WHERE ci.is_test = false
GROUP BY co.name, ci.name, sr.source_sub_region_id, sr.name;

COMMIT;

-- ============================================================
-- DOĞRULAMA
--   1) Kısıt parent VE tüm partition'larda yeni tanıma geçti mi?
--      SELECT conrelid::regclass AS tablo, coninhcount,
--             pg_get_constraintdef(oid) LIKE '%duration_sec%' AS yeni_tanim
--        FROM pg_constraint WHERE conname = 'ck_success_has_no_failure'
--       ORDER BY 1;
--      -- 'ride' coninhcount=0 · her partition coninhcount=1 · hepsinde yeni_tanim = t
--
--   2) SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_ride_unclassified';
--      -- 'duration_sec < 120' ve 'distance_m < 60' İÇERMELİ
--
--   3) Hedef küme (canlı motorla aynı olmalı):
--      SELECT count(*) FROM ride r JOIN city ci ON ci.city_id = r.city_id
--       WHERE ci.is_test = false
--         AND r.outcome IN ('BASARILI','BASARISIZ_HARD')
--         AND NOT ('OUT_OF_CONTENT' = ANY(r.data_quality_flags))
--         AND (r.outcome = 'BASARISIZ_HARD'
--              OR (r.duration_sec IS NOT NULL AND r.distance_m IS NOT NULL
--                  AND r.duration_sec < 120 AND r.distance_m < 60));   -- 65.963
--
-- SONRAKİ ADIM (ŞART, TAM BU SIRAYLA):
--   .\.venv\Scripts\python.exe -m binbin.cli classify --refresh
--   .\.venv\Scripts\python.exe -m binbin.cli assess   --refresh
-- ============================================================
