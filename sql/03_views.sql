-- 03_views.sql: Gold Layer Analytical Materialized Views

-- 1. View: Average Unit Price (VND/m2) by Ward/Commune
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_ward_avg_price_per_m2 AS
SELECT 
    d.city_name,
    d.district_name,
    COALESCE(d.ward_name, '') AS ward_name,
    l.category,
    l.property_type,
    COUNT(l.listing_key) AS total_active_listings,
    ROUND(AVG(l.current_price_per_m2), 0) AS avg_price_per_m2_vnd,
    ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY l.current_price_per_m2)::NUMERIC, 0) AS median_price_per_m2_vnd,
    ROUND(MIN(l.current_price_per_m2), 0) AS min_price_per_m2_vnd,
    ROUND(MAX(l.current_price_per_m2), 0) AS max_price_per_m2_vnd,
    NOW() AS refreshed_at
FROM listings l
JOIN listing_details d ON l.listing_key = d.listing_key
WHERE l.status = 'active' 
  AND l.current_price_per_m2 BETWEEN 1000000 AND 2000000000
  AND d.city_name IS NOT NULL
GROUP BY 
    d.city_name,
    d.district_name,
    COALESCE(d.ward_name, ''),
    l.category,
    l.property_type;

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_ward_avg_price 
ON mv_ward_avg_price_per_m2(city_name, district_name, ward_name, category, property_type);

-- 2. View: Steep Price Drops in the last 7 days (>= 5% drop)
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_steep_price_drops_7d AS
SELECT 
    h.history_id,
    h.listing_key,
    l.source,
    d.title,
    d.url,
    d.city_name,
    d.district_name,
    d.ward_name,
    l.area_m2,
    h.price_vnd AS new_price_vnd,
    (h.price_vnd - h.price_diff_vnd) AS old_price_vnd,
    h.price_diff_vnd,
    h.price_diff_percent,
    h.recorded_at,
    NOW() AS refreshed_at
FROM listing_price_history h
JOIN listings l ON h.listing_key = l.listing_key
JOIN listing_details d ON h.listing_key = d.listing_key
WHERE h.price_status = 'decreased'
  AND h.price_diff_percent <= -5.0
  AND h.recorded_at >= NOW() - INTERVAL '7 days'
ORDER BY h.price_diff_percent ASC;

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_steep_drops_history ON mv_steep_price_drops_7d(history_id);
CREATE INDEX IF NOT EXISTS idx_mv_steep_drops_listing ON mv_steep_price_drops_7d(listing_key);

