CREATE TABLE listing_price_history (
    history_id BIGSERIAL PRIMARY KEY,
    listing_key TEXT NOT NULL REFERENCES listings(listing_key) ON DELETE CASCADE,
    crawl_run_id TEXT REFERENCES crawl_runs(crawl_run_id) ON DELETE SET NULL,
    
    price_vnd NUMERIC(18, 0) NOT NULL,
    price_per_m2 NUMERIC(14, 0),
    price_diff_vnd NUMERIC(18, 0) NOT NULL DEFAULT 0,     
    price_diff_percent NUMERIC(6, 2) NOT NULL DEFAULT 0,  
    price_status VARCHAR(20) NOT NULL,                           
    
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    

    CONSTRAINT chk_history_price_positive CHECK (price_vnd > 0 AND (price_per_m2 IS NULL OR price_per_m2 > 0)),
    CONSTRAINT chk_history_status CHECK (price_status IN ('initial', 'increased', 'decreased')),
    CONSTRAINT chk_price_diff_logic CHECK (
        (price_status = 'initial' AND price_diff_vnd = 0) OR
        (price_status = 'increased' AND price_diff_vnd > 0) OR
        (price_status = 'decreased' AND price_diff_vnd < 0)
    )
);

CREATE INDEX idx_price_history_listing_timeline ON listing_price_history(listing_key, recorded_at DESC);

CREATE INDEX idx_price_history_recorded_at ON listing_price_history(recorded_at DESC);