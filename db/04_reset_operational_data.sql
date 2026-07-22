-- DİKKAT: Bu betik tablo/enum/index/partition SİLMEZ.
-- Yalnız yeniden üretilecek operasyonel verileri temizler.

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '5min';

-- CASCADE bilinçli olarak kullanılmaz. Bilinmeyen yeni bir bağımlılık varsa
-- PostgreSQL işlemi durdursun; beklenmeyen veri sessizce silinmesin.
TRUNCATE TABLE
    false_fault_assessment,
    feedback,
    ride,
    fleet_status_event,
    data_load,
    stg_rental_raw,
    stg_status_raw
RESTART IDENTITY;

COMMIT;
