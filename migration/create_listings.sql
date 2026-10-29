CREATE TABLE listings (
    listing_key TEXT PRIMARY KEY,            
    property_fingerprint TEXT,                   
    source VARCHAR(20) NOT NULL REFERENCES crawler_configs(site_id),
    external_id TEXT,                        
    external_code BIGINT,                     
    advertiser_id VARCHAR(30) REFERENCES advertisers(advertiser_id) ON DELETE SET NULL,
    
    status TEXT NOT NULL DEFAULT 'active',
    missing_count INT NOT NULL DEFAULT 0,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    

    category VARCHAR(20) NOT NULL DEFAULT 'sale',   
    property_type VARCHAR(20) NOT NULL DEFAULT 'land',   
    area_m2 NUMERIC(10, 2),
    facade_m NUMERIC(6, 2),
    depth_m NUMERIC(6, 2),
    

    current_price_vnd NUMERIC(18, 0),            
    current_price_per_m2 NUMERIC(14, 0),
    price_status VARCHAR(20) NOT NULL DEFAULT 'initial',
    price_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
   
    CONSTRAINT chk_listing_status CHECK (status IN ('active', 'expired', 'hidden')),
    CONSTRAINT chk_category CHECK (category IN ('sale', 'rent', 'project')),
    CONSTRAINT chk_property_type CHECK (property_type IN ('land', 'house', 'apartment', 'villa', 'shophouse', 'commercial', 'other')),
    CONSTRAINT chk_price_status CHECK (price_status IN ('initial', 'increased', 'decreased', 'contact')),
    CONSTRAINT chk_missing_count_non_negative CHECK (missing_count >= 0),
    CONSTRAINT chk_positive_dimensions CHECK (
        (area_m2 IS NULL OR area_m2 > 0) AND
        (facade_m IS NULL OR facade_m > 0) AND
        (depth_m IS NULL OR depth_m > 0)
    ),
    CONSTRAINT chk_non_negative_price CHECK (
        (current_price_vnd IS NULL OR current_price_vnd >= 0) AND
        (current_price_per_m2 IS NULL OR current_price_per_m2 >= 0)
    ),
    CONSTRAINT chk_listing_time_order CHECK (last_seen_at >= first_seen_at)
);

CREATE INDEX idx_listings_active_filter 
ON listings(category, property_type, current_price_per_m2) 
WHERE status = 'active';
CREATE INDEX idx_listings_price_changed 
ON listings(price_changed_at DESC, price_status) 
WHERE status = 'active' AND price_status IN ('increased', 'decreased');
CREATE INDEX idx_listings_fingerprint 
ON listings(property_fingerprint) 
WHERE property_fingerprint IS NOT NULL;