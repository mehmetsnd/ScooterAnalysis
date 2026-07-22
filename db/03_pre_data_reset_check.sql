-- Mongo mesafesiyle yeniden yükleme öncesi mevcut durum özeti.
-- Bu betik hiçbir veriyi değiştirmez.

SELECT 'ride' AS object_name, count(*) AS row_count FROM ride
UNION ALL
SELECT 'feedback', count(*) FROM feedback
UNION ALL
SELECT 'false_fault_assessment', count(*) FROM false_fault_assessment
UNION ALL
SELECT 'data_load', count(*) FROM data_load
UNION ALL
SELECT 'stg_rental_raw', count(*) FROM stg_rental_raw
UNION ALL
SELECT 'fleet_status_event', count(*) FROM fleet_status_event
UNION ALL
SELECT 'stg_status_raw', count(*) FROM stg_status_raw
ORDER BY object_name;

SELECT
    count(*) FILTER (WHERE outcome = 'BASARISIZ_HARD') AS source_failed,
    count(*) FILTER (WHERE outcome = 'BASARILI') AS source_success,
    count(*) FILTER (WHERE distance_m IS NULL) AS distance_null,
    count(*) FILTER (
        WHERE 'OUT_OF_CONTENT' = ANY(data_quality_flags)
    ) AS out_of_content
FROM ride;

SELECT count(*) AS partition_count
FROM pg_inherits
WHERE inhparent = 'ride'::regclass;

SELECT
    data_load_id, file_name, status, rows_read, rows_inserted,
    rows_skipped, rows_flagged, period_start, period_end
FROM data_load
ORDER BY data_load_id;
