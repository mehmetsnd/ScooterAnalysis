-- Operasyonel tablolar boş olmalı.

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

-- Şema nesneleri hâlâ bulunmalı.
SELECT
    to_regclass('public.ride') AS ride_table,
    to_regclass('public.feedback') AS feedback_table,
    to_regclass('public.false_fault_assessment') AS false_fault_table,
    to_regclass('public.data_load') AS data_load_table,
    to_regclass('public.fleet_status_event') AS fleet_status_event_table;

-- Partition'lar silinmemiş olmalı.
SELECT parent.relname AS parent_table, child.relname AS partition_name
FROM pg_inherits i
JOIN pg_class parent ON parent.oid = i.inhparent
JOIN pg_class child ON child.oid = i.inhrelid
WHERE parent.relname IN ('ride', 'fleet_status_event')
ORDER BY parent.relname, child.relname;

-- Korunması gereken referans/config verileri.
SELECT 'country' AS object_name, count(*) AS row_count FROM country
UNION ALL
SELECT 'city', count(*) FROM city
UNION ALL
SELECT 'sub_region', count(*) FROM sub_region
UNION ALL
SELECT 'vehicle', count(*) FROM vehicle
UNION ALL
SELECT 'regulation', count(*) FROM regulation
UNION ALL
SELECT 'ops_cost_model', count(*) FROM ops_cost_model
UNION ALL
SELECT 'fleet_status_code', count(*) FROM fleet_status_code
UNION ALL
SELECT 'fleet_status_reason', count(*) FROM fleet_status_reason
ORDER BY object_name;
