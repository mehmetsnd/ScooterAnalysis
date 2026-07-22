-- ============================================================
-- Binbin — KURAL KİTABI REVİZYONU #1 (Haziran 2026 ölçümü sonrası)
-- db/07_signal_rulebook_revision.sql
--
-- NEDEN: db/06 ilk kez yüklendikten sonra sinyallerin AYIRT EDİCİLİĞİ ölçüldü
-- (her kodun başarısız sürüşlerde vs. başarılı sürüşlerde görülme sıklığı = lift).
-- Ölçüm, bazı kodların başarısızlığı hiç açıklamadığını gösterdi.
--
--   kod  neden               başarısızda  başarılıda    lift
--   ---  ------------------  -----------  ----------  ------
--   39   Konumsuz                  %0,02      %0,000   184,9x   ✓ tut
--   33   İletişim yok              %1,02      %0,030    34,2x   ✓ tut
--   46   Arızalı                   %0,52      %0,027    19,3x   ✓ tut
--   30   IoT kablo söküldü         %1,84      %0,706     2,6x   ✓ tut (iş kararı)
--   35   Kilit açık                %1,28      %0,556     2,3x   ✓ tut (iş kararı)
--    9   Batarya bitti             %1,00      %1,833     0,5x   ✓ tut (İŞ KARARI, aşağıda)
--    8   Batarya az                %9,34     %12,354     0,8x   ✗ ÇIKAR
--
-- DEĞİŞİKLİK: yalnız reason 8 (`Batarya az`) sinyal listesinden çıkarılır. Başarılı
-- sürüşlerin çevresinde başarısızlardan DAHA SIK düşüyor — yani başarısızlıkla ters
-- korelasyonlu. Batarya normal kullanımda azalır; bunu "teknik arıza" saymak kategori
-- uydurmaktır (ALTIN KURAL: sinyalsize kategori atanmaz).
--
-- reason 9 (`Batarya bitti`) NEDEN TUTULDU: istatistik onu da elemiyor (0,5x), ancak
-- iş gerekçesi ölçümü geçersiz kılmıyor, TAMAMLIYOR: batarya bitmesi saha ekibini
-- değişim görevine çıkarır — gerçek bir operasyon doğar, boşa görev değildir. Lift
-- düşük çünkü batarya sürüş SONRASI park hâlinde de sık bitiyor; bu, kodun arıza
-- olmadığını değil, zamanlamasının gürültülü olduğunu gösterir.
--
-- ÖNKOŞUL: db/06_vehicle_status.sql çalıştırılmış olmalı.
-- İDEMPOTENT: tekrar çalıştırılabilir. `verified=true` satırlara ASLA DOKUNMAZ —
-- saha ekibi bir eşlemeyi doğruladıysa o karar mühendis önerisini yener (governance).
-- Çalıştırma: pgAdmin Query Tool, binbin veritabanı seçili, tamamını yapıştır.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1) reason 8 (Batarya az) → SİNYAL DEĞİL
-- ------------------------------------------------------------
UPDATE fleet_status_reason SET
    category_hint   = NULL,
    reason_hint     = NULL,
    is_fault_signal = false,
    priority        = 0,
    notes           = 'SİNYAL DEĞİL. Ölçüm (2026-07-21): başarısızda %9,34 · '
                      'başarılıda %12,35 → lift 0,8x. Başarısızlıkla TERS '
                      'korelasyonlu; batarya normal kullanımda azalır. Arıza '
                      'sayılırsa kategori uydurulmuş olur.'
WHERE reason_id = 8 AND NOT verified;

-- ------------------------------------------------------------
-- 2) Kalan sinyal kodlarına ölçülen lift'i şeffaflık için işle
--    (karar değişmiyor; yalnız gerekçe kayda geçiyor)
-- ------------------------------------------------------------
UPDATE fleet_status_reason SET notes =
    'Ölçüm (2026-07-21): lift 0,5x — istatistiksel ayırt ediciliği YOK. Buna rağmen '
    'İŞ KARARIYLA sinyal tutuldu: batarya bitmesi saha ekibini değişim görevine '
    'çıkarır, yani gerçek bir operasyon doğurur (bir çeşit tamir). Sahte alarm sayılmamalı.'
WHERE reason_id = 9 AND NOT verified;

UPDATE fleet_status_reason SET notes =
    'Fiziksel donanım kopukluğu. Ölçüm (2026-07-21): lift 2,6x — sınırda ama fiziksel '
    'kopukluk saha müdahalesi doğurur; iş kararıyla tutuldu.'
WHERE reason_id = 30 AND NOT verified;

UPDATE fleet_status_reason SET notes =
    'Ölçüm (2026-07-21): başarısızda %1,02 · başarılıda %0,03 → lift 34,2x. '
    'En güçlü ikinci sinyal.'
WHERE reason_id = 33 AND NOT verified;

UPDATE fleet_status_reason SET notes =
    'Şüpheli(12) geçişinde 2. en sık tetikleyici (8.285). Ölçüm (2026-07-21): lift 2,3x '
    '— sınırda ama saha müdahalesi doğurur; iş kararıyla tutuldu.'
WHERE reason_id = 35 AND NOT verified;

UPDATE fleet_status_reason SET notes =
    'Ölçüm (2026-07-21): lift 184,9x — en keskin sinyal, ama hacmi çok küçük (9 sürüş).'
WHERE reason_id = 39 AND NOT verified;

UPDATE fleet_status_reason SET notes =
    'Operatörün elle işaretlediği en kesin sinyal; her zaman Toplanmalı(13) ile eşleşir. '
    'Ölçüm (2026-07-21): başarısızda %0,52 · başarılıda %0,03 → lift 19,3x.'
WHERE reason_id = 46 AND NOT verified;

-- Haziran 2026'da hiçbir sürüş penceresine düşmeyen sinyal kodları (kural kitabında
-- kalır — veri gelirse çalışsın diye; ama fiilen ölü olduğu kayda geçer).
UPDATE fleet_status_reason SET notes = notes ||
    ' Haziran 2026''da hiçbir sürüş penceresine düşmedi (lift hesaplanamadı).'
WHERE reason_id IN (7, 34, 51) AND NOT verified
  AND notes IS NOT NULL
  AND position('hiçbir sürüş penceresine düşmedi' in notes) = 0;

COMMIT;

-- ============================================================
-- DOĞRULAMA
--   SELECT count(*) FROM fleet_status_reason WHERE is_fault_signal;   -- 9 olmalı
--   SELECT reason_id, description, is_fault_signal, priority, verified
--     FROM fleet_status_reason WHERE reason_id IN (8, 9) ORDER BY reason_id;
--   -- 8 → false/0, 9 → true/90
--
-- Bu revizyondan sonra `classify --refresh` ÇALIŞTIRILMALI; aksi halde kalıcı
-- ride.failure_category eski kural kitabına göre kalır.
-- ============================================================
