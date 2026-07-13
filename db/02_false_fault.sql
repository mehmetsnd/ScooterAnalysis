-- ============================================================
-- Binbin — EK ŞEMA: Sahte Arıza Alarmı (False Fault) modülü
-- db/02_false_fault.sql   (v2 — partition'lı ride ile uyumlu, çok ülkeli)
--
-- Amaç: "Çalışan cihaz arızalı bildirildi -> 3 boşa görev" zincirini
-- ölçülebilir kılmak ve maliyetini PARAMETRİK hesaplamak.
--
-- ÖNKOŞUL: db/01_reset_ve_kurulum.sql çalıştırılmış olmalı.
-- Çalıştırma: pgAdmin Query Tool, binbin veritabanı seçili, tamamını yapıştır.
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
WHERE r.outcome = 'BASARISIZ_HARD' AND ci.is_test = false
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
