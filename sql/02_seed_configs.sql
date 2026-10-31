-- 02_seed_configs.sql: Seed initial configuration for Meeyland crawler

INSERT INTO crawler_configs (
    site_id,
    name,
    enabled,
    crawler_type,
    schedule_times,
    interval_seconds,
    config_json
) VALUES (
    'meeyland',
    'Meeyland - Mua bán BĐS',
    true,
    'rest_api',
    '{}',
    300,
    '{
      "request": {
        "url": "https://api5.meeyland.com/v1/articles/search",
        "method": "POST",
        "headers": {
          "Accept": "application/json, text/plain, */*",
          "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
          "Content-Type": "application/json",
          "Origin": "https://meeyland.com",
          "Referer": "https://meeyland.com/mua-ban-nha-dat",
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
          "X-Tenant": "bWVleWxhbmQ=",
          "sec-ch-ua-platform": "\"Windows\"",
          "sec-fetch-dest": "empty",
          "sec-fetch-mode": "cors",
          "sec-fetch-site": "same-site"
        },
        "query_params": {},
        "body_template": {
          "category": "5deb722db4367252525c1d00",
          "sort": 1
        }
      },
      "pagination": {
        "strategy": "page_number",
        "param_location": "body",
        "page_param": "page",
        "limit_param": "limit",
        "start_page": 1,
        "page_size": 20,
        "max_pages_incremental": 10,
        "max_pages_full_sweep": 500,
        "stop_when_empty_path": "data.results"
      },
      "rate_limit": {
        "download_delay_sec": 1.5,
        "concurrent_requests": 2,
        "timeout_sec": 15,
        "retry_times": 3
      },
      "response_mapping": {
        "items_path": "data.results",
        "fields": {
          "external_id": "_id",
          "external_code": "code",
          "title": "title",
          "description": "description",
          "price_vnd": "totalPrice",
          "area_m2": "area",
          "facade_m": "facade",
          "depth_m": "depth",
          "category": "category.value",
          "property_type": "typeOfHouse.value",
          "city_name": "city.name",
          "district_name": "oldDistrict.translation[0].name",
          "ward_name": "ward.name",
          "lng": "ward.location.coordinates[0]",
          "lat": "ward.location.coordinates[1]",
          "advertiser_id": "createdBy._id",
          "advertiser_name": "contact.fullName",
          "advertiser_phone": "contact.phones[0]",
          "advertiser_email": "contact.email",
          "advertiser_kind": "userTypesLabel",
          "advertiser_is_verified": "createdBy.phoneVerified",
          "published_at": "publishedAt",
          "url": "url",
          "thumbnail_url": "thumbnailDisplay.url",
          "media_list": "media"
        },
        "value_mappings": {
          "category": {
            "mua_ban": "sale",
            "cho_thue": "rent",
            "du_an": "project",
            "_default": "sale"
          },
          "property_type": {
            "dat": "land",
            "dat_nen_du_an": "land",
            "nha_rieng": "house",
            "nha_mat_pho": "house",
            "chung_cu": "apartment",
            "can_ho_chung_cu": "apartment",
            "chung_cu_mini": "apartment",
            "biet_thu": "villa",
            "biet_thu_lien_ke": "villa",
            "shophouse": "shophouse",
            "shophouse_nha_pho_thuong_mai": "shophouse",
            "kho_nha_xuong": "commercial",
            "khach_san_nha_nghi": "commercial",
            "toa_nha_van_phong": "commercial",
            "khu_nghi_duong_resort": "commercial",
            "_default": "other"
          },
          "advertiser_kind": {
            "Môi giới": "broker",
            "Chính chủ": "owner",
            "_default": "unknown"
          }
        }
      }
    }'::jsonb
)
ON CONFLICT (site_id) DO UPDATE SET
    name = EXCLUDED.name,
    enabled = EXCLUDED.enabled,
    crawler_type = EXCLUDED.crawler_type,
    schedule_times = EXCLUDED.schedule_times,
    interval_seconds = EXCLUDED.interval_seconds,
    config_json = EXCLUDED.config_json,
    updated_at = NOW();


-- 2. Seed configuration for Nha Tot (Cho Tot Nha) REST API
INSERT INTO crawler_configs (
    site_id,
    name,
    enabled,
    crawler_type,
    schedule_times,
    interval_seconds,
    config_json
) VALUES (
    'nhatot',
    'Nhà Tốt - Mua bán BĐS',
    true,
    'rest_api',
    '{}',
    300,
    '{
      "request": {
        "url": "https://gateway.chotot.com/v1/public/ad-listing",
        "method": "GET",
        "headers": {
          "Accept": "application/json",
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        },
        "query_params": {
          "region_v2": 13000,
          "cg": 1000
        }
      },
      "allowed_domains": ["gateway.chotot.com", "chotot.com", "nhatot.com"],
      "pagination": {
        "strategy": "offset_limit",
        "page_param": "o",
        "limit_param": "limit",
        "start_page": 0,
        "page_size": 20,
        "max_pages_incremental": 10,
        "max_pages_full_sweep": 200,
        "stop_when_empty_path": "ads"
      },
      "rate_limit": {
        "download_delay_sec": 0.5,
        "timeout_sec": 15.0,
        "retry_times": 3
      },
      "response_mapping": {
        "items_path": "ads",
        "fields": {
          "external_id": "list_id",
          "url": "share_url",
          "title": "subject",
          "description": "body",
          "price_vnd": "price",
          "area_m2": "size",
          "facade_m": "width",
          "depth_m": "length",
          "legal_status": "property_legal_document",
          "published_at": "date",
          "lat": "latitude",
          "lng": "longitude",
          "city_name": "region_name",
          "district_name": "area_name",
          "ward_name": "ward_name",
          "advertiser_id": "account_id",
          "advertiser_name": "seller_info.full_name",
          "advertiser_is_verified": "is_shop_verified",
          "thumbnail_url": "image",
          "media_list": "images",
          "category": "type",
          "property_type": "category"
        },
        "value_mappings": {
          "legal_status": {
            "1": "Đã có sổ",
            "2": "Đang chờ sổ",
            "3": "Giấy tờ khác",
            "4": "Hợp đồng mua bán",
            "5": "Đang chờ sổ",
            "6": "Giấy tay / Vi bằng",
            "7": "Giấy tờ hợp lệ khác",
            "_default": "Giấy tờ khác"
          },
          "category": {
            "s": "sale",
            "u": "rent",
            "_default": "sale"
          },
          "property_type": {
            "1010": "apartment",
            "1020": "house",
            "1030": "land",
            "1040": "land",
            "1050": "commercial",
            "_default": "other"
          }
        }
      }
    }'::jsonb
)
ON CONFLICT (site_id) DO UPDATE SET
    name = EXCLUDED.name,
    enabled = EXCLUDED.enabled,
    crawler_type = EXCLUDED.crawler_type,
    schedule_times = EXCLUDED.schedule_times,
    interval_seconds = EXCLUDED.interval_seconds,
    config_json = EXCLUDED.config_json,
    updated_at = NOW();


-- 3. Seed configuration for Mogi HTML Crawler
INSERT INTO crawler_configs (
    site_id,
    name,
    enabled,
    crawler_type,
    schedule_times,
    interval_seconds,
    config_json
) VALUES (
    'mogi',
    'Mogi - Mua bán BĐS',
    true,
    'html',
    '{}',
    300,
    '{
      "request": {
        "url": "https://mogi.vn/mua-nha-dat",
        "method": "GET",
        "headers": {
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        },
        "query_params": {}
      },
      "pagination": {
        "strategy": "page_number",
        "page_param": "cp",
        "start_page": 1,
        "page_size": 20,
        "max_pages_incremental": 10,
        "max_pages_full_sweep": 100
      },
      "rate_limit": {
        "download_delay_sec": 0.5,
        "timeout_sec": 15.0,
        "retry_times": 3
      },
      "response_mapping": {
        "items_path": "ul.props > li:not(.mogi-ad)",
        "fields": {
          "external_id": "a.link-overlay::attr(href)",
          "url": "a.link-overlay::attr(href)",
          "title": "h2.prop-title::text",
          "price_vnd": "div.price::text",
          "area_m2": "ul.prop-attr li:nth-child(1)::text",
          "city_name": "div.prop-addr::text",
          "thumbnail_url": "div.prop-img img::attr(data-src)"
        }
      },
      "detail_page": {
        "enabled": true,
        "url_field": "url",
        "fields": {
          "advertiser_phone": "a[ng-href*=\"tel:\"]::attr(ng-href), a[href^=\"tel:\"]::attr(href)",
          "advertiser_name": "div.agent-widget div.agent-name a::text, div.agent-widget div.agent-name::text, div.agent-name::text",
          "description": "div.info-content-body::text, .prop-info::text",
          "media_list": "div.media-item img::attr(data-src), .property-photos img::attr(data-src), div.photo img::attr(src), .media-gallery img::attr(data-src)",
          "published_at": "//div[contains(@class, ''info-attr'')][span[contains(text(), ''Ngày đăng'')]]/span[2]/text(), span.date::text, .prop-date::text, .date::text",
          "legal_status": "//div[contains(@class, ''info-attr'')][span[contains(text(), ''Pháp lý'')]]/span[2]/text()",
          "breadcrumb": ".breadcrumb *::text, ol.breadcrumb *::text"
        }
      }
    }'::jsonb
)
ON CONFLICT (site_id) DO UPDATE SET
    name = EXCLUDED.name,
    enabled = EXCLUDED.enabled,
    crawler_type = EXCLUDED.crawler_type,
    schedule_times = EXCLUDED.schedule_times,
    interval_seconds = EXCLUDED.interval_seconds,
    config_json = EXCLUDED.config_json,
    updated_at = NOW();


-- 4. Seed configuration for Homedy HTML Crawler
INSERT INTO crawler_configs (
    site_id,
    name,
    enabled,
    crawler_type,
    schedule_times,
    interval_seconds,
    config_json
) VALUES (
    'homedy',
    'Homedy - Mua bán BĐS',
    true,
    'html',
    '{}',
    300,
    '{
      "request": {
        "url": "https://homedy.com/ban-nha-dat",
        "method": "GET",
        "headers": {
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        },
        "query_params": {}
      },
      "pagination": {
        "strategy": "page_number",
        "page_param": "p",
        "start_page": 1,
        "page_size": 20,
        "max_pages_incremental": 10,
        "max_pages_full_sweep": 100
      },
      "rate_limit": {
        "download_delay_sec": 0.5,
        "timeout_sec": 15.0,
        "retry_times": 3
      },
      "response_mapping": {
        "items_path": "div.product-item",
        "fields": {
          "external_id": "h3 a::attr(href)",
          "url": "h3 a::attr(href)",
          "title": "h3 a::text",
          "price_vnd": "span.price::text",
          "area_m2": "span.acreage::text",
          "city_name": "div.address::text, span.address::text, .address *::text",
          "thumbnail_url": "img.lazy::attr(data-src)"
        }
      },
      "detail_page": {
        "enabled": true,
        "url_field": "url",
        "fields": {
          "advertiser_phone": "[data-mobile]::attr(data-mobile), .mobile-number::text",
          "advertiser_name": ".agent-inpage .flex-name::text, .flex-name::text, a.text-name::attr(title), a.text-name::text, .agent-name::text",
          "description": ".description *::text, .product-description *::text, div.product-description::text, .description::text",
          "published_at": "//p[contains(@class, ''lb-code'')][contains(text(), ''Ngày đăng'')]/following-sibling::p[contains(@class, ''code'')][1]/text(), span.date::text, .product-date::text, .created-date::text, .date::text",
          "media_list": ".gallery-item img::attr(data-src), .gallery-item img::attr(src), .swiper-slide img::attr(src)",
          "breadcrumb": ".breadcrumb *::text, ol.breadcrumb *::text"
        }
      }
    }'::jsonb
)
ON CONFLICT (site_id) DO UPDATE SET
    name = EXCLUDED.name,
    enabled = EXCLUDED.enabled,
    crawler_type = EXCLUDED.crawler_type,
    schedule_times = EXCLUDED.schedule_times,
    interval_seconds = EXCLUDED.interval_seconds,
    config_json = EXCLUDED.config_json,
    updated_at = NOW();


-- 5. Seed configuration for Cafeland HTML Crawler
INSERT INTO crawler_configs (
    site_id,
    name,
    enabled,
    crawler_type,
    schedule_times,
    interval_seconds,
    config_json
) VALUES (
    'cafeland',
    'Cafeland - Mua bán BĐS',
    true,
    'html',
    '{}',
    300,
    '{
      "request": {
        "url": "https://nhadat.cafeland.vn/nha-dat-ban/page-{page}/",
        "method": "GET",
        "headers": {
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        },
        "query_params": {}
      },
      "pagination": {
        "strategy": "page_number",
        "page_param": "page",
        "start_page": 1,
        "page_size": 20,
        "max_pages_incremental": 10,
        "max_pages_full_sweep": 100
      },
      "rate_limit": {
        "download_delay_sec": 0.5,
        "timeout_sec": 15.0,
        "retry_times": 3
      },
      "response_mapping": {
        "items_path": "div.row-item",
        "fields": {
          "external_id": "div.reales-title a::attr(href)",
          "url": "div.reales-title a::attr(href)",
          "title": "div.reales-title a::text",
          "price_vnd": "span.reales-price::text",
          "area_m2": "span.reales-area::text",
          "city_name": "div.info-location *::text",
          "thumbnail_url": "div.images-reales img::attr(data-src)",
          "advertiser_name": "a.member-name::text",
          "published_at": ".reals-update-time::attr(data-time), .reals-update-time *::text, span.created-date::text, .date-create::text"
        }
      },
      "detail_page": {
        "enabled": true,
        "url_field": "url",
        "fields": {
          "advertiser_phone": "a#detailTelSpanZalo::attr(href), a[title*=\"Liên hệ\"]::attr(href), a[href*=\"zalo.me/\"]::attr(href)",
          "description": "div.blk-content *::text, .content-detail *::text, div.blk-content::text, .content-detail::text",
          "media_list": "div.photo-list img::attr(src), .gallery img::attr(src)",
          "breadcrumb": ".breadcrumb *::text"
        }
      }
    }'::jsonb
)
ON CONFLICT (site_id) DO UPDATE SET
    name = EXCLUDED.name,
    enabled = EXCLUDED.enabled,
    crawler_type = EXCLUDED.crawler_type,
    schedule_times = EXCLUDED.schedule_times,
    interval_seconds = EXCLUDED.interval_seconds,
    config_json = EXCLUDED.config_json,
    updated_at = NOW();


-- 6. Seed configuration for BDS.com.vn HTML Crawler
INSERT INTO crawler_configs (
    site_id,
    name,
    enabled,
    crawler_type,
    schedule_times,
    interval_seconds,
    config_json
) VALUES (
    'bds_com_vn',
    'BDS.com.vn - Mua bán BĐS',
    true,
    'html',
    '{}',
    300,
    '{
      "request": {
        "url": "https://bds.com.vn/mua-ban-nha-dat-page{page}",
        "method": "GET",
        "headers": {
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        },
        "query_params": {}
      },
      "pagination": {
        "strategy": "page_number",
        "page_param": "page",
        "start_page": 1,
        "page_size": 20,
        "max_pages_incremental": 10,
        "max_pages_full_sweep": 100
      },
      "rate_limit": {
        "download_delay_sec": 0.5,
        "timeout_sec": 15.0,
        "retry_times": 3
      },
      "response_mapping": {
        "items_path": "div.item-nhadat",
        "fields": {
          "external_id": "a.title-item-nhadat::attr(href)",
          "url": "a.title-item-nhadat::attr(href)",
          "title": "a.title-item-nhadat::text",
          "price_vnd": "span.price-item-nhadat:first-of-type::text",
          "area_m2": "span.price-item-nhadat:nth-of-type(2)::text",
          "city_name": "span.label-adress::text",
          "thumbnail_url": "a.image-item-nhadat img::attr(src)",
          "advertiser_phone": "a.phone-member::attr(href), a[href^=\"tel:\"]::attr(href), a.phone-member::text",
          "description": "div.box-description::text",
          "published_at": "span.flr::text, span.label-time::text"
        }
      },
      "detail_page": {
        "enabled": true,
        "url_field": "url",
        "fields": {
          "advertiser_name": "span.member-name::text",
          "description": "div.ct-pr-sum::text, .box-description::text",
          "media_list": "div.image-detail img::attr(src), .gallery img::attr(src)",
          "breadcrumb": ".breadcrumb *::text, ul.breadcrumb *::text"
        }
      }
    }'::jsonb
)
ON CONFLICT (site_id) DO UPDATE SET
    name = EXCLUDED.name,
    enabled = EXCLUDED.enabled,
    crawler_type = EXCLUDED.crawler_type,
    schedule_times = EXCLUDED.schedule_times,
    interval_seconds = EXCLUDED.interval_seconds,
    config_json = EXCLUDED.config_json,
    updated_at = NOW();


-- 7. Seed configuration for Muaban.net HTML/Next.js Crawler
INSERT INTO crawler_configs (
    site_id,
    name,
    enabled,
    crawler_type,
    schedule_times,
    interval_seconds,
    config_json
) VALUES (
    'muaban',
    'Muaban.net - Mua bán BĐS',
    true,
    'html',
    '{}',
    300,
    '{
      "request": {
        "url": "https://muaban.net/bat-dong-san/ban-nha-dat-chung-cu?page={page}",
        "method": "GET",
        "headers": {
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        },
        "query_params": {}
      },
      "pagination": {
        "strategy": "page_number",
        "page_param": "page",
        "start_page": 1,
        "page_size": 20,
        "max_pages_incremental": 10,
        "max_pages_full_sweep": 100
      },
      "rate_limit": {
        "download_delay_sec": 0.5,
        "timeout_sec": 15.0,
        "retry_times": 3
      },
      "response_mapping": {
        "items_path": "json:script#__NEXT_DATA__::text -> props.pageProps.classified.items",
        "fields": {
          "external_id": "id",
          "url": "url",
          "title": "title",
          "price_vnd": "price",
          "area_m2": "attributes[0].value",
          "city_name": "location",
          "thumbnail_url": "covers[0]",
          "media_list": "covers",
          "advertiser_phone": "phone",
          "advertiser_name": "contact_name",
          "description": "body",
          "published_at": "publish_at",
          "lat": "latitude",
          "lng": "longitude"
        }
      },
      "detail_page": {
        "enabled": true,
        "url_field": "url",
        "fields": {
          "description": "json:script#__NEXT_DATA__::text -> props.pageProps.classified.body",
          "advertiser_name": "json:script#__NEXT_DATA__::text -> props.pageProps.classified.contact_name"
        }
      }
    }'::jsonb
)
ON CONFLICT (site_id) DO UPDATE SET
    name = EXCLUDED.name,
    enabled = EXCLUDED.enabled,
    crawler_type = EXCLUDED.crawler_type,
    schedule_times = EXCLUDED.schedule_times,
    interval_seconds = EXCLUDED.interval_seconds,
    config_json = EXCLUDED.config_json,
    updated_at = NOW();
