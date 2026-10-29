CREATE TABLE crawl_runs (
    crawl_run_id TEXT PRIMARY KEY,
    site_id VARCHAR(20) NOT NULL REFERENCES crawler_configs(site_id) ON DELETE CASCADE,
    run_mode VARCHAR(20) NOT NULL DEFAULT 'incremental',
    status VARCHAR(20) NOT NULL DEFAULT 'running',

    pages_crawled INT NOT NULL DEFAULT 0,        
    items_scraped INT NOT NULL DEFAULT 0,         
    items_processed INT NOT NULL DEFAULT 0,       
    new_listings_count INT NOT NULL DEFAULT 0,    
    price_changes_detected INT NOT NULL DEFAULT 0, 

    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    lifecycle_applied_at TIMESTAMPTZ,
    error_message TEXT,
    CONSTRAINT chk_run_mode CHECK (run_mode IN ('incremental', 'full_sweep')),
    CONSTRAINT chk_status CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
    CONSTRAINT chk_time_order CHECK (finished_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX idx_crawl_runs_site_history ON crawl_runs(site_id, started_at DESC);
CREATE UNIQUE INDEX uq_crawl_runs_one_running_pe_site 
ON crawl_runs(site_id) 
WHERE status = 'running';